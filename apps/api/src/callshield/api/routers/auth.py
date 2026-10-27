"""Bootstrap, login, whoami, member creation."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from callshield.audit import record_audit
from callshield.auth.deps import get_app_settings, require_role
from callshield.auth.passwords import hash_password, verify_password
from callshield.auth.tokens import Principal, create_access_token
from callshield.core.clock import utcnow
from callshield.core.config import Settings
from callshield.core.errors import ConflictError, ForbiddenError, UnauthorizedError
from callshield.db.models.tenancy import Tenant, User
from callshield.db.session import bind_tenant, get_session
from callshield.domain.enums import HostingMode, Role

router = APIRouter(tags=["auth"])

PASSWORD_MIN = 12
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class BootstrapRequest(BaseModel):
    tenant_name: str = Field(min_length=1, max_length=200)
    hosting_mode: HostingMode = HostingMode.SELF_HOSTED
    admin_email: str = Field(pattern=EMAIL_PATTERN, max_length=320)
    admin_password: str = Field(min_length=PASSWORD_MIN, max_length=256)
    admin_display_name: str = Field(default="", max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class BootstrapResponse(BaseModel):
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    token: TokenResponse


class LoginRequest(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=320)
    password: str = Field(max_length=256)


class MeResponse(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: Role
    email: str
    display_name: str


class CreateUserRequest(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=320)
    password: str = Field(min_length=PASSWORD_MIN, max_length=256)
    display_name: str = Field(default="", max_length=200)
    role: Role = Role.CONSUMER_SUBSCRIBER


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: Role
    display_name: str


def _token_for(settings: Settings, principal: Principal) -> TokenResponse:
    token, ttl = create_access_token(settings, principal)
    return TokenResponse(access_token=token, expires_in=ttl)


def _email_taken(session: Session, email: str) -> bool:
    return session.execute(select(User.id).where(User.email == email)).first() is not None


@router.post("/v1/tenants", response_model=BootstrapResponse, status_code=201)
def bootstrap_tenant(
    body: BootstrapRequest,
    settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> BootstrapResponse:
    """Create a tenant + first admin. Allowed when allow_bootstrap=true or no tenant exists."""
    existing = session.execute(select(func.count()).select_from(Tenant)).scalar_one()
    if existing > 0 and not settings.allow_bootstrap:
        raise ForbiddenError("tenant bootstrap is disabled", code="BOOTSTRAP_DISABLED")
    email = body.admin_email.lower()
    if _email_taken(session, email):
        raise ConflictError("email already registered", code="EMAIL_TAKEN")
    tenant = Tenant(name=body.tenant_name, hosting_mode=body.hosting_mode.value)
    session.add(tenant)
    session.flush()
    bind_tenant(session, tenant.id)
    admin = User(
        tenant_id=tenant.id,
        email=email,
        display_name=body.admin_display_name,
        password_hash=hash_password(body.admin_password),
        role=Role.HOUSEHOLD_ADMIN.value,
    )
    session.add(admin)
    session.flush()
    record_audit(session, tenant.id, f"user:{admin.id}", "tenant.bootstrapped", {})
    principal = Principal(tenant.id, admin.id, Role.HOUSEHOLD_ADMIN)
    return BootstrapResponse(
        tenant_id=tenant.id, user_id=admin.id, token=_token_for(settings, principal)
    )


@router.post("/v1/auth/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> TokenResponse:
    user = session.execute(
        select(User).where(User.email == body.email.lower())
    ).scalar_one_or_none()
    valid = verify_password(user.password_hash if user else None, body.password)
    if user is None or not valid or not user.is_active:
        raise UnauthorizedError("invalid email or password", code="INVALID_CREDENTIALS")
    bind_tenant(session, user.tenant_id)
    user.last_login_at = utcnow()
    record_audit(session, user.tenant_id, f"user:{user.id}", "auth.login", {})
    return _token_for(settings, Principal(user.tenant_id, user.id, Role(user.role)))


@router.get("/v1/auth/me", response_model=MeResponse)
def me(
    principal: Annotated[Principal, Depends(require_role(*Role))],
    session: Annotated[Session, Depends(get_session)],
) -> MeResponse:
    user = session.execute(
        select(User).where(User.id == principal.user_id, User.tenant_id == principal.tenant_id)
    ).scalar_one()
    return MeResponse(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=Role(user.role),
        email=user.email,
        display_name=user.display_name,
    )


@router.post("/v1/users", response_model=UserResponse, status_code=201)
def create_user(
    body: CreateUserRequest,
    principal: Annotated[Principal, Depends(require_role(Role.HOUSEHOLD_ADMIN))],
    session: Annotated[Session, Depends(get_session)],
) -> UserResponse:
    """Admin adds a member to their own tenant. (No email invite flow yet.)"""
    email = body.email.lower()
    if _email_taken(session, email):
        raise ConflictError("email already registered", code="EMAIL_TAKEN")
    user = User(
        tenant_id=principal.tenant_id,
        email=email,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
        role=body.role.value,
    )
    session.add(user)
    session.flush()
    record_audit(
        session,
        principal.tenant_id,
        principal.actor,
        "user.created",
        {"user_id": str(user.id), "role": body.role.value},
    )
    return UserResponse(
        id=user.id, email=user.email, role=body.role, display_name=user.display_name
    )
