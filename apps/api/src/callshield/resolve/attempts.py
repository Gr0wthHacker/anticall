"""Attempt records: rate-control queries, user-attested manual attempts, review (R-008, R-012)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from callshield.audit import actor_for_user, record_audit
from callshield.auth.scoping import scoped_get_or_404, scoped_select
from callshield.core.clock import utcnow
from callshield.core.errors import ConflictError, NotFoundError, UnprocessableError
from callshield.core.idempotency import command_key
from callshield.core.phone import mask_phone
from callshield.db.models.resolve import ContactAttempt, ContactDestination, ResolutionCase
from callshield.db.models.resolve_ext import ContactAttemptDetail
from callshield.domain.enums import ContactOutcome, EvidenceLabel
from callshield.domain.topics import Topic
from callshield.resolve import cases
from callshield.resolve.events import add_timeline, emit

SOURCE_SYSTEM = "SYSTEM"
SOURCE_MANUAL = "USER_MANUAL"
SOURCE_HISTORIC = "USER_HISTORIC"
REVIEW_STATES = frozenset({"CONFIRMED", "CORRECTED", "REJECTED"})
_FUTURE_SLACK = timedelta(minutes=5)


def _system_attempts(tenant_id: uuid.UUID) -> Any:
    return (
        select(ContactAttempt)
        .join(ContactAttemptDetail, ContactAttemptDetail.attempt_id == ContactAttempt.id)
        .where(
            ContactAttempt.tenant_id == tenant_id,
            ContactAttemptDetail.source == SOURCE_SYSTEM,
            ContactAttempt.started_at.is_not(None),
        )
    )


def count_system_attempts(
    session: Session, tenant_id: uuid.UUID, since: datetime, case_id: uuid.UUID | None = None
) -> int:
    stmt = _system_attempts(tenant_id).where(ContactAttempt.started_at >= since)
    if case_id is not None:
        stmt = stmt.where(ContactAttempt.case_id == case_id)
    return int(session.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0)


def last_system_attempt_time(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> datetime | None:
    """Latest start/end time of any system-placed attempt on the case (cooldown anchor)."""
    rows = session.execute(
        _system_attempts(tenant_id).where(ContactAttempt.case_id == case_id)
    ).scalars()
    times = [t for a in rows for t in (a.started_at, a.ended_at) if t is not None]
    return max(times) if times else None


def active_attempt_exists(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    case_id: uuid.UUID | None = None,
    phone: str | None = None,
) -> bool:
    stmt = _system_attempts(tenant_id).where(ContactAttempt.ended_at.is_(None))
    if case_id is not None:
        stmt = stmt.where(ContactAttempt.case_id == case_id)
    if phone is not None:
        stmt = stmt.join(
            ContactDestination, ContactDestination.id == ContactAttempt.destination_id
        ).where(ContactDestination.phone == phone)
    return session.execute(stmt.limit(1)).first() is not None


def get_attempt(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID, attempt_id: uuid.UUID
) -> ContactAttempt:
    attempt = scoped_get_or_404(session, ContactAttempt, attempt_id, tenant_id)
    if attempt.case_id != case_id:
        raise NotFoundError("ContactAttempt not found", code="NOT_FOUND")
    return attempt


def log_manual_attempt(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case: ResolutionCase,
    destination_id: uuid.UUID,
    *,
    outcome: ContactOutcome,
    idempotency_key: str,
    occurred_at: datetime | None = None,
    duration_seconds: int | None = None,
    notes: str = "",
    historic: bool = False,
) -> tuple[ContactAttempt, bool]:
    """R-012: an attempt the user made outside the system. USER_ATTESTED only: no
    recording, no transcript, no fabricated metadata. Does not count toward system caps."""
    if not idempotency_key:
        raise UnprocessableError("Idempotency-Key is required", code="IDEMPOTENCY_KEY_MISSING")
    if outcome is ContactOutcome.VERIFIED_RESOLVED:
        raise UnprocessableError(
            "resolution cannot be attested without independent evidence",
            code="OUTCOME_NOT_ATTESTABLE",
        )
    now = utcnow()
    if occurred_at is not None and occurred_at > now + _FUTURE_SLACK:
        raise UnprocessableError("occurred_at is in the future", code="BAD_TIME")
    dest = cases.get_destination(session, tenant_id, case.id, destination_id)
    key = command_key(user_id, f"manual:{case.id}", idempotency_key)
    existing = session.execute(
        scoped_select(ContactAttempt, tenant_id).where(ContactAttempt.idempotency_key == key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    started = occurred_at or now
    ended = started + timedelta(seconds=duration_seconds) if duration_seconds else None
    claim = {"type": "USER_ATTESTATION", "label": str(EvidenceLabel.USER_ATTESTED), "text": notes}
    attempt = ContactAttempt(
        tenant_id=tenant_id,
        case_id=case.id,
        destination_id=dest.id,
        reason="user-logged attempt made outside the system",
        outcome=outcome.value,
        started_at=started,
        ended_at=ended,
        ivr_route=[],
        agent_claims=[claim],
        policy_state={
            "source": "USER_HISTORIC" if historic else "USER_MANUAL",
            "caps_apply": False,
            "note": "attested by user; not verified",
        },
        mode="HUMAN",
        review_state="UNREVIEWED",
        idempotency_key=key,
    )
    session.add(attempt)
    session.flush()
    session.add(
        ContactAttemptDetail(
            tenant_id=tenant_id,
            attempt_id=attempt.id,
            case_id=case.id,
            source=SOURCE_HISTORIC if historic else SOURCE_MANUAL,
            evidence_label=EvidenceLabel.USER_ATTESTED.value,
            dialed_phone_masked=mask_phone(dest.phone),
            occurred_at=started,
            duration_seconds=duration_seconds,
            notes=notes,
        )
    )
    actor = actor_for_user(user_id)
    payload = {
        "case_id": str(case.id),
        "attempt_id": str(attempt.id),
        "outcome": outcome.value,
        "label": "USER_ATTESTED",
        "historic": historic,
    }
    add_timeline(session, tenant_id, case.id, "attempt.manual_logged", actor, payload, attempt.id)
    emit(session, tenant_id, Topic.CONTACT_ENDED, payload, ("contact.ended", "manual", attempt.id))
    record_audit(session, tenant_id, actor, "resolve.attempt.manual_logged", payload)
    return attempt, True


def import_historic_attempts(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case: ResolutionCase,
    destination_id: uuid.UUID,
    items: list[dict[str, Any]],
    *,
    batch_key: str,
) -> list[ContactAttempt]:
    """Import many past attempts (e.g. 'eleven personal attempts') as USER_ATTESTED.
    Each item is a separate attempt row; nothing is fabricated or capped."""
    out: list[ContactAttempt] = []
    for index, item in enumerate(items):
        attempt, _ = log_manual_attempt(
            session,
            tenant_id,
            user_id,
            case,
            destination_id,
            outcome=ContactOutcome(item.get("outcome", ContactOutcome.UNRESOLVED)),
            idempotency_key=f"{batch_key}:{index}",
            occurred_at=item.get("occurred_at"),
            duration_seconds=item.get("duration_seconds"),
            notes=str(item.get("notes", "")),
            historic=True,
        )
        out.append(attempt)
    return out


def review_attempt(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case: ResolutionCase,
    attempt: ContactAttempt,
    *,
    review_state: str,
    notes: str = "",
    corrected_outcome: ContactOutcome | None = None,
) -> ContactAttempt:
    """User review. The original outcome is never overwritten; a correction is appended."""
    if review_state not in REVIEW_STATES:
        raise UnprocessableError(
            "review_state must be CONFIRMED, CORRECTED or REJECTED", code="BAD_REVIEW_STATE"
        )
    if review_state == "CORRECTED" and corrected_outcome is None:
        raise ConflictError("corrected_outcome is required for CORRECTED", code="CORRECTION_NEEDED")
    attempt.review_state = review_state
    actor = actor_for_user(user_id)
    payload = {
        "case_id": str(case.id),
        "attempt_id": str(attempt.id),
        "review_state": review_state,
        "original_outcome": attempt.outcome,
        "notes": notes,
        "corrected_outcome": corrected_outcome.value if corrected_outcome else None,
    }
    add_timeline(session, tenant_id, case.id, "attempt.reviewed", actor, payload, attempt.id)
    emit(
        session,
        tenant_id,
        Topic.ATTEMPT_REVIEWED,
        payload,
        ("attempt.reviewed", attempt.id, uuid.uuid4().hex[:12]),
    )
    record_audit(session, tenant_id, actor, "resolve.attempt.reviewed", payload)
    return attempt
