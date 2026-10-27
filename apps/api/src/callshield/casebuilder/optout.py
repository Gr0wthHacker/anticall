"""recompute_optout_timeline (19.12 step 8). Read-only over the Resolve tables.

Acknowledgment is NOT suppression (5.2.1). The pipeline may mark a case
REQUEST_ACKNOWLEDGED but never CONTACT_STOPPED, and calls arriving after an opt-out are
"possible subsequent contact" until attribution is corroborated by a human (ENF-006).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_select
from callshield.casebuilder.linking import incidents_for_case
from callshield.db.models.enforce import Case
from callshield.db.models.resolve import ContactAttempt, OptOutEvent, ResolutionCase
from callshield.domain.enums import CaseResolution, OptOutStatus

_DELIVERED_STATES = {
    OptOutStatus.DELIVERED.value,
    OptOutStatus.ACKNOWLEDGED.value,
    OptOutStatus.REFUSED.value,
    OptOutStatus.UNCONFIRMED.value,
    OptOutStatus.CESSATION_OBSERVED.value,
}


@dataclass
class OptOutTimeline:
    resolution_case_ids: list[uuid.UUID] = field(default_factory=list)
    attempt_ids: list[uuid.UUID] = field(default_factory=list)
    opt_out_event_ids: list[uuid.UUID] = field(default_factory=list)
    current_statuses: dict[str, str] = field(default_factory=dict)
    first_delivered_at: datetime | None = None
    acknowledged: bool = False
    possible_subsequent_incident_ids: list[uuid.UUID] = field(default_factory=list)
    repeat_contact_verified: bool = False


def resolution_cases_for(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[ResolutionCase]:
    stmt = scoped_select(ResolutionCase, tenant_id).where(ResolutionCase.enforce_case_id == case_id)
    return list(session.execute(stmt.order_by(ResolutionCase.created_at)).scalars())


def attempts_for(
    session: Session, tenant_id: uuid.UUID, resolution_ids: list[uuid.UUID]
) -> list[ContactAttempt]:
    if not resolution_ids:
        return []
    stmt = scoped_select(ContactAttempt, tenant_id).where(
        ContactAttempt.case_id.in_(resolution_ids)
    )
    return list(
        session.execute(stmt.order_by(ContactAttempt.created_at, ContactAttempt.id)).scalars()
    )


def opt_outs_for(
    session: Session, tenant_id: uuid.UUID, resolution_ids: list[uuid.UUID]
) -> list[OptOutEvent]:
    if not resolution_ids:
        return []
    stmt = scoped_select(OptOutEvent, tenant_id).where(OptOutEvent.case_id.in_(resolution_ids))
    return list(session.execute(stmt.order_by(OptOutEvent.created_at, OptOutEvent.id)).scalars())


def _current_events(events: list[OptOutEvent]) -> list[OptOutEvent]:
    superseded = {e.supersedes_id for e in events if e.supersedes_id is not None}
    return [e for e in events if e.id not in superseded]


def recompute_optout_timeline(
    session: Session, tenant_id: uuid.UUID, case: Case, update_case: bool = True
) -> OptOutTimeline:
    """Derive the opt-out picture from Resolve rows; update case_resolution conservatively."""
    resolutions = resolution_cases_for(session, tenant_id, case.id)
    ids = [r.id for r in resolutions]
    attempts = attempts_for(session, tenant_id, ids)
    events = opt_outs_for(session, tenant_id, ids)
    current = _current_events(events)
    timeline = OptOutTimeline(
        resolution_case_ids=ids,
        attempt_ids=[a.id for a in attempts],
        opt_out_event_ids=[e.id for e in events],
        current_statuses={str(e.id): e.status for e in current},
    )
    delivered = [e.acknowledged_at or e.created_at for e in events if e.status in _DELIVERED_STATES]
    timeline.first_delivered_at = min(delivered) if delivered else None
    timeline.acknowledged = any(e.status == OptOutStatus.ACKNOWLEDGED.value for e in current)
    if timeline.first_delivered_at is not None:
        timeline.possible_subsequent_incident_ids = [
            i.id
            for i in incidents_for_case(session, tenant_id, case.id)
            if (i.occurred_at or i.created_at) > timeline.first_delivered_at
        ]
    timeline.repeat_contact_verified = case.attribution_status == "CORROBORATED"
    if (
        update_case
        and timeline.acknowledged
        and case.case_resolution == CaseResolution.UNRESOLVED.value
    ):
        case.case_resolution = CaseResolution.REQUEST_ACKNOWLEDGED.value
    return timeline
