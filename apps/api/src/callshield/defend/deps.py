"""Shared router dependencies for DEFEND."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from callshield.auth.deps import TenantContext, get_app_settings, tenant_context
from callshield.core.config import Settings
from callshield.core.errors import ForbiddenError
from callshield.domain.enums import Role

WRITER_ROLES = frozenset({Role.HOUSEHOLD_ADMIN, Role.CONSUMER_SUBSCRIBER})


def writer_context(ctx: Annotated[TenantContext, Depends(tenant_context)]) -> TenantContext:
    """Tenant context for state-changing calls: owners/admins only (reviewers/operators read)."""
    if ctx.principal.role not in WRITER_ROLES:
        raise ForbiddenError("insufficient role", code="INSUFFICIENT_ROLE")
    return ctx


Reader = Annotated[TenantContext, Depends(tenant_context)]
Writer = Annotated[TenantContext, Depends(writer_context)]

AppSettings = Annotated[Settings, Depends(get_app_settings)]
