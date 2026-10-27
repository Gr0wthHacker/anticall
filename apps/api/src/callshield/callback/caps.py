"""Attempt caps, enforced in the dialer service (never in the UI).

Per inbound call, per destination (30d), per user (24h + 10 min spacing) come from the
tenant-scoped `callback_attempts`. The instance-wide per-destination cap crosses tenants and
therefore reads `callback_global_attempts`, which holds only (HMAC destination key, time);
see `callback.keys` for why the raw number is never stored there.

An attempt counts when the dial STARTS (answered or not). `record_attempt` re-checks every
cap under a lock and relies on unique constraints, so a bypassed queue step or a race cannot
produce a third attempt.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from callshield.callback.config import NUMBER_WINDOW_DAYS, EffectiveSettings
from callshield.callback.gates import CapLimits, CapsFacts, cap_failure
from callshield.core.errors import PolicyBlockedError
from callshield.db.models.callback_ext import CallbackAttempt, CallbackGlobalAttempt


def limits_for(eff: EffectiveSettings) -> CapLimits:
    return CapLimits(
        eff.max_attempts_per_inbound, eff.max_attempts_per_number_30d, eff.max_attempts_per_user_day
    )


def _count(session: Session, *where: object) -> int:
    stmt = select(func.count()).select_from(CallbackAttempt).where(*where)  # type: ignore[arg-type]
    return int(session.execute(stmt).scalar_one())


def caps_snapshot(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    inbound_call_id: uuid.UUID,
    destination_key: str,
    now: datetime,
) -> CapsFacts:
    scope = (CallbackAttempt.tenant_id == tenant_id,)
    month = now - timedelta(days=NUMBER_WINDOW_DAYS)
    day = now - timedelta(hours=24)
    user = (*scope, CallbackAttempt.user_id == user_id)
    last = session.execute(
        select(func.max(CallbackAttempt.started_at)).where(*user)
    ).scalar_one_or_none()
    instance = session.execute(
        select(func.count())
        .select_from(CallbackGlobalAttempt)
        .where(
            CallbackGlobalAttempt.destination_key == destination_key,
            CallbackGlobalAttempt.attempted_at > month,
        )
    ).scalar_one()
    return CapsFacts(
        inbound_attempts=_count(
            session, *scope, CallbackAttempt.inbound_call_id == inbound_call_id
        ),
        number_attempts_30d=_count(
            session,
            *user,
            CallbackAttempt.destination_key == destination_key,
            CallbackAttempt.started_at > month,
        ),
        user_attempts_24h=_count(session, *user, CallbackAttempt.started_at > day),
        last_user_attempt_at=last,
        instance_attempts_30d=int(instance),
    )


def _lock_destination(session: Session, destination_key: str) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"callback:{destination_key}"}
        )


def record_attempt(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    request_id: uuid.UUID,
    inbound_call_id: uuid.UUID,
    destination_key: str,
    eff: EffectiveSettings,
    now: datetime,
) -> CallbackAttempt:
    """Count the attempt (and re-enforce every cap). Raises PolicyBlockedError if over."""
    _lock_destination(session, destination_key)
    facts = caps_snapshot(session, tenant_id, user_id, inbound_call_id, destination_key, now)
    failure = cap_failure(facts, limits_for(eff), now)
    if failure is not None:
        raise PolicyBlockedError(failure[1], code=failure[0])
    attempt = CallbackAttempt(
        tenant_id=tenant_id,
        request_id=request_id,
        user_id=user_id,
        inbound_call_id=inbound_call_id,
        attempt_no=facts.inbound_attempts + 1,
        destination_key=destination_key,
        started_at=now,
    )
    try:
        with session.begin_nested():
            session.add(attempt)
            session.add(CallbackGlobalAttempt(destination_key=destination_key, attempted_at=now))
    except IntegrityError as exc:  # concurrent attempt took the slot
        raise PolicyBlockedError(
            "The callback limit for this call has been reached.", code="CAP_PER_INBOUND_CALL"
        ) from exc
    return attempt
