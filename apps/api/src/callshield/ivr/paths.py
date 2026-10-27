"""Persistence for user-approved IVR paths (`ivr_paths`). Approval is a user action."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.core.clock import utcnow
from callshield.core.errors import UnprocessableError
from callshield.db.models.resolve import IvrPath
from callshield.ivr.navigator import ApprovedPath, PathStep

_VALID_DIGITS = frozenset("0123456789*#")
MAX_PATH_STEPS = 6
_FALLBACKS = frozenset({"ABORT", "HANDOFF_TO_USER"})


def validate_steps(steps: tuple[PathStep, ...]) -> None:
    if not steps or len(steps) > MAX_PATH_STEPS:
        raise UnprocessableError(f"path must have 1..{MAX_PATH_STEPS} steps", code="BAD_IVR_PATH")
    for step in steps:
        if len(step.digit) != 1 or step.digit not in _VALID_DIGITS:
            raise UnprocessableError("each step must be a single DTMF digit", code="BAD_IVR_PATH")


def approve_path(
    session: Session,
    tenant_id: uuid.UUID,
    destination_id: uuid.UUID,
    steps: tuple[PathStep, ...],
    *,
    timeout_seconds: int = 60,
    fallback: str = "ABORT",
) -> IvrPath:
    """Append a new user-approved version (older versions are kept)."""
    validate_steps(steps)
    if fallback not in _FALLBACKS or not 5 <= timeout_seconds <= 300:
        raise UnprocessableError("bad fallback or timeout", code="BAD_IVR_PATH")
    latest = _latest_row(session, tenant_id, destination_id, approved_only=False)
    row = IvrPath(
        tenant_id=tenant_id,
        destination_id=destination_id,
        version=(latest.version + 1) if latest else 1,
        choices=ApprovedPath(steps).to_choices(),
        user_approved=True,
        confidence=0.5,
        timeout_seconds=timeout_seconds,
        fallback=fallback,
    )
    session.add(row)
    session.flush()
    return row


def _latest_row(
    session: Session, tenant_id: uuid.UUID, destination_id: uuid.UUID, *, approved_only: bool
) -> IvrPath | None:
    stmt = select(IvrPath).where(
        IvrPath.tenant_id == tenant_id, IvrPath.destination_id == destination_id
    )
    if approved_only:
        stmt = stmt.where(IvrPath.user_approved.is_(True))
    return session.execute(stmt.order_by(IvrPath.version.desc()).limit(1)).scalar_one_or_none()


def load_approved_path(
    session: Session, tenant_id: uuid.UUID, destination_id: uuid.UUID
) -> ApprovedPath | None:
    row = _latest_row(session, tenant_id, destination_id, approved_only=True)
    if row is None:
        return None
    return ApprovedPath(
        steps=ApprovedPath.steps_from_choices(row.choices),
        version=row.version,
        confidence=row.confidence,
        last_tested_at=row.last_tested_at,
        timeout_seconds=row.timeout_seconds,
        fallback=row.fallback,
        path_id=str(row.id),
    )


def record_path_result(session: Session, tenant_id: uuid.UUID, path_id: str, *, ok: bool) -> None:
    """Update validation metadata after a run (metadata only; choices are never edited)."""
    row = session.execute(
        select(IvrPath).where(IvrPath.tenant_id == tenant_id, IvrPath.id == uuid.UUID(path_id))
    ).scalar_one_or_none()
    if row is None:
        return
    row.last_tested_at = utcnow()
    row.confidence = min(1.0, row.confidence + 0.2) if ok else row.confidence * 0.5
