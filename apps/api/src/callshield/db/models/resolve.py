"""Resolve-module tables (PRD 5.2, 8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from callshield.core.clock import utcnow
from callshield.db.base import Base, JSONType, TenantOwned, UTCDateTime, created_column, id_column
from callshield.domain.enums import (
    DestinationProvenance,
    EvidenceLabel,
    OptOutStatus,
    ResolutionState,
    Urgency,
    VerificationStatus,
)


class ResolutionCase(TenantOwned, Base):
    __tablename__ = "resolution_cases"

    id: Mapped[uuid.UUID] = id_column()
    protected_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("protected_lines.id"), index=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    claimed_entity: Mapped[str] = mapped_column(String(300), default="")
    objective: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    urgency: Mapped[str] = mapped_column(String(16), default=Urgency.NORMAL.value)
    policy_region: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(32), default=ResolutionState.CREATED.value)
    enforce_case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id"), index=True)
    created_at: Mapped[datetime] = created_column()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ContactDestination(TenantOwned, Base):
    """Caller-ID-only destinations are never usable for automated outbound (R-002)."""

    __tablename__ = "contact_destinations"

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resolution_cases.id"), index=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)  # PII
    provenance: Mapped[str] = mapped_column(
        String(32), default=DestinationProvenance.CALLER_ID_ONLY.value
    )
    source_url_or_evidence: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    verification_status: Mapped[str] = mapped_column(
        String(32), default=VerificationStatus.UNVERIFIED.value
    )
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contact_destinations.id"))
    created_at: Mapped[datetime] = created_column()


class ContactAuthorization(TenantOwned, Base):
    """Explicit user authorization for ONE contact (8.1 critical control, 20.3)."""

    __tablename__ = "contact_authorizations"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resolution_cases.id"), index=True)
    destination_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contact_destinations.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    scope: Mapped[str] = mapped_column(String(48), nullable=False)
    authorization_basis: Mapped[str] = mapped_column(String(200), default="")
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = created_column()


class ContactAttempt(TenantOwned, Base):
    __tablename__ = "contact_attempts"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resolution_cases.id"), index=True)
    destination_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contact_destinations.id"))
    user_authorization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contact_authorizations.id")
    )
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("call_sessions.id"))
    reason: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ivr_route: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    agent_claims: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    policy_state: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    mode: Mapped[str] = mapped_column(String(16), default="HUMAN")  # HUMAN | AI | HYBRID
    review_state: Mapped[str] = mapped_column(String(32), default="UNREVIEWED")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = created_column()


class IvrPath(TenantOwned, Base):
    __tablename__ = "ivr_paths"

    id: Mapped[uuid.UUID] = id_column()
    destination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contact_destinations.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    choices: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    user_approved: Mapped[bool] = mapped_column(default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    last_tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    fallback: Mapped[str] = mapped_column(String(32), default="ABORT")
    created_at: Mapped[datetime] = created_column()


class OptOutEvent(TenantOwned, Base):
    """Acknowledgment is NOT suppression (5.2.1); status changes append new rows."""

    __tablename__ = "opt_out_events"

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resolution_cases.id"), index=True)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contact_attempts.id"))
    request_text_version: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default=OptOutStatus.DRAFTED.value)
    evidence_label: Mapped[str] = mapped_column(String(32), default=EvidenceLabel.CLAIMED.value)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("opt_out_events.id"))
    created_at: Mapped[datetime] = created_column()
