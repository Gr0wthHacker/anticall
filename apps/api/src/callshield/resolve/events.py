"""Ledger + timeline helpers. Payloads carry masked numbers only."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from callshield.core.idempotency import make_idempotency_key
from callshield.db.models.resolve_ext import ResolutionTimelineEntry
from callshield.domain.enums import EventOrigin
from callshield.domain.topics import Topic
from callshield.ledger import append_event


def emit(
    session: Session,
    tenant_id: uuid.UUID,
    topic: Topic,
    payload: dict[str, Any],
    key_parts: tuple[object, ...],
    origin: EventOrigin = EventOrigin.USER_ACTION,
) -> bool:
    """Append a ledger event idempotently. Returns True if newly created."""
    _, created = append_event(
        session, tenant_id, topic, payload, origin, make_idempotency_key(*key_parts)
    )
    return created


def add_timeline(
    session: Session,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    kind: str,
    actor: str,
    data: dict[str, Any] | None = None,
    attempt_id: uuid.UUID | None = None,
) -> ResolutionTimelineEntry:
    session.flush()
    last = session.execute(
        select(func.max(ResolutionTimelineEntry.seq)).where(
            ResolutionTimelineEntry.tenant_id == tenant_id,
            ResolutionTimelineEntry.case_id == case_id,
        )
    ).scalar()
    entry = ResolutionTimelineEntry(
        tenant_id=tenant_id,
        case_id=case_id,
        seq=(last or 0) + 1,
        attempt_id=attempt_id,
        kind=kind,
        actor=actor,
        data=data or {},
    )
    session.add(entry)
    return entry
