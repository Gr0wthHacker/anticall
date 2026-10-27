"""FastAPI auth dependencies: current_principal, require_role, tenant_session."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from callshield.auth.scoping import TenantScopedRepo
from callshield.auth.tokens import Principal, decode_access_token
from callshield.core.config import Settings
from callshield.core.errors import ForbiddenError, UnauthorizedError
from callshield.db.models.tenancy import User
from callshield.db.session import bind_tenant, get_session
from callshield.domain.enums import Role

_bearer = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> Principal:
    """Validate the bearer token AND that the user still exists, is active, and
    belongs to the token's tenant. Binds the session to that tenant."""
    if credentials is None:
        raise UnauthorizedError("missing bearer token", code="MISSING_TOKEN")
    principal = decode_access_token(settings, credentials.credentials)
    bind_tenant(session, principal.tenant_id)
    user = (
        session.query(User)
        .filter(User.id == principal.user_id, User.tenant_id == principal.tenant_id)
        .one_or_none()
    )
    if user is None or not user.is_active:
        raise UnauthorizedError("user not found or inactive", code="INVALID_TOKEN")
    # Trust the DB role over the (possibly stale) token role.
    return Principal(principal.tenant_id, principal.user_id, Role(user.role))


def require_role(*roles: Role) -> Callable[..., Principal]:
    """Dependency factory: `Depends(require_role(Role.HOUSEHOLD_ADMIN))`."""
    allowed = frozenset(roles)

    def checker(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if principal.role not in allowed:
            raise ForbiddenError("insufficient role", code="INSUFFICIENT_ROLE")
        return principal

    return checker


def tenant_session(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Session:
    """Session bound to the caller's tenant (Postgres: app.tenant_id via SET LOCAL).
    Same session object as current_principal used (FastAPI dependency caching)."""
    bind_tenant(session, principal.tenant_id)
    return session


@dataclass
class TenantContext:
    session: Session
    principal: Principal

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.principal.tenant_id

    def repo(self, model: type[Any]) -> TenantScopedRepo[Any]:
        return TenantScopedRepo(self.session, model, self.tenant_id)


def tenant_context(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(tenant_session)],
) -> TenantContext:
    return TenantContext(session=session, principal=principal)
