"""Append-only event ledger (PRD 8.2, 19.1).

Envelope = topic + schema_version + origin + created_at + idempotency_key + tenant scope.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from callshield.core.clock import utcnow
from callshield.db.models.platform import LedgerEvent
from callshield.domain.enums import EventOrigin
from callshield.domain.topics import CURRENT_SCHEMA_VERSION, Topic


class EventEnvelope(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    topic: str
    schema_version: int
    origin: str
    idempotency_key: str
    payload: dict[str, Any]
    provider_ref: str | None
    created_at: datetime


def _find(session: Session, tenant_id: uuid.UUID, key: str) -> LedgerEvent | None:
    stmt = select(LedgerEvent).where(
        LedgerEvent.tenant_id == tenant_id, LedgerEvent.idempotency_key == key
    )
    return session.execute(stmt).scalar_one_or_none()


def append_event(
    session: Session,
    tenant_id: uuid.UUID,
    topic: Topic | str,
    payload: dict[str, Any],
    origin: EventOrigin | str,
    idempotency_key: str,
    provider_ref: str | None = None,
) -> tuple[LedgerEvent, bool]:
    """Insert an event unless (tenant_id, idempotency_key) already exists.

    Returns (event, created). A duplicate key returns the ORIGINAL row with
    created=False; the new payload is ignored, never merged.
    The caller owns commit.
    """
    existing = _find(session, tenant_id, idempotency_key)
    if existing is not None:
        return existing, False
    event = LedgerEvent(
        tenant_id=tenant_id,
        topic=str(topic),
        schema_version=CURRENT_SCHEMA_VERSION,
        origin=str(origin),
        idempotency_key=idempotency_key,
        payload=payload,
        provider_ref=provider_ref,
        created_at=utcnow(),
    )
    try:
        with session.begin_nested():
            session.add(event)
    except IntegrityError:
        # Lost a race with a concurrent writer: return the winner's row.
        winner = _find(session, tenant_id, idempotency_key)
        if winner is None:
            raise
        return winner, False
    return event, True


def list_events(
    session: Session,
    tenant_id: uuid.UUID,
    topic: Topic | str | None = None,
    limit: int = 100,
) -> Sequence[LedgerEvent]:
    stmt = select(LedgerEvent).where(LedgerEvent.tenant_id == tenant_id)
    if topic is not None:
        stmt = stmt.where(LedgerEvent.topic == str(topic))
    stmt = stmt.order_by(LedgerEvent.created_at, LedgerEvent.id).limit(limit)
    return session.execute(stmt).scalars().all()
