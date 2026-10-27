"""Telephony health: adapter status per tenant, SIP registration failure handling."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.audit import SYSTEM_ACTOR, record_audit
from callshield.core.clock import utcnow
from callshield.core.idempotency import make_idempotency_key
from callshield.db.models.defend import ProtectedLine
from callshield.db.models.defend_ext import LineEndpoint, TelephonyHealth
from callshield.defend.lifecycle import IngestOutcome
from callshield.defend.notify import Notification, notify
from callshield.domain.enums import EventOrigin
from callshield.domain.topics import Topic
from callshield.ledger import append_event
from callshield.telephony.base import ProviderAdapter, ProviderCallEvent, ProviderEventType

logger = logging.getLogger("callshield.defend")

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"
STATUS_UNKNOWN = "UNKNOWN"


def get_health_row(session: Session, tenant_id: uuid.UUID, provider: str) -> TelephonyHealth:
    row = session.execute(
        select(TelephonyHealth).where(
            TelephonyHealth.tenant_id == tenant_id, TelephonyHealth.provider == provider
        )
    ).scalar_one_or_none()
    if row is None:
        row = TelephonyHealth(tenant_id=tenant_id, provider=provider, status=STATUS_UNKNOWN)
        session.add(row)
    return row


def touch_health(session: Session, tenant_id: uuid.UUID, provider: str) -> None:
    """A verified event proves the path works unless a failure is still outstanding."""
    row = get_health_row(session, tenant_id, provider)
    row.last_event_at = utcnow()
    if row.status == STATUS_UNKNOWN:
        row.status = STATUS_OK
    row.updated_at = utcnow()


def handle_registration(
    session: Session,
    line: ProtectedLine,
    endpoint: LineEndpoint,
    event: ProviderCallEvent,
    adapter: ProviderAdapter,
) -> IngestOutcome:
    row = get_health_row(session, line.tenant_id, event.provider)
    row.last_event_at = utcnow()
    row.updated_at = utcnow()
    if event.event_type == ProviderEventType.REGISTRATION_OK:
        row.status, row.detail = STATUS_OK, ""
        record_audit(session, line.tenant_id, SYSTEM_ACTOR, "telephony.registration_ok", {})
        return IngestOutcome("accepted", detail="registration_ok")
    row.status, row.detail = STATUS_DEGRADED, event.detail or "SIP registration failed"
    key = make_idempotency_key("registration-failed", event.provider, line.id, event.event_id)
    _, created = append_event(
        session,
        line.tenant_id,
        Topic.CALL_FAILED,
        {"kind": "sip_registration_failed", "line_id": str(line.id), "provider": event.provider},
        EventOrigin.PROVIDER_WEBHOOK,
        key,
        provider_ref=event.event_id,
    )
    if not created:
        return IngestOutcome("duplicate", detail="registration_failed")
    restored = adapter.restore_fallback(endpoint.endpoint_id)
    record_audit(
        session,
        line.tenant_id,
        SYSTEM_ACTOR,
        "telephony.registration_failed",
        {"line_id": str(line.id), "provider": event.provider, "fallback_restored": restored},
    )
    notify(
        Notification(
            line.tenant_id,
            "telephony.registration_failed",
            {"line_id": str(line.id), "provider": event.provider, "fallback_restored": restored},
        )
    )
    return IngestOutcome("accepted", detail="registration_failed")
