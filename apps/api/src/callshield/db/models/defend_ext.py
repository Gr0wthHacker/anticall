"""DEFEND extension tables (new; no changes to existing tables).

Needs an alembic migration (see report): line_endpoints, webhook_receipts,
line_verification_challenges, call_routings, telephony_health, consent_line_links.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from callshield.db.base import Base, JSONType, TenantOwned, UTCDateTime, created_column, id_column


class LineEndpoint(TenantOwned, Base):
    """Maps a public opaque line_key to exactly one protected line and its webhook secret.

    The tenant of an inbound webhook is resolved ONLY through this row (never payload).
    The secret is stored as given (encryption at rest is a deployment concern) and must
    never be logged or returned after creation/rotation.
    """

    __tablename__ = "line_endpoints"
    __table_args__ = (UniqueConstraint("provider", "line_key"),)

    id: Mapped[uuid.UUID] = id_column()
    line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("protected_lines.id"), unique=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    line_key: Mapped[str] = mapped_column(String(80), nullable=False)
    endpoint_id: Mapped[str | None] = mapped_column(String(120))
    webhook_secret: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = created_column()
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class WebhookReceipt(TenantOwned, Base):
    """Seen (provider, nonce) pairs for replay prevention. Purge rows past the window."""

    __tablename__ = "webhook_receipts"
    __table_args__ = (UniqueConstraint("tenant_id", "line_id", "nonce"),)

    id: Mapped[uuid.UUID] = id_column()
    line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("protected_lines.id"), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    received_at: Mapped[datetime] = created_column()


class LineVerificationChallenge(TenantOwned, Base):
    """Ownership challenge (D-001). Only a salted hash of the code is stored."""

    __tablename__ = "line_verification_challenges"

    id: Mapped[uuid.UUID] = id_column()
    line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("protected_lines.id"), index=True, nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = created_column()


class CallRouting(TenantOwned, Base):
    """The deterministic routing decision and screening state for a call session."""

    __tablename__ = "call_routings"

    id: Mapped[uuid.UUID] = id_column()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("call_sessions.id"), unique=True, nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), default="NONE")
    rule_id: Mapped[uuid.UUID | None] = mapped_column()
    hop_count: Mapped[int] = mapped_column(Integer, default=0)
    processing_mode: Mapped[str] = mapped_column(String(32), default="METADATA_ONLY")
    to_e164: Mapped[str | None] = mapped_column(String(20))  # PII; call_sessions has no to/from
    from_displayed: Mapped[str | None] = mapped_column(String(64))  # PII; OBSERVED display
    screening_active: Mapped[bool] = mapped_column(Boolean, default=False)
    screening_state: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = created_column()


class TelephonyHealth(TenantOwned, Base):
    """Per-tenant adapter status; updated by verified provider events."""

    __tablename__ = "telephony_health"
    __table_args__ = (UniqueConstraint("tenant_id", "provider"),)

    id: Mapped[uuid.UUID] = id_column()
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    detail: Mapped[str] = mapped_column(String(200), default="")
    last_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = created_column()


class ConsentLineLink(TenantOwned, Base):
    """Links consent_records (which has no line column) to a protected line."""

    __tablename__ = "consent_line_links"

    consent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("consent_records.id"), primary_key=True
    )
    line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("protected_lines.id"), index=True, nullable=False
    )
