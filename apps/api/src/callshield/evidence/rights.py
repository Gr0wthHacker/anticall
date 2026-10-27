"""Data-subject rights (ENF-020, 19.13-12): export, deletion request, withdrawal.

Deletion has documented limits; they are returned with every response so the user
is never promised more than the system can do.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.audit import record_audit
from callshield.auth.scoping import scoped_select
from callshield.core.clock import utcnow
from callshield.db.models import (
    Case,
    ConsentRecord,
    Incident,
    OptOutEvent,
    ProtectedLine,
)
from callshield.db.models.enforce import EvidenceArtifact
from callshield.db.models.enforce_ext import DataSubjectRequest, IncidentReclassification
from callshield.db.models.tenancy import User
from callshield.evidence.access import describe_artifact
from callshield.evidence.ingest import METADATA_TYPES
from callshield.evidence.retention import delete_artifacts
from callshield.evidence.store import ArtifactStore

DELETION_LIMITS: tuple[str, ...] = (
    "Items under legal hold are retained until the hold is released.",
    "Copies already exported, downloaded, shared or submitted to an agency or other third "
    "party cannot be recalled by this system.",
    "Non-personal audit metadata is kept: record ids, source type, content digest, size, "
    "timestamps and the fact of deletion.",
    "Deleting an item also deletes its derivatives (redactions, transcript revisions).",
    "Backups are purged on the operator's backup rotation schedule, not immediately.",
    "Wrong-person or lawful-retention requirements may require the operator to keep some "
    "records; the response lists what was retained and why.",
)


def _finish(
    session: Session, request: DataSubjectRequest, status: str, result: dict[str, Any]
) -> DataSubjectRequest:
    request.status = status
    request.result = result
    request.completed_at = utcnow()
    session.flush()
    return request


def _new_request(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    kind: str,
    scope: dict[str, Any],
) -> DataSubjectRequest:
    request = DataSubjectRequest(tenant_id=tenant_id, user_id=user_id, kind=kind, scope=scope)
    session.add(request)
    session.flush()
    return request


def request_artifact_deletion(
    session: Session,
    store: ArtifactStore,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
    reason: str,
) -> DataSubjectRequest:
    """Delete an artifact (and derivatives) unless a legal hold applies."""
    actor = f"user:{user_id}"
    request = _new_request(
        session, tenant_id, user_id, "DELETION", {"artifact_id": str(artifact_id), "reason": reason}
    )
    outcome = delete_artifacts(
        session, store, tenant_id, artifact_id, actor, reason or "user_request"
    )
    if outcome.retained and outcome.deleted:
        status = "PARTIAL_LEGAL_HOLD"
    elif outcome.retained:
        status = "DENIED_LEGAL_HOLD"
    else:
        status = "COMPLETED"
    record_audit(session, tenant_id, actor, "data_subject.deletion_requested", {"status": status})
    result = {
        "deleted": outcome.deleted,
        "retained": outcome.retained,
        "limits": list(DELETION_LIMITS),
    }
    return _finish(session, request, status, result)


def _rows(session: Session, model: type[Any], tenant_id: uuid.UUID) -> list[Any]:
    return list(session.execute(scoped_select(model, tenant_id)).scalars().all())


def _export_sections(session: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Tenant-scoped record sections of the data-subject export."""
    return {
        "protected_lines": [
            {"id": str(r.id), "e164": r.e164, "label": r.label}
            for r in _rows(session, ProtectedLine, tenant_id)
        ],
        "cases": [
            {
                "id": str(c.id),
                "title": c.title,
                "state": c.state,
                "opened_at": c.opened_at.isoformat(),
            }
            for c in _rows(session, Case, tenant_id)
        ],
        "incidents": [
            {
                "id": str(i.id),
                "displayed_caller_id": i.displayed_caller_id,
                "label": i.label,
                "evidence_label": i.evidence_label,
                "occurred_at": i.occurred_at.isoformat() if i.occurred_at else None,
            }
            for i in _rows(session, Incident, tenant_id)
        ],
        "evidence_artifacts": [
            describe_artifact(a) for a in _rows(session, EvidenceArtifact, tenant_id)
        ],
        "consent_records": [
            {
                "id": str(c.id),
                "processing_mode": c.processing_mode,
                "authorized": c.authorized,
                "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
            }
            for c in _rows(session, ConsentRecord, tenant_id)
        ],
        "opt_out_events": [
            {"id": str(o.id), "status": o.status, "created_at": o.created_at.isoformat()}
            for o in _rows(session, OptOutEvent, tenant_id)
        ],
        "reclassifications": [
            {"incident_id": str(r.incident_id), "from": r.previous_label, "to": r.new_label}
            for r in _rows(session, IncidentReclassification, tenant_id)
        ],
    }


def build_data_export(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any]:
    """Machine-readable export of the account's data (metadata; content via the evidence API)."""
    user = session.execute(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    ).scalar_one()
    request = _new_request(session, tenant_id, user_id, "EXPORT", {})
    payload: dict[str, Any] = {
        "generated_at": utcnow().isoformat(),
        "user": {"id": str(user.id), "email": user.email, "display_name": user.display_name},
        **_export_sections(session, tenant_id),
        "deletion_limits": list(DELETION_LIMITS),
        "request_id": str(request.id),
    }
    _finish(session, request, "COMPLETED", {"sections": sorted(payload)})
    record_audit(session, tenant_id, f"user:{user_id}", "data_subject.export_generated", {})
    return payload


def withdraw_processing(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, reason: str
) -> DataSubjectRequest:
    """Stop further processing: revoke sharing everywhere and processing on content.

    Nothing is deleted here (deletion is a separate request and respects legal hold).
    Lawful signaling metadata artifacts keep their processing state (ENF-008).
    """
    request = _new_request(session, tenant_id, user_id, "WITHDRAWAL", {"reason": reason})
    revoked = 0
    for artifact in _rows(session, EvidenceArtifact, tenant_id):
        if artifact.deleted_at is not None:
            continue
        artifact.sharing_authorization = "REVOKED"
        if artifact.source_type not in {t.value for t in METADATA_TYPES}:
            artifact.processing_authorization = "REVOKED"
        revoked += 1
    record_audit(
        session,
        tenant_id,
        f"user:{user_id}",
        "data_subject.processing_withdrawn",
        {"artifacts": revoked},
    )
    note = (
        "Community sharing is off by default and no sharing is performed by this module; "
        "revocation of already-disclosed copies is not possible."
    )
    return _finish(session, request, "COMPLETED", {"artifacts_revoked": revoked, "note": note})
