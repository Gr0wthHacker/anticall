"""DB-backed job queue (lean default; Redis is an optional later backend)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from callshield.core.clock import utcnow
from callshield.db.models.platform import Job
from callshield.domain.enums import JobState

BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 3600
LOCK_TIMEOUT_SECONDS = 600


def backoff_seconds(attempts: int) -> int:
    return min(BACKOFF_BASE_SECONDS * int(2 ** max(attempts - 1, 0)), BACKOFF_CAP_SECONDS)


def _find_by_key(session: Session, tenant_id: uuid.UUID, key: str) -> Job | None:
    stmt = select(Job).where(Job.tenant_id == tenant_id, Job.idempotency_key == key)
    return session.execute(stmt).scalar_one_or_none()


def enqueue(
    session: Session,
    tenant_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
    run_after: datetime | None = None,
    max_attempts: int = 5,
) -> tuple[Job, bool]:
    """Enqueue a job. Same (tenant, idempotency_key) returns the existing job, created=False."""
    key = idempotency_key or f"auto:{uuid.uuid4()}"
    existing = _find_by_key(session, tenant_id, key)
    if existing is not None:
        return existing, False
    job = Job(
        tenant_id=tenant_id,
        kind=kind,
        payload=payload,
        idempotency_key=key,
        run_after=run_after or utcnow(),
        max_attempts=max_attempts,
    )
    try:
        with session.begin_nested():
            session.add(job)
    except IntegrityError:
        winner = _find_by_key(session, tenant_id, key)
        if winner is None:
            raise
        return winner, False
    return job, True


def _claim_candidate(session: Session, kinds: list[str] | None, now: datetime) -> Job | None:
    stale = now - timedelta(seconds=LOCK_TIMEOUT_SECONDS)
    runnable = (Job.state == JobState.QUEUED.value) & (Job.run_after <= now)
    crashed = (Job.state == JobState.RUNNING.value) & (Job.locked_at <= stale)
    stmt = select(Job).where(runnable | crashed).order_by(Job.run_after, Job.created_at).limit(1)
    if kinds:
        stmt = stmt.where(Job.kind.in_(kinds))
    if session.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    return session.execute(stmt).scalar_one_or_none()


def claim_next(
    session: Session, kinds: list[str] | None = None, now: datetime | None = None
) -> Job | None:
    """Atomically move one runnable job to RUNNING (compare-and-set on state)."""
    now = now or utcnow()
    candidate = _claim_candidate(session, kinds, now)
    if candidate is None:
        return None
    previous_state, previous_lock = candidate.state, candidate.locked_at
    stmt = (
        update(Job)
        .where(Job.id == candidate.id, Job.state == previous_state)
        .where(Job.locked_at == previous_lock if previous_lock else Job.locked_at.is_(None))
        .values(state=JobState.RUNNING.value, locked_at=now, attempts=Job.attempts + 1)
    )
    if session.execute(stmt).rowcount != 1:  # type: ignore[attr-defined]
        return None  # lost the race; caller polls again
    session.refresh(candidate)
    return candidate


def complete(session: Session, job: Job) -> None:
    job.state = JobState.SUCCEEDED.value
    job.completed_at = utcnow()
    job.locked_at = None
    job.last_error = None


def fail(session: Session, job: Job, error: str, now: datetime | None = None) -> None:
    """Retry with exponential backoff, or mark DEAD once attempts are exhausted."""
    now = now or utcnow()
    job.last_error = error[:1000]
    job.locked_at = None
    if job.attempts >= job.max_attempts:
        job.state = JobState.DEAD.value
        return
    job.state = JobState.QUEUED.value
    job.run_after = now + timedelta(seconds=backoff_seconds(job.attempts))


Handler = Callable[[Session, Job], None]
_HANDLERS: dict[str, Handler] = {}


def job_handler(kind: str) -> Callable[[Handler], Handler]:
    """Register a handler: @job_handler("case.recompute") def run(session, job): ..."""

    def decorator(func: Handler) -> Handler:
        if kind in _HANDLERS and _HANDLERS[kind] is not func:
            raise ValueError(f"duplicate job handler for {kind!r}")
        _HANDLERS[kind] = func
        return func

    return decorator


def get_handler(kind: str) -> Handler | None:
    return _HANDLERS.get(kind)


def registered_kinds() -> list[str]:
    return sorted(_HANDLERS)
