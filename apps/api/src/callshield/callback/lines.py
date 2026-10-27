"""Caller ID rules: the user's OWN verified protected line, nothing else.

A callback may be placed only from a line that is (1) VERIFIED, (2) has a passed outbound
ownership check (SMS/voice code, hashed at rest) and (3) has a provider-reported outbound
A-attestation capability in `line_outbound_attestation`. There is no parameter, field, header
or config key that sets a caller ID: `VerifiedCaller` is only ever built here, from the line row.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.audit import record_audit
from callshield.auth.scoping import scoped_get, scoped_select
from callshield.callback.dialer_port import VerifiedCaller
from callshield.core.clock import utcnow
from callshield.core.errors import ApiError, NotFoundError, PolicyBlockedError
from callshield.db.models.callback_ext import LineOutboundAttestation, LineOutboundOwnershipCheck
from callshield.db.models.defend import ProtectedLine
from callshield.defend.verification import ChallengeChannel
from callshield.domain.enums import LineVerificationStatus

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5


def _hash(line_id: uuid.UUID, code: str) -> str:
    return hashlib.sha256(f"callback-ownership:{line_id}:{code}".encode()).hexdigest()


def get_line_or_404(session: Session, tenant_id: uuid.UUID, line_id: uuid.UUID) -> ProtectedLine:
    line = scoped_get(session, ProtectedLine, line_id, tenant_id)
    if line is None:
        raise NotFoundError("line not found", code="NOT_FOUND")
    return line


def start_ownership_check(
    session: Session, line: ProtectedLine, channel: ChallengeChannel, actor: str
) -> LineOutboundOwnershipCheck:
    code = f"{secrets.randbelow(10**6):06d}"
    check = LineOutboundOwnershipCheck(
        tenant_id=line.tenant_id,
        line_id=line.id,
        code_hash=_hash(line.id, code),
        channel=channel.name,
        expires_at=utcnow() + CODE_TTL,
    )
    session.add(check)
    channel.deliver(line.id, line.e164, code)  # never logged
    record_audit(
        session,
        line.tenant_id,
        actor,
        "callback.ownership_check_started",
        {"line_id": str(line.id)},
    )
    session.flush()
    return check


def _latest_pending(session: Session, line: ProtectedLine) -> LineOutboundOwnershipCheck | None:
    stmt = (
        scoped_select(LineOutboundOwnershipCheck, line.tenant_id)
        .where(
            LineOutboundOwnershipCheck.line_id == line.id,
            LineOutboundOwnershipCheck.verified_at.is_(None),
        )
        .order_by(LineOutboundOwnershipCheck.created_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def confirm_ownership_check(session: Session, line: ProtectedLine, code: str, actor: str) -> None:
    check = _latest_pending(session, line)
    if check is None or check.expires_at < utcnow():
        raise ApiError("no active ownership check; request a new code", code="CHALLENGE_EXPIRED")
    if check.attempts >= MAX_ATTEMPTS:
        raise ApiError("too many attempts; request a new code", code="CHALLENGE_LOCKED")
    check.attempts += 1
    if not hmac.compare_digest(check.code_hash, _hash(line.id, code.strip())):
        session.commit()  # persist the attempt counter even though the request fails
        raise ApiError("incorrect code", code="CHALLENGE_FAILED", status_code=400)
    check.verified_at = utcnow()
    record_audit(
        session, line.tenant_id, actor, "callback.ownership_check_passed", {"line_id": str(line.id)}
    )


def record_outbound_attestation(
    session: Session, tenant_id: uuid.UUID, line_id: uuid.UUID, level: str, source: str
) -> LineOutboundAttestation:
    """Provider-reported capability (called by the telephony adapter, not by users)."""
    normalized = level.strip().upper()
    if normalized not in {"A", "B", "C", "UNKNOWN"}:
        normalized = "UNKNOWN"
    row = session.execute(
        scoped_select(LineOutboundAttestation, tenant_id).where(
            LineOutboundAttestation.line_id == line_id
        )
    ).scalar_one_or_none()
    if row is None:
        row = LineOutboundAttestation(
            tenant_id=tenant_id, line_id=line_id, level=normalized, source=source[:64]
        )
        session.add(row)
    else:
        row.level, row.source, row.recorded_at = normalized, source[:64], utcnow()
    session.flush()
    return row


def caller_id_problem(session: Session, tenant_id: uuid.UUID, line: ProtectedLine) -> str | None:
    """Reason code if this line may not be used as a callback caller ID, else None."""
    if line.verification_status != LineVerificationStatus.VERIFIED.value:
        return "LINE_NOT_VERIFIED"
    owned = session.execute(
        select(LineOutboundOwnershipCheck.id).where(
            LineOutboundOwnershipCheck.tenant_id == tenant_id,
            LineOutboundOwnershipCheck.line_id == line.id,
            LineOutboundOwnershipCheck.verified_at.is_not(None),
        )
    ).first()
    if owned is None:
        return "OWNERSHIP_CHECK_MISSING"
    attestation = session.execute(
        scoped_select(LineOutboundAttestation, tenant_id).where(
            LineOutboundAttestation.line_id == line.id
        )
    ).scalar_one_or_none()
    if attestation is None or attestation.level != "A":
        return "OUTBOUND_ATTESTATION_NOT_A"
    return None


def verified_caller(session: Session, tenant_id: uuid.UUID, line: ProtectedLine) -> VerifiedCaller:
    problem = caller_id_problem(session, tenant_id, line)
    if problem is not None:
        raise PolicyBlockedError(
            "This line cannot be used for callbacks.", code=problem, details={"reason": problem}
        )
    return VerifiedCaller(line_id=line.id, e164=line.e164)
