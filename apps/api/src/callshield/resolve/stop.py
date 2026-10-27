"""User stop / cancel (R-013): end activity, mark the case, offer non-calling alternatives."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from callshield.agent.controller import StopCondition
from callshield.audit import actor_for_user, record_audit
from callshield.auth.scoping import scoped_select
from callshield.core.clock import utcnow
from callshield.db.models.resolve import ContactAttempt, ResolutionCase
from callshield.db.models.resolve_ext import ContactAttemptDetail
from callshield.domain.enums import ContactOutcome, ResolutionState
from callshield.domain.state_machines import RESOLUTION_TRANSITIONS
from callshield.domain.topics import Topic
from callshield.resolve import attempts, authorizations, machine
from callshield.resolve.events import add_timeline, emit
from callshield.resolve.next_steps import (
    NON_CALLING_ALTERNATIVES,
    NextStep,
    NextStepKind,
    persist_next_steps,
)

R = ResolutionState
_ACTIVE_TO_INTERRUPTED = frozenset({R.IN_PROGRESS, R.REQUEST_DELIVERED})


@dataclass(frozen=True)
class StopResult:
    state: str
    aborted_attempts: int
    revoked_authorizations: int
    alternatives: tuple[NextStepKind, ...]


def stop_case(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case: ResolutionCase,
    *,
    reason: str = "",
    close: bool = False,
) -> StopResult:
    actor = actor_for_user(user_id)
    revoked = authorizations.revoke_open_authorizations(session, tenant_id, case.id)
    aborted = _abort_active_attempts(session, tenant_id, case, actor)
    state = machine.current_state(case)
    if state in _ACTIVE_TO_INTERRUPTED:
        machine.transition(session, case, R.INTERRUPTED, actor=actor, note="user stop")
    elif state is R.USER_APPROVED_CONTACT:
        machine.transition(session, case, R.VERIFIED_DESTINATION, actor=actor, note="user stop")
    if close and R.CLOSED in RESOLUTION_TRANSITIONS[machine.current_state(case)]:
        machine.transition(session, case, R.CLOSED, actor=actor, note="closed by user")
    payload = {
        "case_id": str(case.id),
        "reason": reason,
        "aborted_attempts": aborted,
        "revoked_authorizations": revoked,
        "stop_condition": StopCondition.USER_CANCEL.value,
    }
    add_timeline(session, tenant_id, case.id, "contact.stopped", actor, payload)
    emit(
        session,
        tenant_id,
        Topic.CONTACT_ABORTED,
        payload,
        ("contact.aborted", "stop", case.id, uuid.uuid4().hex[:12]),
    )
    record_audit(session, tenant_id, actor, "resolve.contact.stopped", payload)
    alternatives = NON_CALLING_ALTERNATIVES
    persist_next_steps(
        session,
        tenant_id,
        case.id,
        None,
        [
            NextStep(k, "Non-calling alternative offered after the contact was stopped.")
            for k in alternatives
        ],
    )
    return StopResult(case.status, aborted, revoked, alternatives)


def _abort_active_attempts(
    session: Session, tenant_id: uuid.UUID, case: ResolutionCase, actor: str
) -> int:
    stmt = scoped_select(ContactAttempt, tenant_id).where(
        ContactAttempt.case_id == case.id, ContactAttempt.ended_at.is_(None)
    )
    count = 0
    for attempt in session.execute(stmt).scalars():
        detail = session.execute(
            scoped_select(ContactAttemptDetail, tenant_id).where(
                ContactAttemptDetail.attempt_id == attempt.id
            )
        ).scalar_one_or_none()
        if detail is None or detail.source != attempts.SOURCE_SYSTEM:
            continue
        attempt.ended_at = utcnow()
        attempt.outcome = ContactOutcome.USER_ABORTED.value
        detail.stop_condition = StopCondition.USER_CANCEL.value
        emit(
            session,
            tenant_id,
            Topic.CONTACT_ENDED,
            {"attempt_id": str(attempt.id), "outcome": attempt.outcome},
            ("contact.ended", attempt.id),
        )
        add_timeline(
            session,
            tenant_id,
            case.id,
            "contact.ended",
            actor,
            {"outcome": attempt.outcome, "stop_condition": "USER_CANCEL"},
            attempt.id,
        )
        count += 1
    return count
