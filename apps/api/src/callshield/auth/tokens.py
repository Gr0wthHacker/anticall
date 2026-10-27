"""JWT access tokens carrying tenant_id, user_id and role."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

import jwt

from callshield.core.clock import utcnow
from callshield.core.config import Settings
from callshield.core.errors import UnauthorizedError
from callshield.domain.enums import Role


@dataclass(frozen=True)
class Principal:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role: Role

    @property
    def actor(self) -> str:
        return f"user:{self.user_id}"


def create_access_token(settings: Settings, principal: Principal) -> tuple[str, int]:
    """Return (token, expires_in_seconds)."""
    now = utcnow()
    ttl = settings.access_token_ttl_seconds
    claims = {
        "sub": str(principal.user_id),
        "tid": str(principal.tenant_id),
        "role": principal.role.value,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    token = jwt.encode(
        claims, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, ttl


def decode_access_token(settings: Settings, token: str) -> Principal:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "tid", "role"]},
            leeway=0,
        )
        return Principal(
            tenant_id=uuid.UUID(claims["tid"]),
            user_id=uuid.UUID(claims["sub"]),
            role=Role(claims["role"]),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise UnauthorizedError("invalid or expired token", code="INVALID_TOKEN") from exc
