"""Per-user and instance-wide callback blocklists (both keyed by HMAC, never raw numbers)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_select
from callshield.db.models.callback_ext import CallbackBlocklist, CallbackGlobalBlocklist


def is_blocked_by_user(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, destination_key: str
) -> bool:
    stmt = scoped_select(CallbackBlocklist, tenant_id).where(
        CallbackBlocklist.user_id == user_id,
        CallbackBlocklist.destination_key == destination_key,
    )
    return session.execute(stmt).first() is not None


def is_blocked_by_instance(session: Session, destination_key: str) -> bool:
    stmt = select(CallbackGlobalBlocklist).where(
        CallbackGlobalBlocklist.destination_key == destination_key
    )
    return session.execute(stmt).first() is not None


def block_destination(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    destination_key: str,
    masked: str,
    reason: str,
) -> None:
    """Add to BOTH lists (idempotent). A number blocked here is never dialed by any user."""
    if not is_blocked_by_user(session, tenant_id, user_id, destination_key):
        session.add(
            CallbackBlocklist(
                tenant_id=tenant_id,
                user_id=user_id,
                destination_key=destination_key,
                destination_masked=masked,
                reason=reason,
            )
        )
    if not is_blocked_by_instance(session, destination_key):
        try:
            with session.begin_nested():
                session.add(CallbackGlobalBlocklist(destination_key=destination_key, reason=reason))
        except IntegrityError:
            pass  # another worker blocked it first
    session.flush()
