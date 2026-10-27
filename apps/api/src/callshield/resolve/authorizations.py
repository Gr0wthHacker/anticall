"""Contact authorization lifecycle: a separate, explicit, single-use user action (PRD 8.1)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from callshield.audit import actor_for_user, record_audit
from callshield.auth.scoping import scoped_get, scoped_select
from callshield.core.clock import utcnow
from callshield.core.config import Settings
from callshield.core.errors import ConflictError, PolicyBlockedError, UnprocessableError
from callshield.core.idempotency import command_key
from callshield.db.models.resolve import ContactAuthorization, ContactDestination, ResolutionCase
from callshield.domain.enums import ContactScope, ResolutionState, Role
from callshield.domain.topics import Topic
from callshield.resolve import cases, machine
from callshield.resolve.events import add_timeline, emit
from callshield.resolve.policy import ALLOWED_ROLES, Reason, destination_reasons

MIN_TTL_SECONDS = 30
DEFAULT_TTL_SECONDS = 900
MAX_TTL_SECONDS = 3600


def refuse(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
    action: str,
    reasons: list[Reason],
) -> PolicyBlockedError:
    """Audit + persist a refusal (committed so it survives the request rollback), then
    return the error for the caller to raise."""
    actor = actor_for_user(user_id)
    codes = [r.code for r in reasons]
    record_audit(
        session, tenant_id, actor, f"{action}.refused", {"case_id": str(case_id), "reasons": codes}
    )
    add_timeline(session, tenant_id, case_id, f"{action}.refused", actor, {"reasons": codes})
    emit(
        session,
        tenant_id,
        Topic.CONTACT_BLOCKED,
        {"case_id": str(case_id), "reasons": codes},
        ("contact.blocked", action, case_id, user_id, uuid.uuid4().hex[:12]),
    )
    session.commit()
    return PolicyBlockedError(
        "; ".join(r.message for r in reasons),
        code=codes[0] if codes else "POLICY_BLOCKED",
        details={"reasons": [r.as_dict() for r in reasons]},
    )


def _actor_reasons(case: ResolutionCase, user_id: uuid.UUID, role: Role) -> list[Reason]:
    out: list[Reason] = []
    if role not in ALLOWED_ROLES:
        out.append(Reason("ROLE_NOT_PERMITTED", "This role may not authorize outbound contact."))
    if case.owner_user_id != user_id:
        out.append(Reason("NOT_CASE_OWNER", "Only the case owner may authorize contact."))
    return out


def create_authorization(
    session: Session,
    settings: Settings,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    role: Role,
    case: ResolutionCase,
    destination_id: uuid.UUID,
    *,
    scope: ContactScope,
    ttl_seconds: int,
    idempotency_key: str,
    basis: str = "",
) -> tuple[ContactAuthorization, bool]:
    """Create (or replay) ONE single-use authorization. Returns (authorization, created)."""
    if not idempotency_key:
        raise UnprocessableError("Idempotency-Key is required", code="IDEMPOTENCY_KEY_MISSING")
    if not MIN_TTL_SECONDS <= ttl_seconds <= MAX_TTL_SECONDS:
        raise UnprocessableError(
            f"ttl_seconds must be {MIN_TTL_SECONDS}..{MAX_TTL_SECONDS}", code="BAD_TTL"
        )
    key = command_key(user_id, f"authorize:{case.id}", idempotency_key)
    existing = session.execute(
        scoped_select(ContactAuthorization, tenant_id).where(
            ContactAuthorization.idempotency_key == key
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.case_id != case.id or existing.destination_id != destination_id:
            raise ConflictError(
                "Idempotency-Key reused with a different request", code="IDEMPOTENCY_KEY_REUSED"
            )
        return existing, False
    reasons = _authorization_blockers(
        session, settings, tenant_id, user_id, role, case, destination_id
    )
    if reasons:
        raise refuse(session, tenant_id, user_id, case.id, "resolve.authorization", reasons)
    now = utcnow()
    auth = ContactAuthorization(
        tenant_id=tenant_id,
        case_id=case.id,
        destination_id=destination_id,
        user_id=user_id,
        scope=scope.value,
        authorization_basis=basis[:200] or "explicit user action",
        expires_at=now + timedelta(seconds=ttl_seconds),
        idempotency_key=key,
    )
    session.add(auth)
    session.flush()
    actor = actor_for_user(user_id)
    ctx = {"destination_independently_verified": True, "user_authorization_id": str(auth.id)}
    machine.advance_to(
        session, case, ResolutionState.USER_APPROVED_CONTACT, actor=actor, context=ctx
    )
    payload = {
        "case_id": str(case.id),
        "authorization_id": str(auth.id),
        "destination_id": str(destination_id),
        "scope": scope.value,
        "expires_at": auth.expires_at.isoformat(),
    }
    add_timeline(session, tenant_id, case.id, "contact.authorized", actor, payload)
    emit(session, tenant_id, Topic.CONTACT_AUTHORIZED, payload, ("contact.authorized", auth.id))
    record_audit(session, tenant_id, actor, "resolve.contact.authorized", payload)
    return auth, True


def _authorization_blockers(
    session: Session,
    settings: Settings,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    role: Role,
    case: ResolutionCase,
    destination_id: uuid.UUID,
) -> list[Reason]:
    reasons = _actor_reasons(case, user_id, role)
    if not settings.outbound_enabled:
        reasons.append(Reason("OUTBOUND_DISABLED", "Outbound calling is disabled."))
    dest = scoped_get(session, ContactDestination, destination_id, tenant_id)
    facts = cases.destination_facts(session, tenant_id, dest) if dest else None
    reasons += destination_reasons(facts, case.id)
    open_stmt = scoped_select(ContactAuthorization, tenant_id).where(
        ContactAuthorization.case_id == case.id,
        ContactAuthorization.consumed_at.is_(None),
        ContactAuthorization.expires_at > utcnow(),
    )
    if session.execute(open_stmt).first() is not None:
        reasons.append(
            Reason(
                "OPEN_AUTHORIZATION_EXISTS", "An unused authorization already exists for this case."
            )
        )
    return reasons


def consume_authorization(
    session: Session, tenant_id: uuid.UUID, authorization_id: uuid.UUID
) -> bool:
    """Atomic single-use consumption: exactly one caller can win."""
    now = utcnow()
    result = session.execute(
        update(ContactAuthorization)
        .where(
            ContactAuthorization.id == authorization_id,
            ContactAuthorization.tenant_id == tenant_id,
            ContactAuthorization.consumed_at.is_(None),
            ContactAuthorization.expires_at > now,
        )
        .values(consumed_at=now)
        .execution_options(synchronize_session="fetch")
    )
    return bool(getattr(result, "rowcount", 0) == 1)


def revoke_open_authorizations(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID) -> int:
    count = 0
    stmt = select(ContactAuthorization).where(
        ContactAuthorization.tenant_id == tenant_id,
        ContactAuthorization.case_id == case_id,
        ContactAuthorization.consumed_at.is_(None),
        ContactAuthorization.expires_at > utcnow(),
    )
    for auth in session.execute(stmt).scalars():
        auth.expires_at = utcnow()
        count += 1
    return count
