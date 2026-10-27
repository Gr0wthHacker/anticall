"""Defend-module tables: lines, calls, observations, claims, classifications, consent."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from callshield.db.base import Base, JSONType, TenantOwned, UTCDateTime, created_column, id_column
from callshield.domain.enums import (
    EvidenceLabel,
    IncidentStatus,
    LineVerificationStatus,
    ReviewStatus,
    VerificationStatus,
)


class ProtectedLine(TenantOwned, Base):
    __tablename__ = "protected_lines"

    id: Mapped[uuid.UUID] = id_column()
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    e164: Mapped[str] = mapped_column(String(20), nullable=False)  # PII: never log raw
    label: Mapped[str] = mapped_column(String(100), default="")
    verification_status: Mapped[str] = mapped_column(
        String(32), default=LineVerificationStatus.UNVERIFIED.value
    )
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    screening_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    routing_config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = created_column()


class CallSession(TenantOwned, Base):
    __tablename__ = "call_sessions"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "provider_call_id"),)

    id: Mapped[uuid.UUID] = id_column()
    line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("protected_lines.id"), index=True)
    provider: Mapped[str] = mapped_column(String(64), default="unknown")
    provider_call_id: Mapped[str] = mapped_column(String(200), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    disposition: Mapped[str] = mapped_column(String(32), default="RECEIVED")
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_column()


class CallerObservation(TenantOwned, Base):
    """Observed CLI is an observed number, NOT a verified calling party (19.4)."""

    __tablename__ = "caller_observations"

    id: Mapped[uuid.UUID] = id_column()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    displayed_caller_id: Mapped[str | None] = mapped_column(String(64))
    attestation_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    source: Mapped[str] = mapped_column(String(64), default="provider")
    label: Mapped[str] = mapped_column(String(32), default=EvidenceLabel.OBSERVED.value)
    observed_at: Mapped[datetime] = created_column()
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("caller_observations.id"))


class OrganizationClaim(TenantOwned, Base):
    """What the caller said they are. CLAIMED, never a proven identity (D-005)."""

    __tablename__ = "organization_claims"

    id: Mapped[uuid.UUID] = id_column()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    claimed_name: Mapped[str] = mapped_column(String(300), nullable=False)
    statement_type: Mapped[str] = mapped_column(String(32), default="SELLER_NAME")
    label: Mapped[str] = mapped_column(String(32), default=EvidenceLabel.CLAIMED.value)
    verification_status: Mapped[str] = mapped_column(
        String(32), default=VerificationStatus.UNVERIFIED.value
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organization_claims.id"))
    created_at: Mapped[datetime] = created_column()


class Classification(TenantOwned, Base):
    """Model output. Confidence is NOT factual verification. Corrections append rows."""

    __tablename__ = "classifications"

    id: Mapped[uuid.UUID] = id_column()
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_label: Mapped[str] = mapped_column(
        String(32), default=EvidenceLabel.MODEL_INFERRED.value
    )
    reviewed_status: Mapped[str] = mapped_column(String(32), default=ReviewStatus.UNREVIEWED.value)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("classifications.id"))
    created_at: Mapped[datetime] = created_column()


class ConsentRecord(TenantOwned, Base):
    __tablename__ = "consent_records"

    id: Mapped[uuid.UUID] = id_column()
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    processing_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    legal_basis_config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    authorized_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = created_column()


class Incident(TenantOwned, Base):
    """Normalized unit linking a call session to cases (19.12)."""

    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = id_column()
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("call_sessions.id"), index=True
    )
    line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("protected_lines.id"))
    displayed_caller_id: Mapped[str | None] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    status: Mapped[str] = mapped_column(String(32), default=IncidentStatus.CANDIDATE.value)
    evidence_label: Mapped[str] = mapped_column(String(32), default=EvidenceLabel.OBSERVED.value)
    summary: Mapped[str] = mapped_column(Text, default="")
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = created_column()


class RoutingRule(TenantOwned, Base):
    """Deterministic routing (D-003); evaluated before any LLM."""

    __tablename__ = "routing_rules"

    id: Mapped[uuid.UUID] = id_column()
    line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("protected_lines.id"), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    matcher: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_column()


class TrustedContact(TenantOwned, Base):
    __tablename__ = "trusted_contacts"
    __table_args__ = (UniqueConstraint("tenant_id", "line_id", "e164"),)

    id: Mapped[uuid.UUID] = id_column()
    line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("protected_lines.id"), index=True)
    e164: Mapped[str] = mapped_column(String(20), nullable=False)  # PII
    kind: Mapped[str] = mapped_column(String(32), default="TRUSTED")
    name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = created_column()
