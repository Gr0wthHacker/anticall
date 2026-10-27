"""Evidence packet builder (PRD 19.5, ENF-009).

Produces the zip bytes plus the manifest. PDF rendering is a separately configurable
component per the PRD: this module emits `case_summary.html` and `case_summary.txt`
only. Hashes in the manifest detect modification; they do not prove authenticity,
chain of custody or admissibility, and the packet makes no such claim.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_get_or_404, scoped_select
from callshield.casebuilder.linking import incidents_for_case
from callshield.casebuilder.matching import claimed_entity_for
from callshield.casebuilder.optout import attempts_for, opt_outs_for, resolution_cases_for
from callshield.casebuilder.timeline import TimelineEntry, build_timeline
from callshield.core.hashing import sha256_hex, sha256_json
from callshield.core.phone import mask_phone
from callshield.core.redaction import redact_structure
from callshield.db.models.defend import CallSession, Incident
from callshield.db.models.enforce import (
    Case,
    CaseAssessment,
    CaseAuditEvent,
    EvidenceArtifact,
    FilingDraft,
    SubmissionApproval,
)
from callshield.db.models.enforce_ext import CaseDossierSnapshot
from callshield.domain.enums import EvidenceType, RedactionState
from callshield.evidence.access import sharing_blocker
from callshield.evidence.integrity import verify_artifact
from callshield.evidence.store import ArtifactStore

SCHEMA_VERSION = "1"
GENERATOR_VERSION = "callshield-casebuilder/1.0"
HASH_ALGORITHM = "sha256"
NO_CLAIMS = (
    "This packet makes no claim of notarization, forensic certification, admissibility, or "
    "verified identity of any caller. SHA-256 digests only let a reader detect later "
    "modification of the listed files."
)
_EXT = {
    "text/plain": ".txt",
    "application/json": ".json",
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
}


@dataclass
class ExportOptions:
    export_version: int
    artifact_ids: list[uuid.UUID] = field(default_factory=list)
    mask_numbers: bool = False
    now: datetime | None = None


@dataclass
class BundleResult:
    zip_bytes: bytes
    manifest: dict[str, Any]
    export_digest: str


def tenant_pseudonym(tenant_id: uuid.UUID) -> str:
    return sha256_hex(f"callshield-tenant-pseudonym:v1:{tenant_id}")[:24]


FORMULA_PREFIXES = ("=", "+", "-", "@", chr(9), chr(13))
_NUMERIC = re.compile(r"^[+-]?\d[\d\-\s().:]*$")


def csv_safe(value: Any) -> str:
    """Neutralize spreadsheet formula injection; plain numbers (phones) stay intact."""
    text = "" if value is None else str(value)
    if text[:1] in FORMULA_PREFIXES and not _NUMERIC.match(text):
        return "'" + text
    return text


def _csv_bytes(headers: list[str], rows: list[list[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([csv_safe(cell) for cell in row])
    return buffer.getvalue().encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")


def chronology_csv(entries: list[TimelineEntry]) -> bytes:
    rows = [
        [
            e.entry_id,
            e.kind,
            e.occurred_at.isoformat(),
            e.time_basis,
            e.source_label.value,
            e.summary,
            json.dumps(e.source_ids, sort_keys=True),
        ]
        for e in entries
    ]
    headers = [
        "entry_id",
        "kind",
        "occurred_at_utc",
        "time_basis",
        "source_label",
        "summary",
        "ids",
    ]
    return _csv_bytes(headers, rows)


def _num(value: str | None, mask: bool) -> str:
    return mask_phone(value) if mask else (value or "")


def calls_csv(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID, mask: bool) -> bytes:
    incidents = incidents_for_case(session, tenant_id, case_id)
    by_call = {i.call_session_id: i for i in incidents if i.call_session_id}
    resolution_ids = [r.id for r in resolution_cases_for(session, tenant_id, case_id)]
    attempt_calls = {
        a.call_session_id
        for a in attempts_for(session, tenant_id, resolution_ids)
        if a.call_session_id
    }
    ids = set(by_call) | attempt_calls
    calls = (
        list(
            session.execute(
                scoped_select(CallSession, tenant_id).where(CallSession.id.in_(ids))
            ).scalars()
        )
        if ids
        else []
    )
    rows: list[list[Any]] = []
    for call in sorted(calls, key=lambda c: (c.started_at or c.created_at, str(c.id))):
        inc: Incident | None = by_call.get(call.id)
        rows.append(
            [
                call.id,
                inc.id if inc else "",
                call.direction,
                call.started_at.isoformat() if call.started_at else "",
                call.ended_at.isoformat() if call.ended_at else "",
                call.duration_seconds,
                call.disposition,
                call.provider,
                _num(inc.displayed_caller_id, mask) if inc else "",
                "OBSERVED (displayed number is not a verified calling party)",
            ]
        )
    headers = [
        "call_session_id", "incident_id", "direction", "started_at_utc", "ended_at_utc",
        "duration_seconds", "disposition", "provider", "displayed_caller_id", "source_label",
    ]  # fmt: skip
    return _csv_bytes(headers, rows)


def opt_out_csv(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID) -> bytes:
    ids = [r.id for r in resolution_cases_for(session, tenant_id, case_id)]
    rows: list[list[Any]] = []
    for att in attempts_for(session, tenant_id, ids):
        rows.append(
            ["ATTEMPT", att.id, att.case_id, att.id, att.outcome or "", att.mode, "",
             (att.started_at or att.created_at).isoformat(), ""]
        )  # fmt: skip
    for ev in opt_outs_for(session, tenant_id, ids):
        when = (ev.acknowledged_at or ev.created_at).isoformat()
        note = "acknowledgment is not suppression"
        rows.append(
            ["OPT_OUT_EVENT", ev.id, ev.case_id, ev.attempt_id or "", ev.status, "",
             ev.evidence_label, when, note]
        )  # fmt: skip
    headers = ["row_type", "id", "resolution_case_id", "attempt_id", "status_or_outcome", "mode",
               "evidence_label", "occurred_at_utc", "note"]  # fmt: skip
    return _csv_bytes(headers, rows)


def operator_identity(
    session: Session, tenant_id: uuid.UUID, case: Case, mask: bool
) -> dict[str, Any]:
    incidents = incidents_for_case(session, tenant_id, case.id)
    claims = sorted(
        {c for i in incidents if (c := claimed_entity_for(session, tenant_id, i.call_session_id))}
    )
    numbers = sorted(
        {_num(i.displayed_caller_id, mask) for i in incidents if i.displayed_caller_id}
    )
    return {
        "attribution_status": case.attribution_status,
        "verified_operator_identity": None,
        "claimed_entities": [{"name": c, "label": "CLAIMED", "verified": False} for c in claims],
        "observed_numbers": [
            {
                "number": n,
                "label": "OBSERVED",
                "note": "observed number, not a verified calling party",
            }
            for n in numbers
        ],
        "note": "A displayed number can be spoofed and does not establish who placed a call.",
    }


def issue_spotting(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID) -> dict[str, Any]:
    rows = list(
        session.execute(
            scoped_select(CaseAssessment, tenant_id)
            .where(CaseAssessment.case_id == case_id)
            .order_by(CaseAssessment.evaluated_at, CaseAssessment.id)
        ).scalars()
    )
    note = "Issue spotting only. Not a legal determination or legal advice."
    if not rows:
        return {"assessments": [], "note": note + " No assessment has been run for this case."}
    return {
        "assessments": [
            {
                "ruleset_version": r.ruleset_version,
                "model_version": r.model_version,
                "issue_type": r.issue_type,
                "status": r.status,
                "supporting_event_ids": r.supporting_event_ids,
                "missing_requirements": r.missing_requirements,
                "evaluated_at": r.evaluated_at.isoformat(),
            }
            for r in rows
        ],
        "note": note,
    }


def user_attestation(
    session: Session, store: ArtifactStore, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> dict[str, Any] | None:
    stmt = scoped_select(EvidenceArtifact, tenant_id).where(
        EvidenceArtifact.case_id == case_id,
        EvidenceArtifact.source_type == EvidenceType.USER_STATEMENT.value,
        EvidenceArtifact.deleted_at.is_(None),
    )
    statements = []
    for art in session.execute(stmt.order_by(EvidenceArtifact.collected_at)).scalars():
        if art.storage_key is None or sharing_blocker(art) is not None:
            continue
        statements.append(
            {
                "artifact_id": str(art.id),
                "label": art.evidence_label,
                "recorded_at": art.collected_at.isoformat(),
                "stated_event_date": art.provenance.get("stated_event_date"),
                "content": json.loads(store.get(art.storage_key).decode("utf-8")),
            }
        )
    approvals = session.execute(
        scoped_select(SubmissionApproval, tenant_id)
        .join(FilingDraft, FilingDraft.id == SubmissionApproval.draft_id)
        .where(FilingDraft.case_id == case_id)
    ).scalars()
    approval_rows = [
        {
            "approval_id": str(a.id),
            "draft_id": str(a.draft_id),
            "approved_payload_digest": a.approved_payload_digest,
            "attestation_version": a.attestation_version,
            "authorized_at": a.authorized_at.isoformat(),
        }
        for a in approvals
    ]
    if not statements and not approval_rows:
        return None
    return {"statements": statements, "filing_approvals": approval_rows, "label": "USER_ATTESTED"}


def audit_extract(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID) -> dict[str, Any]:
    rows = session.execute(
        scoped_select(CaseAuditEvent, tenant_id)
        .where(CaseAuditEvent.case_id == case_id)
        .order_by(CaseAuditEvent.event_time, CaseAuditEvent.id)
    ).scalars()
    return {
        "events": [
            redact_structure(
                {
                    "id": str(e.id),
                    "actor_type": e.actor_type,
                    "action": e.action,
                    "event_time": e.event_time.isoformat(),
                    "event_data": e.event_data,
                }
            )
            for e in rows
        ]
    }


def _attachment_path(art: EvidenceArtifact) -> str:
    return f"attachments/{art.id}{_EXT.get(art.media_type, '.bin')}"


def select_attachments(
    session: Session,
    store: ArtifactStore,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    selected: list[uuid.UUID],
) -> tuple[list[tuple[EvidenceArtifact, bytes]], list[dict[str, str]]]:
    """Only selected, authorized, intact, undeleted artifacts of THIS case are attached."""
    stmt = scoped_select(EvidenceArtifact, tenant_id).where(EvidenceArtifact.case_id == case_id)
    arts = list(
        session.execute(stmt.order_by(EvidenceArtifact.collected_at, EvidenceArtifact.id)).scalars()
    )
    wanted = set(selected)
    included: list[tuple[EvidenceArtifact, bytes]] = []
    excluded: list[dict[str, str]] = []
    for art in arts:
        reason = _exclusion_reason(store, art, art.id in wanted)
        if reason is None:
            assert art.storage_key is not None
            included.append((art, store.get(art.storage_key)))
        else:
            excluded.append(
                {
                    "artifact_id": str(art.id),
                    "source_type": art.source_type,
                    "origin": art.origin,
                    "reason": reason,
                }
            )
    known = {a.id for a in arts}
    excluded.extend(
        {
            "artifact_id": str(i),
            "source_type": "UNKNOWN",
            "origin": "UNKNOWN",
            "reason": "NOT_FOUND_IN_CASE",
        }
        for i in sorted(wanted - known, key=str)
    )
    return included, excluded


def _exclusion_reason(store: ArtifactStore, art: EvidenceArtifact, selected: bool) -> str | None:
    if not selected:
        return "NOT_SELECTED"
    blocker = sharing_blocker(art)
    if blocker is not None:
        return blocker
    return None if verify_artifact(store, art).ok else "INTEGRITY_CHECK_FAILED"


def redactions_doc(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID, included_ids: set[uuid.UUID]
) -> dict[str, Any]:
    stmt = scoped_select(EvidenceArtifact, tenant_id).where(
        EvidenceArtifact.case_id == case_id,
        EvidenceArtifact.redaction_state == RedactionState.REDACTED_DERIVATIVE.value,
    )
    items = []
    for art in session.execute(stmt.order_by(EvidenceArtifact.created_at)).scalars():
        original = art.provenance.get("derived_from", {})
        items.append(
            {
                "derived_artifact_id": str(art.id),
                "original_artifact_id": str(art.derived_from_id) if art.derived_from_id else None,
                "original_sha256": original.get("sha256"),
                "derived_sha256": art.sha256,
                "redaction": art.provenance.get("redaction"),
                "derived_included_in_export": art.id in included_ids,
            }
        )
    return {"redactions": items, "note": "Terms are recorded as digests and counts only."}


def _summary_lines(case: Case, meta: dict[str, Any], entries: list[TimelineEntry]) -> list[str]:
    return [
        f"Case: {case.title}",
        f"State: {case.state}",
        f"Attribution: {case.attribution_status} (a displayed number is not an identity)",
        f"Recording authorization: {case.recording_authorization}",
        f"Legal assessment: {case.legal_assessment}",
        f"Resolution: {case.case_resolution}",
        f"Linked incidents: {meta.get('linked_incident_count', 0)}",
        f"Contact attempts: {meta.get('attempt_count', 0)}",
        f"Opt-out events: {meta.get('opt_out_event_count', 0)}",
        f"Chronology entries: {len(entries)}",
        "",
        "This summary describes recorded events and their labels. It is not a legal "
        "determination, and any suspected issue still requires review.",
        NO_CLAIMS,
    ]


def summary_text(case: Case, meta: dict[str, Any], entries: list[TimelineEntry]) -> bytes:
    lines = _summary_lines(case, meta, entries)
    lines += ["", "Chronology:"] + [
        f"- {e.occurred_at.isoformat()} [{e.source_label.value}] {e.summary}" for e in entries
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def summary_html(case: Case, meta: dict[str, Any], entries: list[TimelineEntry]) -> bytes:
    head = "".join(f"<p>{html.escape(line)}</p>" for line in _summary_lines(case, meta, entries))
    rows = "".join(
        f"<tr><td>{html.escape(e.occurred_at.isoformat())}</td><td>{html.escape(e.source_label.value)}"
        f"</td><td>{html.escape(e.kind)}</td><td>{html.escape(e.summary)}</td></tr>"
        for e in entries
    )
    page = (
        "<!doctype html><html><head><meta charset='utf-8'><title>Case summary</title></head><body>"
        f"<h1>Case summary</h1>{head}<table border='1'><thead><tr><th>Time (UTC)</th><th>Label</th>"
        f"<th>Kind</th><th>Summary</th></tr></thead><tbody>{rows}</tbody></table></body></html>"
    )
    return page.encode("utf-8")


def readme_text() -> bytes:
    text = f"""CALLSHIELD CASE EVIDENCE PACKET

Contents
- manifest.json: every file with its SHA-256, plus included and excluded artifacts.
- case_summary.html / case_summary.txt: human-readable summary. A PDF rendering is a
  separately configurable component and is not produced by this generator.
- chronology.csv, calls.csv, opt_out_requests.csv: events as recorded, each with a source label.
- operator_identity_assessment.json, issue_spotting.json, user_attestation.json (if any).
- audit_extract.json, redactions.json, attachments/ (only authorized, selected files).

Source labels
OBSERVED = recorded by the software or carrier. CLAIMED = said by the caller.
MODEL_INFERRED = produced by a model or rule. USER_ATTESTED = reported by the user.
EXTERNALLY_VERIFIED = backed by an external document, with its verification scope noted.

Limits
{NO_CLAIMS}
A displayed caller number can be spoofed. An opt-out acknowledgment is not proof that
contact stopped. Issue spotting is not a legal determination. Omitted items are listed in
manifest.json under excluded_artifacts_summary.
"""
    return text.encode("utf-8")


def _zip_bytes(files: dict[str, bytes], when: datetime) -> bytes:
    buffer = io.BytesIO()
    stamp = (max(when.year, 1980), when.month, when.day, when.hour, when.minute, when.second)
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ["manifest.json", *sorted(n for n in files if n != "manifest.json")]:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    return buffer.getvalue()


def _included_entry(art: EvidenceArtifact, path: str) -> dict[str, Any]:
    return {
        "artifact_id": str(art.id),
        "sha256": art.sha256,
        "byte_length": art.byte_length,
        "mime_type": art.media_type,
        "origin": art.origin,
        "evidence_label": art.evidence_label,
        "collection_method": art.collection_method,
        "collected_at_utc": art.collected_at.isoformat(),
        "consent_status": {
            "processing_authorization": art.processing_authorization,
            "sharing_authorization": art.sharing_authorization,
        },
        "redaction_state": art.redaction_state,
        "version": art.version,
        "derived_from_id": str(art.derived_from_id) if art.derived_from_id else None,
        "path": path,
    }


def _excluded_summary(excluded: list[dict[str, str]]) -> dict[str, Any]:
    by_reason: dict[str, int] = {}
    for item in excluded:
        by_reason[item["reason"]] = by_reason.get(item["reason"], 0) + 1
    return {
        "total_excluded": len(excluded),
        "by_reason": dict(sorted(by_reason.items())),
        "items": excluded,
    }


def build_bundle(
    session: Session,
    store: ArtifactStore,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    snapshot: CaseDossierSnapshot,
    options: ExportOptions,
    now: datetime,
) -> BundleResult:
    """Assemble the zip. Every query is tenant-scoped; no settings or secrets are read."""
    case = scoped_get_or_404(session, Case, case_id, tenant_id)
    entries = build_timeline(session, tenant_id, case_id)
    included, excluded = select_attachments(
        session, store, tenant_id, case_id, options.artifact_ids
    )
    files: dict[str, bytes] = {
        "README_EVIDENCE.txt": readme_text(),
        "case_summary.html": summary_html(case, snapshot.meta, entries),
        "case_summary.txt": summary_text(case, snapshot.meta, entries),
        "chronology.csv": chronology_csv(entries),
        "calls.csv": calls_csv(session, tenant_id, case_id, options.mask_numbers),
        "opt_out_requests.csv": opt_out_csv(session, tenant_id, case_id),
        "operator_identity_assessment.json": _json_bytes(
            operator_identity(session, tenant_id, case, options.mask_numbers)
        ),
        "issue_spotting.json": _json_bytes(issue_spotting(session, tenant_id, case_id)),
        "audit_extract.json": _json_bytes(audit_extract(session, tenant_id, case_id)),
        "redactions.json": _json_bytes(
            redactions_doc(session, tenant_id, case_id, {a.id for a, _ in included})
        ),
    }
    omitted: list[str] = []
    attestation = user_attestation(session, store, tenant_id, case_id)
    if attestation is None:
        omitted.append("user_attestation.json: no user attestation recorded")
    else:
        files["user_attestation.json"] = _json_bytes(attestation)
    included_rows = []
    for art, data in included:
        path = _attachment_path(art)
        files[path] = data
        included_rows.append(_included_entry(art, path))
    manifest = _manifest(case, snapshot, options, now, files, included_rows, excluded, omitted)
    files["manifest.json"] = _json_bytes(manifest)
    return BundleResult(_zip_bytes(files, now), manifest, manifest["export_digest"])


def _manifest(
    case: Case,
    snapshot: CaseDossierSnapshot,
    options: ExportOptions,
    now: datetime,
    files: dict[str, bytes],
    included_rows: list[dict[str, Any]],
    excluded: list[dict[str, str]],
    omitted: list[str],
) -> dict[str, Any]:
    file_rows = [
        {"path": name, "sha256": sha256_hex(data), "byte_length": len(data)}
        for name, data in sorted(files.items())
    ]
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "case_id": str(case.id),
        "snapshot_id": str(snapshot.id),
        "snapshot_digest": snapshot.digest,
        "export_version": options.export_version,
        "created_at_utc": now.isoformat(),
        "tenant_id_pseudonym": tenant_pseudonym(case.tenant_id),
        "included_artifacts": included_rows,
        "excluded_artifacts_summary": _excluded_summary(excluded),
        "omitted_files": omitted,
        "files": file_rows,
        "generator_version": GENERATOR_VERSION,
        "hash_algorithm": HASH_ALGORITHM,
        "review_status": {
            "case_state": case.state,
            "attribution_status": case.attribution_status,
            "legal_assessment": case.legal_assessment,
            "reviewed_by_counsel": case.legal_assessment == "COUNSEL_REVIEWED",
        },
        "disclaimer": NO_CLAIMS,
    }
    manifest["export_digest"] = sha256_json(manifest)
    return manifest
