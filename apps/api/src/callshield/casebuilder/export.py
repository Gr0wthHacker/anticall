"""Async case export service (ENF-009, E-007): request, run, download, tokens.

`POST /exports` records a CaseExport row + a `case.export` job and returns at once.
The worker builds the zip, stores it, and marks the row COMPLETED. Downloads are
tenant-scoped, either by bearer auth or by a short-lived signed token.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import jwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from callshield.audit import record_case_audit
from callshield.auth.scoping import scoped_get_or_404, scoped_select
from callshield.casebuilder.export_bundle import ExportOptions, build_bundle
from callshield.casebuilder.snapshot import refresh_dossier_snapshot
from callshield.core.clock import utcnow
from callshield.core.config import Settings
from callshield.core.errors import ConflictError, NotFoundError, UnauthorizedError
from callshield.core.hashing import sha256_hex
from callshield.core.idempotency import command_key
from callshield.db.models.enforce import Case
from callshield.db.models.enforce_ext import CaseDossierSnapshot, CaseExport
from callshield.domain.enums import ActorType, EventOrigin
from callshield.domain.topics import Topic
from callshield.evidence.access import actor_type_for
from callshield.evidence.store import ArtifactStore
from callshield.ledger import append_event
from callshield.worker.queue import enqueue

EXPORT_JOB_KIND = "case.export"
EXPORT_RETENTION_DAYS = 30
TOKEN_AUDIENCE = "callshield-export-download"
DEFAULT_TOKEN_TTL_SECONDS = 300
MAX_TOKEN_TTL_SECONDS = 3600


def _next_version(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID) -> int:
    stmt = select(func.coalesce(func.max(CaseExport.version), 0)).where(
        CaseExport.tenant_id == tenant_id, CaseExport.case_id == case_id
    )
    return int(session.execute(stmt).scalar_one()) + 1


def _find_by_key(session: Session, tenant_id: uuid.UUID, key: str) -> CaseExport | None:
    stmt = scoped_select(CaseExport, tenant_id).where(CaseExport.idempotency_key == key)
    return session.execute(stmt).scalars().first()


def _record_requested(
    session: Session, tenant_id: uuid.UUID, export: CaseExport, actor: str
) -> None:
    payload = {
        "case_id": str(export.case_id),
        "export_id": str(export.id),
        "version": export.version,
    }
    append_event(
        session, tenant_id, Topic.EXPORT_REQUESTED, payload, EventOrigin.USER_ACTION,
        f"export-requested:{export.id}",
    )  # fmt: skip
    record_case_audit(
        session, tenant_id, export.case_id, actor_type_for(actor), "export.requested",
        payload, actor_id=actor,
    )  # fmt: skip


def request_export(
    session: Session,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    actor: str,
    *,
    artifact_ids: list[uuid.UUID] | None = None,
    mask_numbers: bool = False,
    client_key: str | None = None,
) -> tuple[CaseExport, bool]:
    """Snapshot the dossier, record the export and enqueue the build job."""
    scoped_get_or_404(session, Case, case_id, tenant_id)
    key = command_key(actor, f"case.export.{case_id}", client_key) if client_key else None
    if key is not None:
        existing = _find_by_key(session, tenant_id, key)
        if existing is not None:
            return existing, False
    snapshot = refresh_dossier_snapshot(session, tenant_id, case_id).snapshot
    export = CaseExport(
        tenant_id=tenant_id,
        case_id=case_id,
        snapshot_id=snapshot.id,
        version=_next_version(session, tenant_id, case_id),
        requested_by=actor,
        selection={
            "artifact_ids": [str(a) for a in (artifact_ids or [])],
            "mask_numbers": mask_numbers,
        },
        idempotency_key=key or f"auto:{uuid.uuid4()}",
    )
    session.add(export)
    session.flush()
    enqueue(
        session, tenant_id, EXPORT_JOB_KIND, {"export_id": str(export.id)}, f"export:{export.id}"
    )
    _record_requested(session, tenant_id, export, actor)
    return export, True


def run_export(
    session: Session,
    store: ArtifactStore,
    tenant_id: uuid.UUID,
    export_id: uuid.UUID,
    now: datetime | None = None,
) -> CaseExport:
    """Build and store the zip. Idempotent: a COMPLETED export is returned unchanged."""
    export = scoped_get_or_404(session, CaseExport, export_id, tenant_id)
    if export.status == "COMPLETED":
        return export
    now = now or utcnow()
    snapshot = scoped_get_or_404(session, CaseDossierSnapshot, export.snapshot_id, tenant_id)
    options = ExportOptions(
        export_version=export.version,
        artifact_ids=[uuid.UUID(a) for a in export.selection.get("artifact_ids", [])],
        mask_numbers=bool(export.selection.get("mask_numbers", False)),
    )
    try:
        bundle = build_bundle(session, store, tenant_id, export.case_id, snapshot, options, now)
        key = f"{tenant_id}/exports/{export.id}.zip"
        if store.exists(key):
            store.delete(key)  # retry after a crash between put and commit
        store.put(key, bundle.zip_bytes)
    except Exception as exc:
        export.status = "FAILED"
        export.failure_reason = f"{type(exc).__name__}"[:500]
        raise
    export.storage_key = key
    export.zip_sha256 = sha256_hex(bundle.zip_bytes)
    export.byte_length = len(bundle.zip_bytes)
    export.export_digest = bundle.export_digest
    export.manifest_summary = _summary(bundle.manifest)
    export.status = "COMPLETED"
    export.completed_at = now
    export.expires_at = now + timedelta(days=EXPORT_RETENTION_DAYS)
    _record_completed(session, tenant_id, export)
    return export


def _summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "included_artifacts": len(manifest["included_artifacts"]),
        "excluded_total": manifest["excluded_artifacts_summary"]["total_excluded"],
        "file_count": len(manifest["files"]) + 1,
        "snapshot_digest": manifest["snapshot_digest"],
    }


def _record_completed(session: Session, tenant_id: uuid.UUID, export: CaseExport) -> None:
    payload = {
        "case_id": str(export.case_id),
        "export_id": str(export.id),
        "export_digest": export.export_digest,
    }
    append_event(
        session, tenant_id, Topic.EXPORT_COMPLETED, payload, EventOrigin.WORKER,
        f"export-completed:{export.id}",
    )  # fmt: skip
    record_case_audit(
        session, tenant_id, export.case_id, ActorType.WORKER, "export.completed", payload,
        actor_id="worker",
    )  # fmt: skip


def get_export(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID, export_id: uuid.UUID
) -> CaseExport:
    export = scoped_get_or_404(session, CaseExport, export_id, tenant_id)
    if export.case_id != case_id:
        raise NotFoundError("CaseExport not found")
    return export


def export_bytes(store: ArtifactStore, export: CaseExport, now: datetime | None = None) -> bytes:
    """Zip bytes for a COMPLETED, unexpired export; verifies the stored digest."""
    now = now or utcnow()
    if export.status != "COMPLETED" or export.storage_key is None or export.deleted_at:
        raise ConflictError("export is not available", code="EXPORT_NOT_READY")
    if export.expires_at is not None and export.expires_at <= now:
        raise ConflictError("export has expired", code="EXPORT_EXPIRED")
    data = store.get(export.storage_key)
    if sha256_hex(data) != export.zip_sha256:
        raise ConflictError("export failed integrity check", code="EXPORT_INTEGRITY_FAILED")
    return data


def create_download_token(
    settings: Settings,
    tenant_id: uuid.UUID,
    export_id: uuid.UUID,
    ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Signed, scoped (tenant + export), short-lived token. Returns (token, expires_at)."""
    now = now or utcnow()
    expires = now + timedelta(seconds=min(max(ttl_seconds, 1), MAX_TOKEN_TTL_SECONDS))
    claims = {
        "aud": TOKEN_AUDIENCE,
        "tid": str(tenant_id),
        "eid": str(export_id),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    token = jwt.encode(
        claims, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, expires


def decode_download_token(settings: Settings, token: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Return (tenant_id, export_id) or raise UnauthorizedError."""
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=TOKEN_AUDIENCE,
        )
        return uuid.UUID(claims["tid"]), uuid.UUID(claims["eid"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise UnauthorizedError("invalid or expired download token", code="INVALID_TOKEN") from exc


def expire_exports(
    session: Session, store: ArtifactStore, tenant_id: uuid.UUID, now: datetime | None = None
) -> int:
    """Delete expired export zips (they hold a copy of personal data). Rows are kept."""
    now = now or utcnow()
    stmt = scoped_select(CaseExport, tenant_id).where(
        CaseExport.expires_at.is_not(None),
        CaseExport.expires_at <= now,
        CaseExport.deleted_at.is_(None),
    )
    count = 0
    for export in session.execute(stmt).scalars().all():
        if export.storage_key:
            store.delete(export.storage_key)
        export.storage_key = None
        export.deleted_at = now
        count += 1
    return count
