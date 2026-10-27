"""Line ownership verification (D-001): challenge code via a user-controlled channel.

The channel abstraction delivers the code to something only the line owner controls (a
call/SMS to the number itself, a SIP device registered by the user). The code is stored
only as a salted hash, expires, and is attempt-limited. Until VERIFIED a line is not
routable.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.audit import record_audit
from callshield.core.clock import utcnow
from callshield.core.config import Settings
from callshield.core.errors import ApiError, ConflictError, PolicyBlockedError
from callshield.db.models.defend import ProtectedLine
from callshield.db.models.defend_ext import LineVerificationChallenge
from callshield.domain.enums import EventOrigin, LineVerificationStatus
from callshield.domain.topics import Topic
from callshield.ledger import append_event

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5


class ChallengeChannel(Protocol):
    name: str

    def deliver(self, line_id: uuid.UUID, destination: str, code: str) -> None:
        """Send `code` to the line owner. Must not log the code or the raw destination."""
        ...


class InMemoryOutboxChannel:
    """Dev/test channel: keeps codes in memory so tests and local tooling can read them."""

    name = "memory-outbox"

    def __init__(self) -> None:
        self.outbox: list[tuple[uuid.UUID, str]] = []

    def deliver(self, line_id: uuid.UUID, destination: str, code: str) -> None:
        self.outbox.append((line_id, code))

    def last_code(self, line_id: uuid.UUID) -> str | None:
        codes = [c for lid, c in self.outbox if lid == line_id]
        return codes[-1] if codes else None


_channel: ChallengeChannel | None = None


def set_challenge_channel(channel: ChallengeChannel | None) -> None:
    global _channel
    _channel = channel


def get_challenge_channel(settings: Settings) -> ChallengeChannel:
    global _channel
    if _channel is None:
        if settings.environment not in ("dev", "test"):
            raise PolicyBlockedError(
                "no verification channel configured", code="VERIFICATION_CHANNEL_MISSING"
            )
        _channel = InMemoryOutboxChannel()
    return _channel


def _hash_code(line_id: uuid.UUID, code: str) -> str:
    return hashlib.sha256(f"{line_id}:{code}".encode()).hexdigest()


def start_challenge(
    session: Session, settings: Settings, line: ProtectedLine, actor: str
) -> LineVerificationChallenge:
    if line.verification_status == LineVerificationStatus.VERIFIED.value:
        raise ConflictError("line already verified", code="ALREADY_VERIFIED")
    channel = get_challenge_channel(settings)
    code = f"{secrets.randbelow(10**6):06d}"
    challenge = LineVerificationChallenge(
        tenant_id=line.tenant_id,
        line_id=line.id,
        code_hash=_hash_code(line.id, code),
        channel=channel.name,
        expires_at=utcnow() + CODE_TTL,
    )
    session.add(challenge)
    line.verification_status = LineVerificationStatus.PENDING.value
    channel.deliver(line.id, line.e164, code)
    record_audit(
        session, line.tenant_id, actor, "line.verification_started", {"line_id": str(line.id)}
    )
    return challenge


def _latest_open(session: Session, line: ProtectedLine) -> LineVerificationChallenge | None:
    stmt = (
        select(LineVerificationChallenge)
        .where(
            LineVerificationChallenge.tenant_id == line.tenant_id,
            LineVerificationChallenge.line_id == line.id,
            LineVerificationChallenge.consumed_at.is_(None),
        )
        .order_by(LineVerificationChallenge.created_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def confirm_challenge(session: Session, line: ProtectedLine, code: str, actor: str) -> None:
    """Raise ApiError on any failure; mark the line VERIFIED on success."""
    challenge = _latest_open(session, line)
    if challenge is None or challenge.expires_at < utcnow():
        raise ApiError("no active challenge; request a new code", code="CHALLENGE_EXPIRED")
    if challenge.attempts >= MAX_ATTEMPTS:
        raise ApiError("too many attempts; request a new code", code="CHALLENGE_LOCKED")
    challenge.attempts += 1
    if not hmac.compare_digest(challenge.code_hash, _hash_code(line.id, code.strip())):
        session.commit()  # persist the attempt counter even though the request fails
        raise ApiError("incorrect code", code="CHALLENGE_FAILED", status_code=400)
    challenge.consumed_at = utcnow()
    line.verification_status = LineVerificationStatus.VERIFIED.value
    line.verified_at = utcnow()
    record_audit(session, line.tenant_id, actor, "line.verified", {"line_id": str(line.id)})
    append_event(
        session,
        line.tenant_id,
        Topic.LINE_VERIFIED,
        {"line_id": str(line.id)},
        EventOrigin.USER_ACTION,
        f"line-verified:{line.id}",
    )
