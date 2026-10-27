"""Case creation and listing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from callshield.audit import record_case_audit
from callshield.auth.scoping import scoped_get_or_404, scoped_select
from callshield.core.errors import ForbiddenError
from callshield.core.idempotency import command_key
from callshield.db.models.defend import ProtectedLine
from callshield.db.models.enforce import Case
from callshield.domain.enums import ActorType, EventOrigin, Role
from callshield.domain.topics import Topic
from callshield.ledger import append_event

OWNER_OVERRIDE_ROLES = frozenset({Role.HOUSEHOLD_ADMIN, Role.OPERATOR})


def _find_by_key(session: Session, tenant_id: uuid.UUID, key: str) -> Case | None:
    stmt = scoped_select(Case, tenant_id).where(Case.idempotency_key == key)
    return session.execute(stmt).scalars().first()


def open_case(
    session: Session,
    tenant_id: uuid.UUID,
    protected_line_id: uuid.UUID,
    title: str,
    *,
    actor: str,
    idempotency_key: str | None = None,
    origin: EventOrigin = EventOrigin.USER_ACTION,
) -> tuple[Case, bool]:
    """Create a case (state OPENED). Same idempotency key returns the original case."""
    if idempotency_key is not None:
        existing = _find_by_key(session, tenant_id, idempotency_key)
        if existing is not None:
            return existing, False
    scoped_get_or_404(session, ProtectedLine, protected_line_id, tenant_id)
    case = Case(
        tenant_id=tenant_id,
        protected_line_id=protected_line_id,
        title=title[:300],
        idempotency_key=idempotency_key,
    )
    session.add(case)
    session.flush()
    append_event(
        session,
        tenant_id,
        Topic.CASE_CREATED,
        {"case_id": str(case.id), "protected_line_id": str(protected_line_id)},
        origin,
        f"case-created:{case.id}",
    )
    record_case_audit(
        session,
        tenant_id,
        case.id,
        ActorType.SYSTEM if actor == "system" else ActorType.USER,
        "case.opened",
        {"title": case.title},
        actor_id=actor,
    )
    return case, True


def authorize_line_owner(
    session: Session, tenant_id: uuid.UUID, line_id: uuid.UUID, user_id: uuid.UUID, role: Role
) -> ProtectedLine:
    """Line must be in the tenant; if it has an owner, only that owner or an admin may act."""
    line = scoped_get_or_404(session, ProtectedLine, line_id, tenant_id)
    if line.owner_user_id and line.owner_user_id != user_id and role not in OWNER_OVERRIDE_ROLES:
        raise ForbiddenError("not the protected line owner", code="NOT_LINE_OWNER")
    return line


def user_idempotency_key(user_id: uuid.UUID, client_key: str | None) -> str | None:
    return command_key(user_id, "case.create", client_key) if client_key else None


@dataclass
class CasePage:
    items: list[Case]
    total: int
    limit: int
    offset: int


def list_cases(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    states: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> CasePage:
    stmt = scoped_select(Case, tenant_id)
    if states:
        stmt = stmt.where(Case.state.in_(states))
    total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    page = stmt.order_by(Case.opened_at.desc(), Case.id).limit(limit).offset(offset)
    return CasePage(list(session.execute(page).scalars()), total, limit, offset)
