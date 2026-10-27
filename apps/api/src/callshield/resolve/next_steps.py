"""Reviewable next steps (R-009) and possible-recontact attribution review (R-010).

A recommended CALL is only a suggestion: it carries `requires_new_authorization` and
never creates, reuses or implies an authorization.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from callshield.audit import actor_for_user, record_audit
from callshield.auth.scoping import scoped_get_or_404, scoped_select
from callshield.core.clock import utcnow
from callshield.core.errors import ConflictError
from callshield.db.models.defend import Incident
from callshield.db.models.resolve import ResolutionCase
from callshield.db.models.resolve_ext import RecontactProposal, ResolutionNextStep
from callshield.domain.enums import ContactOutcome as O
from callshield.domain.enums import ResolutionState as R
from callshield.domain.topics import Topic
from callshield.resolve import machine
from callshield.resolve.events import add_timeline, emit

SYSTEM_ACTOR = "system"


class NextStepKind(StrEnum):
    VERIFIED_COMPLIANCE_EMAIL = "VERIFIED_COMPLIANCE_EMAIL"
    CERTIFIED_MAIL = "CERTIFIED_MAIL"
    CARRIER_REPORT = "CARRIER_REPORT"
    SUPERVISED_ADDITIONAL_CALL = "SUPERVISED_ADDITIONAL_CALL"
    REGULATOR_COMPLAINT = "REGULATOR_COMPLAINT"
    REVERIFY_DESTINATION = "REVERIFY_DESTINATION"
    KEEP_MONITORING = "KEEP_MONITORING"


@dataclass(frozen=True)
class NextStep:
    kind: NextStepKind
    rationale: str

    @property
    def involves_call(self) -> bool:
        return self.kind is NextStepKind.SUPERVISED_ADDITIONAL_CALL


K = NextStepKind
_WRITTEN = (
    NextStep(
        K.VERIFIED_COMPLIANCE_EMAIL, "Send the request in writing to a verified compliance address."
    ),
    NextStep(K.CERTIFIED_MAIL, "Mail a written request; it creates a durable record."),
)
_ESCALATE = (
    NextStep(K.CARRIER_REPORT, "Report the unwanted calls to your carrier."),
    NextStep(K.REGULATOR_COMPLAINT, "Prepare a complaint to the relevant regulator."),
)
_RETRY = NextStep(
    K.SUPERVISED_ADDITIONAL_CALL,
    "A supervised additional call may be reviewed; it needs a new authorization "
    "and respects cooldowns.",
)


def recommend_next_steps(outcome: O | str | None) -> list[NextStep]:
    """Pure: why the last contact failed decides what to suggest."""
    o = O(outcome) if outcome else O.UNRESOLVED
    if o is O.ACKNOWLEDGED:
        return [
            NextStep(
                K.KEEP_MONITORING,
                "Acknowledgment is a claim, not proof of suppression; monitor for further calls.",
            )
        ]
    if o is O.WRONG_DESTINATION:
        return [
            NextStep(
                K.REVERIFY_DESTINATION,
                "The destination appears wrong; re-verify it before any contact.",
            )
        ]
    if o is O.REFUSED:
        return [*_ESCALATE, *_WRITTEN]
    if o in (O.REFERRED_TO_CHANNEL, O.UNRESOLVED, O.REQUEST_DELIVERED):
        return [*_WRITTEN, _RETRY, *_ESCALATE]
    if o is O.USER_ABORTED:
        return [*_WRITTEN, *_ESCALATE]
    return [_RETRY, *_WRITTEN, *_ESCALATE]  # NO_ANSWER, IVR_FAILED, DROPPED, ...


NON_CALLING_ALTERNATIVES = (
    K.VERIFIED_COMPLIANCE_EMAIL,
    K.CERTIFIED_MAIL,
    K.CARRIER_REPORT,
    K.REGULATOR_COMPLAINT,
)


def persist_next_steps(
    session: Session,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    attempt_id: uuid.UUID | None,
    steps: list[NextStep],
) -> list[ResolutionNextStep]:
    rows: list[ResolutionNextStep] = []
    for step in steps:
        row = ResolutionNextStep(
            tenant_id=tenant_id,
            case_id=case_id,
            attempt_id=attempt_id,
            kind=step.kind.value,
            rationale=step.rationale,
            involves_call=step.involves_call,
            requires_new_authorization=step.involves_call,
        )
        session.add(row)
        rows.append(row)
    return rows


# ------------------------------------------------------------------ recontact (R-010)


def propose_recontact(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID, incident_id: uuid.UUID
) -> RecontactProposal:
    """Propose a later inbound incident for attribution review. NEVER auto-reopens.

    Idempotent per (case, incident). A MONITORING case moves to ATTRIBUTION_REVIEW; in other
    non-closed states only the proposal is recorded.
    """
    case = scoped_get_or_404(session, ResolutionCase, case_id, tenant_id)
    scoped_get_or_404(session, Incident, incident_id, tenant_id)
    existing = session.execute(
        scoped_select(RecontactProposal, tenant_id).where(
            RecontactProposal.case_id == case_id, RecontactProposal.incident_id == incident_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    if machine.current_state(case) is R.CLOSED:
        raise ConflictError("case is closed", code="CASE_CLOSED")
    proposal = RecontactProposal(tenant_id=tenant_id, case_id=case_id, incident_id=incident_id)
    session.add(proposal)
    session.flush()
    if machine.current_state(case) is R.MONITORING:
        machine.transition(session, case, R.POSSIBLE_RECONTACT, actor=SYSTEM_ACTOR)
        machine.transition(session, case, R.ATTRIBUTION_REVIEW, actor=SYSTEM_ACTOR)
    add_timeline(
        session,
        tenant_id,
        case_id,
        "recontact.proposed",
        SYSTEM_ACTOR,
        {"incident_id": str(incident_id), "proposal_id": str(proposal.id)},
    )
    return proposal


def decide_recontact(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case: ResolutionCase,
    proposal_id: uuid.UUID,
    *,
    related: bool,
) -> RecontactProposal:
    """User attribution decision: REOPENED (related) or NOT_RELATED."""
    proposal = scoped_get_or_404(session, RecontactProposal, proposal_id, tenant_id)
    if proposal.case_id != case.id:
        raise ConflictError("proposal belongs to another case", code="PROPOSAL_MISMATCH")
    if proposal.status != "PROPOSED":
        raise ConflictError("proposal already decided", code="ALREADY_DECIDED")
    actor = actor_for_user(user_id)
    if machine.current_state(case) is R.ATTRIBUTION_REVIEW:
        target = R.REOPENED if related else R.NOT_RELATED
        machine.transition(session, case, target, actor=actor)
    proposal.status = "RELATED" if related else "NOT_RELATED"
    proposal.decided_by_user_id = user_id
    proposal.decided_at = utcnow()
    payload = {
        "case_id": str(case.id),
        "proposal_id": str(proposal.id),
        "related": related,
        "incident_id": str(proposal.incident_id),
    }
    add_timeline(session, tenant_id, case.id, "recontact.decided", actor, payload)
    if related:
        emit(session, tenant_id, Topic.CASE_REOPENED, payload, ("case.reopened", proposal.id))
    record_audit(session, tenant_id, actor, "resolve.recontact.decided", payload)
    return proposal
