"""Tenant scoping helpers. Module code MUST read tenant data through these (or
an explicit tenant_id filter), never `session.get(Model, id)`."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import ColumnElement, Select, select
from sqlalchemy.orm import Session

from callshield.core.errors import NotFoundError
from callshield.db.base import Base

M = TypeVar("M", bound=Base)


def scoped_select(model: type[M], tenant_id: uuid.UUID) -> Select[M]:
    """SELECT pre-filtered by tenant_id. Refuses models without tenant_id."""
    column = getattr(model, "tenant_id", None)
    if column is None:
        raise TypeError(f"{model.__name__} is not tenant-owned")
    return select(model).where(column == tenant_id)


def scoped_get(
    session: Session, model: type[M], object_id: uuid.UUID, tenant_id: uuid.UUID
) -> M | None:
    """Fetch by primary key within a tenant; None for another tenant's row."""
    stmt = scoped_select(model, tenant_id).where(getattr(model, "id") == object_id)  # noqa: B009
    return session.execute(stmt).scalar_one_or_none()


def scoped_get_or_404(
    session: Session, model: type[M], object_id: uuid.UUID, tenant_id: uuid.UUID
) -> M:
    """Same as scoped_get but raises 404 (never 403: do not leak existence)."""
    found = scoped_get(session, model, object_id, tenant_id)
    if found is None:
        raise NotFoundError(f"{model.__name__} not found", code="NOT_FOUND")
    return found


class TenantScopedRepo(Generic[M]):
    """Repository bound to (session, model, tenant_id). All access is tenant-filtered."""

    def __init__(self, session: Session, model: type[M], tenant_id: uuid.UUID) -> None:
        self.session = session
        self.model = model
        self.tenant_id = tenant_id

    def select(self) -> Select[M]:
        return scoped_select(self.model, self.tenant_id)

    def get(self, object_id: uuid.UUID) -> M | None:
        return scoped_get(self.session, self.model, object_id, self.tenant_id)

    def get_or_404(self, object_id: uuid.UUID) -> M:
        return scoped_get_or_404(self.session, self.model, object_id, self.tenant_id)

    def list(self, *where: ColumnElement[bool], limit: int = 100, offset: int = 0) -> Sequence[M]:
        stmt = self.select().where(*where).limit(limit).offset(offset)
        return self.session.execute(stmt).scalars().all()

    def add(self, **fields: Any) -> M:
        """Create a row stamped with this repo's tenant_id (callers cannot override it)."""
        fields.pop("tenant_id", None)
        obj = self.model(tenant_id=self.tenant_id, **fields)
        self.session.add(obj)
        return obj
