"""Local auth, principals, tenant scoping."""

from callshield.auth.deps import (
    TenantContext,
    current_principal,
    require_role,
    tenant_context,
    tenant_session,
)
from callshield.auth.scoping import (
    TenantScopedRepo,
    scoped_get,
    scoped_get_or_404,
    scoped_select,
)
from callshield.auth.tokens import Principal

__all__ = [
    "Principal",
    "TenantContext",
    "TenantScopedRepo",
    "current_principal",
    "require_role",
    "scoped_get",
    "scoped_get_or_404",
    "scoped_select",
    "tenant_context",
    "tenant_session",
]
