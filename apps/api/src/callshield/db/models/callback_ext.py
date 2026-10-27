"""CALLBACK flow tables (docs/prd/CHANGE_callback_flow.md). New tables only.

Needs an alembic migration + RLS (tenant tables) -- see report. Privacy notes:
* No raw destination numbers are stored. Destinations are re-derived at dial time from the
  logged inbound call; here we keep only a salted HMAC key (`destination_key`) and a masked
  form for display.
* `CallbackGlobalBlocklist` and `CallbackGlobalAttempt` are deliberately NOT tenant-owned:
  the instance-wide caps/blocklist cross tenants, so they hold nothing but the HMAC key and
  a timestamp/reason.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from callshield.db.base import Base, JSONType, TenantOwned, UTCDateTime, created_column, id_column


class CallbackSettingsRow(TenantOwned, Base):
    """Overrides. `scope_key` is 'TENANT' (operator keys) or 'USER:<uuid>' (user keys)."""

    __tablename__ = "callback_settings"
    __table_args__ = (UniqueConstraint("tenant_id", "scope_key"),)

    id: Mapped[uuid.UUID] = id_column()
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    timezone: Mapped[str | None] = mapped_column(String(64))
    overrides: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = created_column()


class CallbackAuthorization(TenantOwned, Base):
    """One tap = one authorization (gate 4). Expires after 30 min; single use."""

    __tablename__ = "callback_authorizations"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = id_column()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    inbound_call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("call_sessions.id"), index=True, nullable=False
    )
    app_version: Mapped[str] = mapped_column(String(40), nullable=False)
    authorized_at: Mapped[datetime] = created_column()
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)


class CallbackRequest(TenantOwned, Base):
    """A queued/executed callback. `open_slot` (= inbound call id while the request is
    non-terminal, else NULL) is UNIQUE so two simultaneous queue requests yield one dial."""

    __tablename__ = "callback_requests"
    __table_args__ = (UniqueConstraint("open_slot"),)

    id: Mapped[uuid.UUID] = id_column()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    inbound_call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("call_sessions.id"), index=True, nullable=False
    )
    line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("protected_lines.id"), nullable=False)
    authorization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("callback_authorizations.id"), nullable=False
    )
    destination_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    destination_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    open_slot: Mapped[str | None] = mapped_column(String(64))
    outbound_recording: Mapped[bool] = mapped_column(Boolean, default=False)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id"))
    case_id: Mapped[uuid.UUID | None] = mapped_column()  # enforce cases.id, by id only
    gate_report: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    placed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # set once, before ring
    created_at: Mapped[datetime] = created_column()
    updated_at: Mapped[datetime] = created_column()


class CallbackAttempt(TenantOwned, Base):
    """Counted when the dial STARTS (answered or not). Source of truth for per-user caps."""

    __tablename__ = "callback_attempts"
    __table_args__ = (
        UniqueConstraint("request_id"),
        UniqueConstraint("tenant_id", "inbound_call_id", "attempt_no"),
    )

    id: Mapped[uuid.UUID] = id_column()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("callback_requests.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    inbound_call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("call_sessions.id"), index=True, nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    destination_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    started_at: Mapped[datetime] = created_column()


class CallbackGlobalAttempt(Base):
    """Instance-wide cap ledger. Crosses tenants BY DESIGN: no tenant/user/request id."""

    __tablename__ = "callback_global_attempts"

    id: Mapped[uuid.UUID] = id_column()
    destination_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    attempted_at: Mapped[datetime] = created_column()


class CallbackBlocklist(TenantOwned, Base):
    __tablename__ = "callback_blocklist"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "destination_key"),)

    id: Mapped[uuid.UUID] = id_column()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    destination_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    destination_masked: Mapped[str] = mapped_column(String(32), default="")
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = created_column()


class CallbackGlobalBlocklist(Base):
    """Instance-wide blocklist. Cross-tenant by design; HMAC key + reason only."""

    __tablename__ = "callback_global_blocklist"

    destination_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = created_column()


class CallbackResult(TenantOwned, Base):
    """Structured outcome. `fields` = answering-party values (all labeled CLAIMED);
    `call_metadata` = platform observations (labeled OBSERVED). Tombstoned after retention."""

    __tablename__ = "callback_results"
    __table_args__ = (UniqueConstraint("request_id"),)

    id: Mapped[uuid.UUID] = id_column()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("callback_requests.id"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    call_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    case_id: Mapped[uuid.UUID | None] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    tombstoned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = created_column()


class CallbackMedia(TenantOwned, Base):
    """Post-disclosure audio/transcript objects (only when outbound_recording=true)."""

    __tablename__ = "callback_media"

    id: Mapped[uuid.UUID] = id_column()
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("callback_requests.id"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # AUDIO | TRANSCRIPT
    storage_key: Mapped[str] = mapped_column(String(200), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = created_column()
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class LineOutboundAttestation(TenantOwned, Base):
    """Provider-reported outbound attestation capability for a protected line."""

    __tablename__ = "line_outbound_attestation"
    __table_args__ = (UniqueConstraint("tenant_id", "line_id"),)

    id: Mapped[uuid.UUID] = id_column()
    line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("protected_lines.id"), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)  # A | B | C | UNKNOWN
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = created_column()


class LineOutboundOwnershipCheck(TenantOwned, Base):
    """Ownership-check record required before the first callback. Code stored hashed."""

    __tablename__ = "line_outbound_ownership_checks"

    id: Mapped[uuid.UUID] = id_column()
    line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("protected_lines.id"), index=True, nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = created_column()
