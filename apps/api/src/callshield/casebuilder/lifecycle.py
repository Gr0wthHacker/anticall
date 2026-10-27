"""Enforce case lifecycle (19.3): generic transition service with guard context.

Early-state facts (incident/artifact counts) are computed here from the database and
override anything the caller passes, so a client cannot assert them. Guards owned by
other modules (USER_APPROVED, SUBMITTED, ...) live in domain.state_machines and read
the context the *calling service* supplies; the HTTP surface of this module only
exposes the early edges (`USER_TRANSITIONS`).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from callshield.audit import record_case_audit
from callshield.auth.scoping import scoped_get_or_404
from callshield.casebuilder.linking import linked_incident_ids
from callshield.core.clock import utcnow
from callshield.core.errors import ConflictError
from callshield.db.models.enforce import Case, EvidenceArtifact
from callshield.domain.enums import ActorType, EnforceCaseState, EventOrigin
from callshield.domain.state_machines import Machine, evaluate_transition
from callshield.domain.topics import Topic
from callshield.ledger import append_event

E = EnforceCaseState

USER_TRANSITIONS: frozenset[tuple[E, E]] = frozenset(
    {
        (E.OPENED, E.INTAKE),
        (E.INTAKE, E.EVIDENCE_COLLECTING),
        (E.EVIDENCE_COLLECTING, E.REVIEW_REQUIRED),
        (E.REVIEW_REQUIRED, E.EVIDENCE_COLLECTING),
        (E.OPENED, E.CLOSED),
        (E.INTAKE, E.CLOSED),
        (E.EVIDENCE_COLLECTING, E.CLOSED),
        (E.REVIEW_REQUIRED, E.CLOSED),
    }
)

_NEEDS_INCIDENT = {(E.INTAKE, E.EVIDENCE_COLLECTING), (E.EVIDENCE_COLLECTING, E.REVIEW_REQUIRED)}


def case_facts(session: Session, tenant_id: uuid.UUID, case: Case) -> dict[str, Any]:
    """Facts derived from stored data; used as trusted guard context."""
    artifacts = session.execute(
        select(func.count()).where(
            EvidenceArtifact.tenant_id == tenant_id,
            EvidenceArtifact.case_id == case.id,
            EvidenceArtifact.deleted_at.is_(None),
        )
    ).scalar_one()
    return {
        "incident_count": len(linked_incident_ids(session, tenant_id, case.id)),
        "artifact_count": artifacts,
    }


def _early_guard_failures(from_state: E, to_state: E, facts: Mapping[str, Any]) -> list[str]:
    if (from_state, to_state) in _NEEDS_INCIDENT and not facts["incident_count"]:
        return ["at least one linked incident is required"]
    return []


def transition_case(
    session: Session,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    to_state: E,
    actor: str,
    *,
    context: Mapping[str, Any] | None = None,
    expected_review_version: int | None = None,
    user_facing: bool = False,
) -> Case:
    """Validate (table + registered guards + early-state facts) and apply a transition.

    `user_facing=True` restricts to USER_TRANSITIONS and ignores caller context.
    """
    case = scoped_get_or_404(session, Case, case_id, tenant_id)
    from_state = E(case.state)
    if expected_review_version is not None and expected_review_version != case.review_version:
        raise ConflictError("case changed since it was read", code="STALE_REVIEW_VERSION")
    if user_facing and (from_state, to_state) not in USER_TRANSITIONS:
        raise ConflictError(
            f"{from_state} -> {to_state} is not available here", code="INVALID_TRANSITION"
        )
    facts = case_facts(session, tenant_id, case)
    merged = {**({} if user_facing else dict(context or {})), **facts}
    decision = evaluate_transition(Machine.ENFORCE_CASE, from_state, to_state, merged)
    failures = list(decision.reasons) + _early_guard_failures(from_state, to_state, facts)
    if failures:
        raise ConflictError(
            "transition refused", code="INVALID_TRANSITION", details={"reasons": failures}
        )
    _apply(session, tenant_id, case, from_state, to_state, actor)
    return case


def _apply(
    session: Session, tenant_id: uuid.UUID, case: Case, from_state: E, to_state: E, actor: str
) -> None:
    case.state = to_state.value
    case.review_version += 1
    case.updated_at = utcnow()
    payload = {"case_id": str(case.id), "from": from_state.value, "to": to_state.value}
    user = actor.startswith("user:")
    append_event(
        session,
        tenant_id,
        Topic.CASE_STATE_CHANGED,
        payload,
        EventOrigin.USER_ACTION if user else EventOrigin.SYSTEM,
        f"case-state:{case.id}:{case.review_version}",
    )
    record_case_audit(
        session,
        tenant_id,
        case.id,
        ActorType.USER if user else ActorType.SYSTEM,
        "case.state_changed",
        payload,
        actor_id=actor,
    )
