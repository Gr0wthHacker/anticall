"""Tenants and users (PRD 8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from callshield.db.base import Base, JSONType, TenantOwned, UTCDateTime, created_column, id_column
from callshield.domain.enums import HostingMode, Role


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = id_column()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    hosting_mode: Mapped[str] = mapped_column(String(32), default=HostingMode.SELF_HOSTED.value)
    jurisdiction_policy_version: Mapped[str] = mapped_column(String(64), default="unset")
    created_at: Mapped[datetime] = created_column()


class User(TenantOwned, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = id_column()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default=Role.CONSUMER_SUBSCRIBER.value)
    consent_preferences: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # TODO(mfa): populated once callshield.auth.mfa has a real provider.
    mfa_enrolled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_column()
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
