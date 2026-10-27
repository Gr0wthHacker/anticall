"""Enforce-core extension tables (own module; shared model files are untouched).

Imported by `callshield.evidence` and `callshield.casebuilder` so the metadata registers.
All rows are append-only or status-tracking records; none mutate source evidence.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from callshield.core.clock import utcnow
from callshield.db.base import Base, JSONType, TenantOwned, UTCDateTime, created_column, id_column


class CaseDossierSnapshot(TenantOwned, Base):
    """Append-only dossier snapshot metadata (19.12). `digest` covers counts and states
    only, so an unchanged dossier never produces a new row or an alert."""

    __tablename__ = "case_dossier_snapshots"
    __table_args__ = (UniqueConstraint("case_id", "snapshot_no"),)

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    snapshot_no: Mapped[int] = mapped_column(Integer, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = created_column()


class CaseExport(TenantOwned, Base):
    """Tracked, versioned export (ENF-009, E-007)."""

    __tablename__ = "case_exports"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_dossier_snapshots.id"))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="QUEUED")  # QUEUED|COMPLETED|FAILED
    requested_by: Mapped[str] = mapped_column(String(100), default="system")
    selection: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    storage_key: Mapped[str | None] = mapped_column(String(500))
    zip_sha256: Mapped[str | None] = mapped_column(String(64))
    byte_length: Mapped[int | None] = mapped_column(BigInteger)
    export_digest: Mapped[str | None] = mapped_column(String(64))
    manifest_summary: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = created_column()
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class DataSubjectRequest(TenantOwned, Base):
    """Data-subject export / deletion / withdrawal requests (ENF-020, 19.13-12)."""

    __tablename__ = "data_subject_requests"

    id: Mapped[uuid.UUID] = id_column()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # EXPORT|DELETION|WITHDRAWAL
    status: Mapped[str] = mapped_column(String(32), default="RECEIVED")
    scope: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = created_column()
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class IncidentReclassification(TenantOwned, Base):
    """User/reviewer reclassification of an incident. The source incident row is never
    edited; the latest row per incident wins in the effective view (ENF-007)."""

    __tablename__ = "incident_reclassifications"

    id: Mapped[uuid.UUID] = id_column()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id"), index=True)
    previous_label: Mapped[str] = mapped_column(String(32), nullable=False)
    new_label: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(100), default="system")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class CaseReviewItem(TenantOwned, Base):
    """Queue entry: an incident that could not be attached to a case confidently."""

    __tablename__ = "case_review_items"

    id: Mapped[uuid.UUID] = id_column()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_case_ids: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    match_ruleset_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="OPEN")  # OPEN|RESOLVED
    resolution: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = created_column()
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
