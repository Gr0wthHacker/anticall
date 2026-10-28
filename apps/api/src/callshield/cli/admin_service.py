"""Create the first tenant + admin from the command line (mirrors POST /v1/tenants)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.audit import record_audit
from callshield.auth.passwords import hash_password
from callshield.db.models.tenancy import Tenant, User
from callshield.db.session import bind_tenant
from callshield.domain.enums import HostingMode, Role

PASSWORD_MIN = 12


class AdminCreateError(ValueError):
    pass


def create_admin(session: Session, tenant_name: str, email: str, password: str) -> uuid.UUID:
    """Create tenant + HOUSEHOLD_ADMIN. Caller commits. Never logs the password."""
    email = email.strip().lower()
    if "@" not in email or len(password) < PASSWORD_MIN:
        raise AdminCreateError(f"need a valid email and a password of {PASSWORD_MIN}+ characters")
    if session.execute(select(User.id).where(User.email == email)).first() is not None:
        raise AdminCreateError("that email is already registered")
    tenant = Tenant(name=tenant_name or "My household", hosting_mode=HostingMode.SELF_HOSTED.value)
    session.add(tenant)
    session.flush()
    bind_tenant(session, tenant.id)
    admin = User(
        tenant_id=tenant.id,
        email=email,
        display_name="",
        password_hash=hash_password(password),
        role=Role.HOUSEHOLD_ADMIN.value,
    )
    session.add(admin)
    session.flush()
    record_audit(session, tenant.id, "cli:create-admin", "tenant.bootstrapped", {})
    return admin.id
