"""create_or_match_candidate_incident / attach_if_confident (19.12 steps 6-7).

A displayed number is spoofable (ENF-006): it groups incidents for convenience but is
never treated as the operator's identity. Conflicting claims always go to review.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_select
from callshield.casebuilder.config import CaseBuilderConfig
from callshield.casebuilder.linking import incidents_for_case
from callshield.core.phone import mask_phone
from callshield.db.models.defend import Incident, OrganizationClaim
from callshield.db.models.enforce import Case
from callshield.domain.enums import EnforceCaseState, StatementType

_SUFFIXES = re.compile(r"\b(incorporated|inc|llc|ltd|corp|corporation|co|company)\b\.?")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def normalize_entity(name: str | None) -> str:
    """Case/punctuation/corporate-suffix insensitive form of a claimed entity name."""
    if not name:
        return ""
    lowered = _NON_ALNUM.sub(" ", _SUFFIXES.sub(" ", name.lower()))
    return " ".join(lowered.split())


def claimed_entity_for(
    session: Session, tenant_id: uuid.UUID, call_session_id: uuid.UUID | None
) -> str:
    """Normalized latest SELLER_NAME claim for the call (CLAIMED, never verified)."""
    if call_session_id is None:
        return ""
    stmt = scoped_select(OrganizationClaim, tenant_id).where(
        OrganizationClaim.session_id == call_session_id
    )
    claims = list(session.execute(stmt.order_by(OrganizationClaim.created_at.desc())).scalars())
    seller = [c for c in claims if c.statement_type == StatementType.SELLER_NAME.value]
    ordered = seller or claims
    return normalize_entity(ordered[0].claimed_name) if ordered else ""


@dataclass
class MatchResult:
    kind: str  # CONFIDENT | REVIEW | NONE
    case_id: uuid.UUID | None = None
    candidate_case_ids: list[uuid.UUID] = field(default_factory=list)
    reason: str = ""
    basis: dict[str, Any] = field(default_factory=dict)


def _case_signature(
    session: Session, tenant_id: uuid.UUID, case: Case
) -> tuple[set[str], set[str]]:
    """(displayed numbers, normalized claimed entities) among the case's linked incidents."""
    clis: set[str] = set()
    entities: set[str] = set()
    for incident in incidents_for_case(session, tenant_id, case.id):
        if incident.displayed_caller_id:
            clis.add(incident.displayed_caller_id)
        entity = claimed_entity_for(session, tenant_id, incident.call_session_id)
        if entity:
            entities.add(entity)
    return clis, entities


def _compare(
    incident: Incident, entity: str, signature: tuple[set[str], set[str]]
) -> tuple[str, str]:
    """Return (verdict, reason) for one open case. verdict: COMPATIBLE|CONFLICT|CLAIM_ONLY|NONE."""
    clis, entities = signature
    cli_equal = bool(incident.displayed_caller_id) and incident.displayed_caller_id in clis
    entity_equal = bool(entity) and entity in entities
    conflict = bool(entity) and bool(entities) and entity not in entities
    if cli_equal and conflict:
        return "CONFLICT", "same_cli_different_claim"
    if cli_equal:
        return "COMPATIBLE", "same_displayed_cli_compatible_claims"
    if entity_equal:
        return "CLAIM_ONLY", "claim_only_match_different_number"
    return "NONE", ""


def match_incident(
    session: Session,
    tenant_id: uuid.UUID,
    incident: Incident,
    entity: str,
    config: CaseBuilderConfig,
) -> MatchResult:
    """Find the case this incident belongs to, if confident; otherwise candidates for review."""
    stmt = scoped_select(Case, tenant_id).where(
        Case.protected_line_id == incident.line_id, Case.state != EnforceCaseState.CLOSED.value
    )
    compatible: list[Case] = []
    uncertain: list[tuple[Case, str]] = []
    for case in session.execute(stmt.order_by(Case.opened_at, Case.id)).scalars():
        verdict, reason = _compare(incident, entity, _case_signature(session, tenant_id, case))
        if verdict == "COMPATIBLE":
            compatible.append(case)
        elif verdict in ("CONFLICT", "CLAIM_ONLY"):
            uncertain.append((case, reason))
    basis = {
        "match_ruleset_version": config.match_ruleset_version,
        "displayed_cli_masked": mask_phone(incident.displayed_caller_id),
        "claimed_entity": entity or None,
        "same_displayed_number_is_not_identity": True,
    }
    if len(compatible) == 1 and not uncertain:
        return MatchResult(
            "CONFIDENT", compatible[0].id, [], "same_displayed_cli_compatible_claims", basis
        )
    candidates = [c.id for c in compatible] + [c.id for c, _ in uncertain]
    if candidates:
        reason = "ambiguous_multiple_cases" if len(compatible) > 1 else uncertain[0][1]
        return MatchResult("REVIEW", None, candidates, reason, basis)
    return MatchResult("NONE", None, [], "no_candidate_case", basis)
