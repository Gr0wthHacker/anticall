"""Append-only incident linkage (ENF-007) and reclassification.

Rows are never edited or deleted. The latest row per (case, incident) is current.
Unlinking appends a *_UNLINK row; the source incident stays untouched.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from callshield.audit import record_case_audit
from callshield.auth.scoping import scoped_get_or_404, scoped_select
from callshield.core.clock import utcnow
from callshield.core.errors import UnprocessableError
from callshield.db.models.defend import Classification, Incident
from callshield.db.models.enforce import Case, CaseIncident
from callshield.db.models.enforce_ext import IncidentReclassification
from callshield.domain.enums import ActorType, ClassificationLabel, EventOrigin, LinkageType
from callshield.domain.topics import Topic
from callshield.evidence.access import actor_type_for
from callshield.ledger import append_event

LINK_TYPES = frozenset(
    {LinkageType.AUTO_MATCH.value, LinkageType.USER_LINK.value, LinkageType.REVIEWER_LINK.value}
)
UNLINK_TYPES = frozenset({LinkageType.USER_UNLINK.value, LinkageType.REVIEWER_UNLINK.value})


def _monotonic(now: datetime, previous: datetime | None) -> datetime:
    """Keep append order stable even when the clock is frozen or coarse."""
    if previous is not None and now <= previous:
        return previous + timedelta(microseconds=1)
    return now


def all_link_rows(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID) -> list[CaseIncident]:
    stmt = scoped_select(CaseIncident, tenant_id).where(CaseIncident.case_id == case_id)
    return list(session.execute(stmt.order_by(CaseIncident.created_at, CaseIncident.id)).scalars())


def current_links(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> dict[uuid.UUID, CaseIncident]:
    """Latest linkage row per incident (link or unlink)."""
    latest: dict[uuid.UUID, CaseIncident] = {}
    for row in all_link_rows(session, tenant_id, case_id):
        latest[row.incident_id] = row
    return latest


def linked_incident_ids(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[uuid.UUID]:
    rows = current_links(session, tenant_id, case_id)
    return [iid for iid, row in rows.items() if row.linkage_type in LINK_TYPES]


def append_link(
    session: Session,
    tenant_id: uuid.UUID,
    case: Case,
    incident: Incident,
    linkage_type: LinkageType,
    evidence_basis: dict[str, object],
    actor: str,
) -> tuple[CaseIncident | None, bool]:
    """Append a linkage row unless the current state already matches. Returns (row, created)."""
    current = current_links(session, tenant_id, case.id).get(incident.id)
    want_link = linkage_type.value in LINK_TYPES
    if current is not None and (current.linkage_type in LINK_TYPES) == want_link:
        return current, False
    if current is None and not want_link:
        return None, False
    row = CaseIncident(
        tenant_id=tenant_id,
        case_id=case.id,
        incident_id=incident.id,
        linkage_type=linkage_type.value,
        evidence_basis=dict(evidence_basis),
        actor=actor,
        created_at=_monotonic(utcnow(), current.created_at if current else None),
    )
    session.add(row)
    session.flush()
    _record_link_event(session, tenant_id, case, row, want_link, actor)
    return row, True


def _record_link_event(
    session: Session, tenant_id: uuid.UUID, case: Case, row: CaseIncident, linked: bool, actor: str
) -> None:
    topic = Topic.INCIDENT_LINKED if linked else Topic.INCIDENT_UNLINKED
    payload = {
        "case_id": str(case.id),
        "incident_id": str(row.incident_id),
        "link_row_id": str(row.id),
        "linkage_type": row.linkage_type,
    }
    origin = EventOrigin.SYSTEM if actor == "system" else EventOrigin.USER_ACTION
    append_event(session, tenant_id, topic, payload, origin, f"incident-link:{row.id}")
    record_case_audit(
        session,
        tenant_id,
        case.id,
        actor_type_for(actor),
        "incident.linked" if linked else "incident.unlinked",
        {**payload, "evidence_basis": row.evidence_basis},
        actor_id=actor,
    )


def set_link(
    session: Session,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    incident_id: uuid.UUID,
    *,
    link: bool,
    reviewer: bool,
    evidence_basis: dict[str, object],
    actor: str,
) -> tuple[CaseIncident | None, bool]:
    """User/reviewer link or unlink. The incident must be on the case's protected line."""
    case = scoped_get_or_404(session, Case, case_id, tenant_id)
    incident = scoped_get_or_404(session, Incident, incident_id, tenant_id)
    if link and incident.line_id != case.protected_line_id:
        raise UnprocessableError(
            "incident belongs to a different protected line", code="LINE_MISMATCH"
        )
    if link:
        kind = LinkageType.REVIEWER_LINK if reviewer else LinkageType.USER_LINK
    else:
        kind = LinkageType.REVIEWER_UNLINK if reviewer else LinkageType.USER_UNLINK
    return append_link(session, tenant_id, case, incident, kind, evidence_basis, actor)


def latest_reclassification(
    session: Session, tenant_id: uuid.UUID, incident_id: uuid.UUID
) -> IncidentReclassification | None:
    stmt = (
        scoped_select(IncidentReclassification, tenant_id)
        .where(IncidentReclassification.incident_id == incident_id)
        .order_by(IncidentReclassification.created_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def effective_label(session: Session, tenant_id: uuid.UUID, incident: Incident) -> str:
    """User reclassification > latest classification of the call > incident.label."""
    reclass = latest_reclassification(session, tenant_id, incident.id)
    if reclass is not None:
        return reclass.new_label
    if incident.call_session_id is not None:
        stmt = (
            scoped_select(Classification, tenant_id)
            .where(Classification.call_id == incident.call_session_id)
            .order_by(Classification.created_at.desc(), Classification.id)
            .limit(1)
        )
        latest = session.execute(stmt).scalars().first()
        if latest is not None:
            return latest.label
    return incident.label


def reclassify_incident(
    session: Session,
    tenant_id: uuid.UUID,
    incident_id: uuid.UUID,
    new_label: str,
    reason: str,
    actor: str,
    case_id: uuid.UUID | None = None,
) -> IncidentReclassification:
    """Append a reclassification; the source incident and classifications are not edited."""
    incident = scoped_get_or_404(session, Incident, incident_id, tenant_id)
    try:
        label = ClassificationLabel(new_label).value
    except ValueError as exc:
        raise UnprocessableError("unknown classification label", code="BAD_LABEL") from exc
    previous = latest_reclassification(session, tenant_id, incident.id)
    row = IncidentReclassification(
        tenant_id=tenant_id,
        incident_id=incident.id,
        case_id=case_id,
        previous_label=effective_label(session, tenant_id, incident),
        new_label=label,
        reason=reason,
        actor=actor,
        created_at=_monotonic(utcnow(), previous.created_at if previous else None),
    )
    session.add(row)
    session.flush()
    if case_id is not None:
        record_case_audit(
            session,
            tenant_id,
            case_id,
            ActorType.USER if actor.startswith("user:") else ActorType.SYSTEM,
            "incident.reclassified",
            {"incident_id": str(incident.id), "from": row.previous_label, "to": label},
            actor_id=actor,
        )
    return row


def incidents_for_case(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[Incident]:
    """Currently linked incidents, ordered by occurrence time then id."""
    ids = linked_incident_ids(session, tenant_id, case_id)
    if not ids:
        return []
    stmt = scoped_select(Incident, tenant_id).where(Incident.id.in_(ids))
    rows = list(session.execute(stmt).scalars())
    return sorted(rows, key=lambda i: (i.occurred_at or i.created_at, str(i.id)))


__all__ = [
    "LINK_TYPES",
    "UNLINK_TYPES",
    "all_link_rows",
    "append_link",
    "current_links",
    "effective_label",
    "incidents_for_case",
    "linked_incident_ids",
    "reclassify_incident",
    "set_link",
]
