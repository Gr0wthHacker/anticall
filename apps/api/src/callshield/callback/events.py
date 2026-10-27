"""Ledger topics (string constants; the shared Topic enum is not edited) and emit helper.

Payloads never contain raw destination numbers; use masked forms only.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from callshield.core.idempotency import make_idempotency_key
from callshield.domain.enums import EventOrigin
from callshield.ledger import append_event

AUTHORIZED = "callback.authorized"
QUEUED = "callback.queued"
BLOCKED = "callback.blocked"
CANCELLED = "callback.cancelled"
STARTED = "callback.started"
COMPLETED = "callback.completed"
POSSIBLE_SPOOF = "callback.possible_spoof"


def emit(
    session: Session,
    tenant_id: uuid.UUID,
    topic: str,
    payload: dict[str, Any],
    *key_parts: object,
    origin: EventOrigin = EventOrigin.SYSTEM,
) -> bool:
    _, created = append_event(
        session, tenant_id, topic, payload, origin, make_idempotency_key(topic, *key_parts)
    )
    return created
