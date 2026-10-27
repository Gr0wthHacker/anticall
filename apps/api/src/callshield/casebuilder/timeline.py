"""Case event timeline (ENF-004, 19.4).

Merges ledger events, incidents, calls, opt-out events, contact attempts, artifacts,
link changes and corrections into one stably ordered list. Every entry carries a
source label so observed, claimed, inferred and user-attested facts stay visibly
separate. Each manual attempt is its own entry; there is no cutoff or aggregation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_get_or_404, scoped_select
from callshield.casebuilder.linking import all_link_rows, effective_label, incidents_for_case
from callshield.casebuilder.optout import attempts_for, opt_outs_for, resolution_cases_for
from callshield.core.phone import mask_phone
from callshield.db.models.defend import CallSession, Incident
from callshield.db.models.enforce import Case, EvidenceArtifact
from callshield.db.models.enforce_ext import IncidentReclassification
from callshield.db.models.platform import LedgerEvent
from callshield.domain.enums import EventOrigin, EvidenceLabel, EvidenceType, LinkageType
from callshield.domain.topics import Topic

KIND_ORDER = {
    "INCIDENT": 0,
    "CALL": 1,
    "LEDGER_EVENT": 2,
    "CONTACT_ATTEMPT": 3,
    "OPT_OUT_EVENT": 4,
    "LINK_CHANGE": 5,
    "ARTIFACT": 6,
    "USER_IMPORT": 7,
    "CORRECTION": 8,
}
EXCLUDED_TOPICS = frozenset(
    {
        Topic.EVIDENCE_CREATED.value,
        Topic.EVIDENCE_REDACTED.value,
        Topic.EVIDENCE_DELETED.value,
        Topic.ARTIFACT_EXPIRED.value,
        Topic.INCIDENT_LINKED.value,
        Topic.INCIDENT_UNLINKED.value,
        Topic.CORRECTION_RECORDED.value,
        Topic.LINE_VERIFIED.value,
        "case.alert",
    }
)
_PAYLOAD_ID_KEYS = (
    "case_id",
    "incident_id",
    "call_session_id",
    "session_id",
    "resolution_case_id",
    "attempt_id",
)
_ORIGIN_LABEL = {
    EventOrigin.USER_ACTION.value: EvidenceLabel.USER_ATTESTED,
    EventOrigin.USER_IMPORT.value: EvidenceLabel.USER_ATTESTED,
    EventOrigin.MODEL.value: EvidenceLabel.MODEL_INFERRED,
}


@dataclass(frozen=True)
class TimelineEntry:
    entry_id: str
    kind: str
    occurred_at: datetime
    time_basis: str  # observed | recorded | user_stated | user_stated_date_only
    source_label: EvidenceLabel
    summary: str
    source_ids: dict[str, str] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def sort_key(self) -> tuple[datetime, int, str]:
        return (self.occurred_at, KIND_ORDER.get(self.kind, 99), self.entry_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "kind": self.kind,
            "occurred_at": self.occurred_at.astimezone(UTC).isoformat(),
            "time_basis": self.time_basis,
            "source_label": self.source_label.value,
            "summary": self.summary,
            "source_ids": self.source_ids,
            "details": self.details,
        }


def _incident_entries(
    session: Session, tenant_id: uuid.UUID, incidents: list[Incident]
) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for inc in incidents:
        label = effective_label(session, tenant_id, inc)
        entries.append(
            TimelineEntry(
                f"incident:{inc.id}",
                "INCIDENT",
                inc.occurred_at or inc.created_at,
                "observed" if inc.occurred_at else "recorded",
                EvidenceLabel(inc.evidence_label),
                f"Incident classified {label}; displayed number "
                f"{mask_phone(inc.displayed_caller_id)}"
                " (an observed number, not a verified caller)",
                {"incident_id": str(inc.id)},
                {"effective_label": label, "source_label_field": inc.label},
            )
        )
        entries.extend(_call_entry(session, tenant_id, inc))
    return entries


def _call_entry(session: Session, tenant_id: uuid.UUID, inc: Incident) -> list[TimelineEntry]:
    if inc.call_session_id is None:
        return []
    stmt = scoped_select(CallSession, tenant_id).where(CallSession.id == inc.call_session_id)
    call = session.execute(stmt).scalars().first()
    if call is None:
        return []
    return [
        TimelineEntry(
            f"call:{call.id}",
            "CALL",
            call.started_at or call.created_at,
            "observed" if call.started_at else "recorded",
            EvidenceLabel.OBSERVED,
            f"{call.direction} call, disposition {call.disposition}",
            {"call_session_id": str(call.id), "incident_id": str(inc.id)},
            {"duration_seconds": call.duration_seconds, "provider": call.provider},
        )
    ]


def _resolve_entries(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[TimelineEntry]:
    ids = [r.id for r in resolution_cases_for(session, tenant_id, case_id)]
    entries: list[TimelineEntry] = []
    for att in attempts_for(session, tenant_id, ids):
        observed = att.call_session_id is not None
        entries.append(
            TimelineEntry(
                f"attempt:{att.id}",
                "CONTACT_ATTEMPT",
                att.started_at or att.created_at,
                "observed" if observed and att.started_at else "recorded",
                EvidenceLabel.OBSERVED if observed else EvidenceLabel.USER_ATTESTED,
                f"Contact attempt ({att.mode}), outcome {att.outcome or 'not recorded'}",
                {"attempt_id": str(att.id), "resolution_case_id": str(att.case_id)},
                {"outcome": att.outcome, "review_state": att.review_state},
            )
        )
    for ev in opt_outs_for(session, tenant_id, ids):
        entries.append(
            TimelineEntry(
                f"optout:{ev.id}",
                "OPT_OUT_EVENT",
                ev.acknowledged_at or ev.created_at,
                "recorded",
                EvidenceLabel(ev.evidence_label),
                f"Opt-out request status {ev.status} (an acknowledgment is not suppression)",
                {"opt_out_event_id": str(ev.id)},
                {"status": ev.status, "supersedes_id": str(ev.supersedes_id or "") or None},
            )
        )
    return entries


def _link_entries(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[TimelineEntry]:
    entries = []
    for row in all_link_rows(session, tenant_id, case_id):
        auto = row.linkage_type == LinkageType.AUTO_MATCH.value
        entries.append(
            TimelineEntry(
                f"link:{row.id}",
                "LINK_CHANGE",
                row.created_at,
                "recorded",
                EvidenceLabel.MODEL_INFERRED if auto else EvidenceLabel.USER_ATTESTED,
                f"Incident link recorded: {row.linkage_type}",
                {"incident_id": str(row.incident_id), "case_incident_id": str(row.id)},
                {"linkage_type": row.linkage_type, "evidence_basis": row.evidence_basis},
            )
        )
    return entries


def _import_date(art: EvidenceArtifact) -> tuple[datetime, str]:
    stated = art.provenance.get("stated_event_date")
    if isinstance(stated, str) and stated:
        parsed = date.fromisoformat(stated[:10])
        return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC), "user_stated_date_only"
    return art.collected_at, "recorded"


def _artifact_entries(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[TimelineEntry]:
    stmt = scoped_select(EvidenceArtifact, tenant_id).where(EvidenceArtifact.case_id == case_id)
    entries = []
    for art in session.execute(stmt).scalars():
        is_import = art.source_type == EvidenceType.USER_STATEMENT.value and bool(
            art.provenance.get("imported")
        )
        kind = "USER_IMPORT" if is_import else ("CORRECTION" if art.supersedes_id else "ARTIFACT")
        when, basis = _import_date(art) if is_import else (art.collected_at, "recorded")
        entries.append(
            TimelineEntry(
                f"artifact:{art.id}",
                kind,
                when,
                basis,
                EvidenceLabel(art.evidence_label),
                f"{art.source_type} ({art.origin}, v{art.version})"
                + (" - deleted, audit metadata only" if art.deleted_at else ""),
                {"artifact_id": str(art.id)}
                | ({"supersedes_id": str(art.supersedes_id)} if art.supersedes_id else {}),
                {"sha256": art.sha256, "deleted": art.deleted_at is not None, "origin": art.origin},
            )
        )
    return entries


def _reclass_entries(
    session: Session, tenant_id: uuid.UUID, incident_ids: set[uuid.UUID]
) -> list[TimelineEntry]:
    if not incident_ids:
        return []
    stmt = scoped_select(IncidentReclassification, tenant_id).where(
        IncidentReclassification.incident_id.in_(incident_ids)
    )
    return [
        TimelineEntry(
            f"reclass:{r.id}",
            "CORRECTION",
            r.created_at,
            "recorded",
            EvidenceLabel.USER_ATTESTED,
            f"Incident reclassified {r.previous_label} -> {r.new_label}; source event unchanged",
            {"incident_id": str(r.incident_id), "reclassification_id": str(r.id)},
            {"reason": r.reason},
        )
        for r in session.execute(stmt).scalars()
    ]


def _ledger_entries(
    session: Session, tenant_id: uuid.UUID, related_ids: set[str]
) -> list[TimelineEntry]:
    stmt = scoped_select(LedgerEvent, tenant_id).where(LedgerEvent.topic.not_in(EXCLUDED_TOPICS))
    entries = []
    for ev in session.execute(stmt).scalars():
        if not any(str(ev.payload.get(k)) in related_ids for k in _PAYLOAD_ID_KEYS):
            continue
        label = _ORIGIN_LABEL.get(ev.origin, EvidenceLabel.OBSERVED)
        entries.append(
            TimelineEntry(
                f"ledger:{ev.id}",
                "LEDGER_EVENT",
                ev.created_at,
                "observed",
                label,
                f"Ledger event {ev.topic} via {ev.origin}",
                {"ledger_event_id": str(ev.id), "provider_ref": ev.provider_ref or ""},
                {"topic": ev.topic, "origin": ev.origin},
            )
        )
    return entries


def build_timeline(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[TimelineEntry]:
    """Source-labeled, stably ordered chronology for a case. Tenant-scoped (404 otherwise)."""
    case = scoped_get_or_404(session, Case, case_id, tenant_id)
    incidents = incidents_for_case(session, tenant_id, case.id)
    links = all_link_rows(session, tenant_id, case.id)
    ever_ids = {r.incident_id for r in links}
    resolution_ids = [r.id for r in resolution_cases_for(session, tenant_id, case.id)]
    attempt_ids = [a.id for a in attempts_for(session, tenant_id, resolution_ids)]
    related = {str(case.id)} | {str(i) for i in ever_ids} | {str(i) for i in resolution_ids}
    related |= {str(i) for i in attempt_ids} | {str(i.call_session_id) for i in incidents}
    entries = [
        *_incident_entries(session, tenant_id, incidents),
        *_resolve_entries(session, tenant_id, case.id),
        *_link_entries(session, tenant_id, case.id),
        *_artifact_entries(session, tenant_id, case.id),
        *_reclass_entries(session, tenant_id, ever_ids),
        *_ledger_entries(session, tenant_id, related),
    ]
    return sorted(entries, key=TimelineEntry.sort_key)
