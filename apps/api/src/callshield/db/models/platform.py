"""Platform tables: ledger (append-only), audit, jobs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from callshield.core.clock import utcnow
from callshield.db.base import Base, JSONType, TenantOwned, UTCDateTime, created_column, id_column
from callshield.domain.enums import JobState
from callshield.domain.topics import CURRENT_SCHEMA_VERSION


class LedgerEvent(TenantOwned, Base):
    """Append-only operational event (PRD 8.2, 19.1). UPDATE/DELETE blocked by a
    Postgres trigger; application code must only INSERT via ledger.append_event."""

    __tablename__ = "ledger_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        Index("ix_ledger_events_tenant_topic_created", "tenant_id", "topic", "created_at"),
    )

    id: Mapped[uuid.UUID] = id_column()
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=CURRENT_SCHEMA_VERSION)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    provider_ref: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class AuditEvent(TenantOwned, Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = id_column()
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    immutable_ref: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 of content


class Job(TenantOwned, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        Index("ix_jobs_state_run_after", "state", "run_after"),
    )

    id: Mapped[uuid.UUID] = id_column()
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    state: Mapped[str] = mapped_column(String(16), default=JobState.QUEUED.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(String(1000))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = created_column()
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
