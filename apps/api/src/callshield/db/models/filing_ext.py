"""ENFORCE-FILING extension tables (assessment runs, filing metadata, receipts).

Own module so shared model files stay untouched. Import side effect registers
these tables on Base.metadata (imported by callshield.assessment / callshield.filing).
All rows are append-only except the explicit status columns noted per table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from callshield.core.clock import utcnow
from callshield.db.base import Base, JSONType, TenantOwned, UTCDateTime, created_column, id_column


class DestinationRegistryExt(Base):
    """Global companion to destination_registry: stable slug, template, honest capability."""

    __tablename__ = "destination_registry_ext"

    id: Mapped[uuid.UUID] = id_column()
    destination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("destination_registry.id"), unique=True, nullable=False
    )
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    template_key: Mapped[str | None] = mapped_column(String(64))
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_handoff: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    notices: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    seed_version: Mapped[str] = mapped_column(String(32), default="")


class AssessmentRun(TenantOwned, Base):
    """One deterministic evaluation (append-only). Per-issue rows live in case_assessments."""

    __tablename__ = "assessment_runs"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    ruleset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    user_facts: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    facts_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    results: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    disclaimer: Mapped[str] = mapped_column(Text, nullable=False)
    ai_commentary: Mapped[str | None] = mapped_column(Text)
    ai_commentary_label: Mapped[str | None] = mapped_column(String(32))
    ai_model_version: Mapped[str | None] = mapped_column(String(100))
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class FilingDraftMeta(TenantOwned, Base):
    """Immutable companion of a filing_drafts row (one row per revision)."""

    __tablename__ = "filing_draft_meta"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = id_column()
    draft_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("filing_drafts.id"), unique=True, nullable=False
    )
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    lineage_id: Mapped[uuid.UUID] = mapped_column(index=True)
    destination_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("destination_registry.id"))
    destination_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="ORIGINAL")  # ORIGINAL | SUPPLEMENT
    supersedes_draft_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("filing_drafts.id"))
    supplements_submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("filing_submissions.id")
    )
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    completeness: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    authorization_basis: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = created_column()


class FilingApprovalDetail(TenantOwned, Base):
    """Everything the user attested to for ONE approval (19.3 guards, ENF-014)."""

    __tablename__ = "filing_approval_details"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = id_column()
    approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_approvals.id"), unique=True, nullable=False
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("filing_drafts.id"), index=True)
    statement_ids: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    displayed_items: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    completed_fields: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    sharing_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    destination_validated: Mapped[bool] = mapped_column(Boolean, default=False)
    line_authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(48), default="filing.approve")
    authorization_basis: Mapped[str] = mapped_column(String(200), default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = created_column()


class FilingSubmissionExt(TenantOwned, Base):
    """Submission companion: dedupe key, approval consumption, provenance of the command."""

    __tablename__ = "filing_submission_ext"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key"),
        UniqueConstraint("approval_id"),
    )

    id: Mapped[uuid.UUID] = id_column()
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("filing_submissions.id"), unique=True, nullable=False
    )
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_approvals.id"), nullable=False
    )
    agency: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL once the submission is no longer "active" (FAILED); unique while active (ENF-018).
    dedupe_key: Mapped[str | None] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(16), default="ORIGINAL")
    supplements_submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("filing_submissions.id")
    )
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    scope: Mapped[str] = mapped_column(String(48), default="filing.submit")
    authorization_basis: Mapped[str] = mapped_column(String(200), default="")
    client_idempotency_key: Mapped[str | None] = mapped_column(String(255))
    status_checks: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created_column()


class FilingReceipt(TenantOwned, Base):
    """Append-only receipts (ENF-017). Never invented: every row has a real source."""

    __tablename__ = "filing_receipts"

    id: Mapped[uuid.UUID] = id_column()
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("filing_submissions.id"), index=True
    )
    receipt_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_label: Mapped[str] = mapped_column(String(32), nullable=False)
    confirmation_number: Mapped[str | None] = mapped_column(String(200))
    confirmation_source: Mapped[str] = mapped_column(String(32), default="NONE")
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    endpoint: Mapped[str] = mapped_column(String(1000), default="")
    sanitized_response: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = created_column()
