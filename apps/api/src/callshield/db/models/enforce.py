"""Enforce-module tables (PRD 19.6), merged with PRD 8 definitions.

Corrections are appended (new rows/versions/audit events), never applied by
mutating source evidence.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from callshield.core.clock import utcnow
from callshield.db.base import Base, JSONType, TenantOwned, UTCDateTime, created_column, id_column
from callshield.domain.enums import (
    AttributionStatus,
    AuthorizationState,
    CaseResolution,
    ComplaintReviewState,
    EnforceCaseState,
    FilingDraftReviewState,
    LegalAssessment,
    RecordingAuthorization,
    RedactionState,
    SubmissionState,
)


class Case(TenantOwned, Base):
    """Formal ENFORCE case (19.3). Orthogonal fields are separate columns."""

    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = id_column()
    protected_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("protected_lines.id"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    state: Mapped[str] = mapped_column(String(48), default=EnforceCaseState.OPENED.value)
    attribution_status: Mapped[str] = mapped_column(
        String(32), default=AttributionStatus.UNKNOWN.value
    )
    recording_authorization: Mapped[str] = mapped_column(
        String(32), default=RecordingAuthorization.NOT_EVALUATED.value
    )
    legal_assessment: Mapped[str] = mapped_column(
        String(32), default=LegalAssessment.NOT_REVIEWED.value
    )
    case_resolution: Mapped[str] = mapped_column(
        String(32), default=CaseResolution.UNRESOLVED.value
    )
    review_version: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    opened_at: Mapped[datetime] = created_column()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)


class CaseIncident(TenantOwned, Base):
    """Append-only linkage; latest row per (case, incident) is current (ENF-007)."""

    __tablename__ = "case_incidents"

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    linkage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_basis: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    actor: Mapped[str] = mapped_column(String(100), default="system")
    created_at: Mapped[datetime] = created_column()


class EvidenceArtifact(TenantOwned, Base):
    """Merged PRD 8 + 19.6 definition. Originals are never overwritten; derivatives
    (transcripts, redactions) are new rows pointing at derived_from_id."""

    __tablename__ = "evidence_artifacts"

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id"), index=True)
    resolution_case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resolution_cases.id"), index=True
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id"))
    source_type: Mapped[str] = mapped_column(String(48), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    storage_key: Mapped[str | None] = mapped_column(String(500))
    sha256: Mapped[str | None] = mapped_column(String(64))
    byte_length: Mapped[int | None] = mapped_column(BigInteger)
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    collection_method: Mapped[str] = mapped_column(String(100), default="")
    evidence_label: Mapped[str] = mapped_column(String(32), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    processing_authorization: Mapped[str] = mapped_column(
        String(32), default=AuthorizationState.NOT_EVALUATED.value
    )
    sharing_authorization: Mapped[str] = mapped_column(
        String(32), default=AuthorizationState.NOT_EVALUATED.value
    )
    consent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("consent_records.id"))
    redaction_state: Mapped[str] = mapped_column(String(32), default=RedactionState.NONE.value)
    derived_from_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence_artifacts.id"))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence_artifacts.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = created_column()


class CaseAssessment(TenantOwned, Base):
    """Versioned deterministic rules output. Never a legal determination (19.9)."""

    __tablename__ = "case_assessments"

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    ruleset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(100))
    issue_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(48), nullable=False)
    supporting_event_ids: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    missing_requirements: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class ComplaintDraft(TenantOwned, Base):
    __tablename__ = "complaint_drafts"

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resolution_cases.id"), index=True)
    jurisdiction: Mapped[str] = mapped_column(String(32), default="")
    draft_version: Mapped[int] = mapped_column(Integer, default=1)
    body: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    user_review_state: Mapped[str] = mapped_column(
        String(32), default=ComplaintReviewState.DRAFT.value
    )
    created_at: Mapped[datetime] = created_column()


class FilingDraft(TenantOwned, Base):
    __tablename__ = "filing_drafts"

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    agency: Mapped[str] = mapped_column(String(32), nullable=False)
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    draft_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    evidence_snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    review_state: Mapped[str] = mapped_column(
        String(32), default=FilingDraftReviewState.DRAFT.value
    )
    created_at: Mapped[datetime] = created_column()


class SubmissionApproval(TenantOwned, Base):
    __tablename__ = "submission_approvals"

    id: Mapped[uuid.UUID] = id_column()
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("filing_drafts.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    approved_payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    attestation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    authorized_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class FilingSubmission(TenantOwned, Base):
    __tablename__ = "filing_submissions"
    __table_args__ = (UniqueConstraint("idempotency_key"),)

    id: Mapped[uuid.UUID] = id_column()
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("filing_drafts.id"), index=True)
    approval_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("submission_approvals.id"))
    adapter: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(48), default=SubmissionState.PENDING.value)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    transport_response_ref: Mapped[str | None] = mapped_column(String(500))
    agency_receipt: Mapped[str | None] = mapped_column(Text)  # never invented
    failure_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = created_column()


class CaseAuditEvent(TenantOwned, Base):
    __tablename__ = "case_audit_events"

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    event_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    event_data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class DestinationRegistry(Base):
    """Global (not tenant-owned) registry of filing destinations (19.8)."""

    __tablename__ = "destination_registry"

    id: Mapped[uuid.UUID] = id_column()
    agency: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    method: Mapped[str] = mapped_column(String(32), default="manual_handoff")
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    capability_evidence: Mapped[str] = mapped_column(Text, default="")
    last_reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    authorization_requirements: Mapped[str] = mapped_column(Text, default="")
    supported_issue_types: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    supported_fields: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    attachment_constraints: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    retention: Mapped[str] = mapped_column(Text, default="")
    sunset_procedure: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
