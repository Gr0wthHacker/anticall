"""Gate 4: one tap = one authorization. Logged with user, time, inbound call and app version;
expires after 30 minutes if the dial has not started; consumed atomically (single use).

Follows the pattern of `resolve.authorizations` but is not tied to `resolution_cases`.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_select
from callshield.callback.config import AUTHORIZATION_TTL_MINUTES
from callshield.callback.gates import AuthorizationFacts
from callshield.core.clock import utcnow
from callshield.core.errors import ConflictError, UnprocessableError
from callshield.core.idempotency import command_key
from callshield.db.models.callback_ext import CallbackAuthorization


def create_authorization(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    inbound_call_id: uuid.UUID,
    app_version: str,
    idempotency_key: str,
) -> tuple[CallbackAuthorization, bool]:
    if not idempotency_key:
        raise UnprocessableError("Idempotency-Key is required", code="IDEMPOTENCY_KEY_MISSING")
    key = command_key(user_id, f"callback:{inbound_call_id}", idempotency_key)
    existing = session.execute(
        scoped_select(CallbackAuthorization, tenant_id).where(
            CallbackAuthorization.idempotency_key == key
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.inbound_call_id != inbound_call_id or existing.user_id != user_id:
            raise ConflictError("Idempotency-Key reused", code="IDEMPOTENCY_KEY_REUSED")
        return existing, False
    now = utcnow()
    auth = CallbackAuthorization(
        tenant_id=tenant_id,
        user_id=user_id,
        inbound_call_id=inbound_call_id,
        app_version=app_version[:40] or "unknown",
        authorized_at=now,
        expires_at=now + timedelta(minutes=AUTHORIZATION_TTL_MINUTES),
        idempotency_key=key,
    )
    session.add(auth)
    session.flush()
    return auth, True


def consume_authorization(
    session: Session, tenant_id: uuid.UUID, authorization_id: uuid.UUID
) -> bool:
    """Atomic compare-and-set: exactly one caller can win, and only before expiry."""
    now = utcnow()
    result = session.execute(
        update(CallbackAuthorization)
        .where(
            CallbackAuthorization.id == authorization_id,
            CallbackAuthorization.tenant_id == tenant_id,
            CallbackAuthorization.consumed_at.is_(None),
            CallbackAuthorization.expires_at > now,
        )
        .values(consumed_at=now)
        .execution_options(synchronize_session="fetch")
    )
    return bool(getattr(result, "rowcount", 0) == 1)


def authorization_facts(
    auth: CallbackAuthorization | None, user_id: uuid.UUID, inbound_call_id: uuid.UUID
) -> AuthorizationFacts | None:
    if auth is None:
        return None
    return AuthorizationFacts(
        matches_user_and_call=auth.user_id == user_id and auth.inbound_call_id == inbound_call_id,
        expires_at=auth.expires_at,
        consumed=auth.consumed_at is not None,
    )
