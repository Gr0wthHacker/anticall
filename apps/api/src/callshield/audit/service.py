"""Audit trail writers. Corrections are new audit events, never edits."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from callshield.core.clock import utcnow
from callshield.core.hashing import sha256_json
from callshield.db.models.enforce import CaseAuditEvent
from callshield.db.models.platform import AuditEvent
from callshield.domain.enums import ActorType


def actor_for_user(user_id: uuid.UUID) -> str:
    return f"user:{user_id}"


SYSTEM_ACTOR = "system"


def record_audit(
    session: Session,
    tenant_id: uuid.UUID,
    actor: str,
    action: str,
    data: dict[str, Any] | None = None,
) -> AuditEvent:
    """Write an audit_events row. immutable_ref is a content digest (tamper evidence
    only, not proof of authenticity). The caller owns commit."""
    now = utcnow()
    body = data or {}
    event = AuditEvent(
        tenant_id=tenant_id,
        actor=actor,
        action=action,
        data=body,
        time=now,
        immutable_ref=sha256_json(
            {"tenant": tenant_id, "actor": actor, "action": action, "data": body, "time": now}
        ),
    )
    session.add(event)
    return event


def record_case_audit(
    session: Session,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    actor_type: ActorType | str,
    action: str,
    data: dict[str, Any] | None = None,
    actor_id: str | None = None,
) -> CaseAuditEvent:
    event = CaseAuditEvent(
        tenant_id=tenant_id,
        case_id=case_id,
        actor_type=str(actor_type),
        actor_id=actor_id,
        action=action,
        event_time=utcnow(),
        event_data=data or {},
    )
    session.add(event)
    return event
