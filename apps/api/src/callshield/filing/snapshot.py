"""Evidence snapshot for a case, read straight from the DB (PRD 19.3 / 19.10).

Builds a normalized, tenant-scoped view (latest-wins incident links, live artifacts,
current claims/opt-outs, relevant ledger events) and a SHA-256 digest over it. The
digest detects change only; it does not prove authenticity or admissibility (E-004).
Raw phone numbers are hashed inside the digest and never logged.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_select
from callshield.core.hashing import sha256_hex, sha256_json
from callshield.db.models.defend import Incident, OrganizationClaim, ProtectedLine
from callshield.db.models.enforce import Case, CaseIncident, EvidenceArtifact
from callshield.db.models.platform import LedgerEvent
from callshield.db.models.resolve import OptOutEvent, ResolutionCase
from callshield.domain.enums import IncidentStatus, LinkageType

# Ledger topics that constitute evidence. Anything the filing/assessment modules
# themselves emit (filing.*, assessment.*, case.state_changed, export.*) is excluded
# so producing a draft never changes the snapshot it was built from.
EVIDENCE_TOPIC_PREFIXES: tuple[str, ...] = (
    "call.",
    "incident.",
    "request.",
    "contact.",
    "ivr.",
    "evidence.",
    "classification.",
    "correction.",
    "destination.",
)
_UNLINK_TYPES = frozenset({LinkageType.USER_UNLINK.value, LinkageType.REVIEWER_UNLINK.value})
_USER_LINK_TYPES = frozenset({LinkageType.USER_LINK.value, LinkageType.REVIEWER_LINK.value})
_LEDGER_SCAN_LIMIT = 5000


@dataclass(frozen=True)
class IncidentFact:
    incident_id: uuid.UUID
    call_session_id: uuid.UUID | None
    occurred_at: datetime | None
    displayed_caller_id: str | None
    label: str
    evidence_label: str
    status: str
    linkage_type: str

    @property
    def confirmed(self) -> bool:
        """Confirmed only by a human link; an automatic match stays 'suspected'."""
        return self.linkage_type in _USER_LINK_TYPES


@dataclass(frozen=True)
class ClaimFact:
    claim_id: uuid.UUID
    session_id: uuid.UUID
    claimed_name: str
    verification_status: str


@dataclass(frozen=True)
class OptOutFact:
    opt_out_id: uuid.UUID
    status: str
    evidence_label: str
    acknowledged_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class ArtifactFact:
    artifact_id: uuid.UUID
    source_type: str
    media_type: str
    sha256: str | None
    evidence_label: str
    processing_authorization: str
    sharing_authorization: str
    redaction_state: str
    consent_id: uuid.UUID | None
    version: int
    provenance: dict[str, Any]


@dataclass(frozen=True)
class LineFact:
    line_id: uuid.UUID
    e164: str
    verification_status: str
    owner_user_id: uuid.UUID | None


@dataclass(frozen=True)
class CaseSnapshot:
    case_id: uuid.UUID
    attribution_status: str
    recording_authorization: str
    line: LineFact | None
    incidents: tuple[IncidentFact, ...]
    claims: tuple[ClaimFact, ...]
    opt_outs: tuple[OptOutFact, ...]
    artifacts: tuple[ArtifactFact, ...]
    ledger_event_ids: tuple[uuid.UUID, ...]

    def digest_body(self) -> dict[str, Any]:
        """Canonical, PII-hashed representation that the digest covers."""
        return {
            "case": [str(self.case_id), self.attribution_status, self.recording_authorization],
            "line": _line_body(self.line),
            "incidents": [_incident_body(i) for i in self.incidents],
            "claims": [
                [str(c.claim_id), sha256_hex(c.claimed_name), c.verification_status]
                for c in self.claims
            ],
            "opt_outs": [
                [str(o.opt_out_id), o.status, o.evidence_label, o.acknowledged_at]
                for o in self.opt_outs
            ],
            "artifacts": [_artifact_body(a) for a in self.artifacts],
            "ledger_events": [str(e) for e in self.ledger_event_ids],
        }

    @property
    def digest(self) -> str:
        return sha256_json(self.digest_body())


def _line_body(line: LineFact | None) -> list[Any] | None:
    if line is None:
        return None
    return [str(line.line_id), sha256_hex(line.e164), line.verification_status]


def _incident_body(i: IncidentFact) -> list[Any]:
    caller = sha256_hex(i.displayed_caller_id) if i.displayed_caller_id else None
    return [str(i.incident_id), i.occurred_at, caller, i.label, i.evidence_label, i.linkage_type]


def _artifact_body(a: ArtifactFact) -> list[Any]:
    return [
        str(a.artifact_id),
        a.sha256,
        a.version,
        a.evidence_label,
        a.processing_authorization,
        a.sharing_authorization,
        a.redaction_state,
        str(a.consent_id) if a.consent_id else None,
    ]


def latest_links(rows: list[CaseIncident]) -> dict[uuid.UUID, CaseIncident]:
    """Latest row per incident wins (ENF-007). Ties break on (created_at, id)."""
    latest: dict[uuid.UUID, CaseIncident] = {}
    for row in sorted(rows, key=lambda r: (r.created_at, str(r.id))):
        latest[row.incident_id] = row
    return latest


def _load_incidents(session: Session, case: Case) -> list[IncidentFact]:
    links = session.execute(
        scoped_select(CaseIncident, case.tenant_id).where(CaseIncident.case_id == case.id)
    ).scalars()
    current = {
        iid: row
        for iid, row in latest_links(list(links)).items()
        if row.linkage_type not in _UNLINK_TYPES
    }
    if not current:
        return []
    incidents = session.execute(
        scoped_select(Incident, case.tenant_id).where(Incident.id.in_(list(current)))
    ).scalars()
    facts = [
        IncidentFact(
            incident_id=inc.id,
            call_session_id=inc.call_session_id,
            occurred_at=inc.occurred_at,
            displayed_caller_id=inc.displayed_caller_id,
            label=inc.label,
            evidence_label=inc.evidence_label,
            status=inc.status,
            linkage_type=current[inc.id].linkage_type,
        )
        for inc in incidents
        if inc.status != IncidentStatus.DISMISSED.value
    ]
    return sorted(facts, key=_incident_sort_key)


def _incident_sort_key(f: IncidentFact) -> tuple[bool, str, str]:
    stamp = f.occurred_at.isoformat() if f.occurred_at else ""
    return (f.occurred_at is None, stamp, str(f.incident_id))


def _load_claims(session: Session, case: Case, session_ids: list[uuid.UUID]) -> list[ClaimFact]:
    if not session_ids:
        return []
    rows = list(
        session.execute(
            scoped_select(OrganizationClaim, case.tenant_id).where(
                OrganizationClaim.session_id.in_(session_ids)
            )
        ).scalars()
    )
    superseded = {r.supersedes_id for r in rows if r.supersedes_id}
    return sorted(
        (
            ClaimFact(r.id, r.session_id, r.claimed_name, r.verification_status)
            for r in rows
            if r.id not in superseded
        ),
        key=lambda c: str(c.claim_id),
    )


def _resolution_case_ids(session: Session, case: Case) -> list[uuid.UUID]:
    stmt = scoped_select(ResolutionCase, case.tenant_id).where(
        ResolutionCase.enforce_case_id == case.id
    )
    return [r.id for r in session.execute(stmt).scalars()]


def _load_opt_outs(
    session: Session, case: Case, resolution_ids: list[uuid.UUID]
) -> list[OptOutFact]:
    if not resolution_ids:
        return []
    rows = list(
        session.execute(
            scoped_select(OptOutEvent, case.tenant_id).where(
                OptOutEvent.case_id.in_(resolution_ids)
            )
        ).scalars()
    )
    superseded = {r.supersedes_id for r in rows if r.supersedes_id}
    return sorted(
        (
            OptOutFact(r.id, r.status, r.evidence_label, r.acknowledged_at, r.created_at)
            for r in rows
            if r.id not in superseded
        ),
        key=lambda o: (o.created_at, str(o.opt_out_id)),
    )


def _load_artifacts(session: Session, case: Case) -> list[ArtifactFact]:
    rows = list(
        session.execute(
            scoped_select(EvidenceArtifact, case.tenant_id).where(
                EvidenceArtifact.case_id == case.id, EvidenceArtifact.deleted_at.is_(None)
            )
        ).scalars()
    )
    superseded = {r.supersedes_id for r in rows if r.supersedes_id}
    return sorted(
        (
            ArtifactFact(
                r.id,
                r.source_type,
                r.media_type,
                r.sha256,
                r.evidence_label,
                r.processing_authorization,
                r.sharing_authorization,
                r.redaction_state,
                r.consent_id,
                r.version,
                dict(r.provenance or {}),
            )
            for r in rows
            if r.id not in superseded
        ),
        key=lambda a: str(a.artifact_id),
    )


def _payload_refs(payload: dict[str, Any]) -> set[str]:
    keys = ("case_id", "incident_id", "call_session_id", "session_id", "resolution_case_id")
    return {str(payload[k]) for k in keys if payload.get(k) is not None}


def _load_ledger_ids(session: Session, case: Case, refs: set[str]) -> list[uuid.UUID]:
    rows = session.execute(
        scoped_select(LedgerEvent, case.tenant_id)
        .order_by(LedgerEvent.created_at, LedgerEvent.id)
        .limit(_LEDGER_SCAN_LIMIT)
    ).scalars()
    wanted = refs | {str(case.id)}
    return sorted(
        (
            ev.id
            for ev in rows
            if ev.topic.startswith(EVIDENCE_TOPIC_PREFIXES)
            and _payload_refs(ev.payload or {}) & wanted
        ),
        key=str,
    )


def _load_line(session: Session, case: Case) -> LineFact | None:
    row = session.execute(
        scoped_select(ProtectedLine, case.tenant_id).where(
            ProtectedLine.id == case.protected_line_id
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return LineFact(row.id, row.e164, row.verification_status, row.owner_user_id)


def load_case_snapshot(session: Session, case: Case) -> CaseSnapshot:
    """Read everything the filing/assessment layers may rely on, tenant-scoped."""
    incidents = _load_incidents(session, case)
    session_ids = [i.call_session_id for i in incidents if i.call_session_id]
    resolution_ids = _resolution_case_ids(session, case)
    refs = {str(i.incident_id) for i in incidents}
    refs |= {str(s) for s in session_ids} | {str(r) for r in resolution_ids}
    return CaseSnapshot(
        case_id=case.id,
        attribution_status=case.attribution_status,
        recording_authorization=case.recording_authorization,
        line=_load_line(session, case),
        incidents=tuple(incidents),
        claims=tuple(_load_claims(session, case, session_ids)),
        opt_outs=tuple(_load_opt_outs(session, case, resolution_ids)),
        artifacts=tuple(_load_artifacts(session, case)),
        ledger_event_ids=tuple(_load_ledger_ids(session, case, refs)),
    )
