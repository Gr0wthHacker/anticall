"""Shared small types for the filing services."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from callshield.audit.service import actor_for_user
from callshield.domain.enums import Role


@dataclass(frozen=True)
class Actor:
    """The verified principal issuing a command (never taken from a request body)."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role: Role

    @property
    def label(self) -> str:
        return actor_for_user(self.user_id)


def command_audit_data(
    scope: str, authorization_basis: str, idempotency_key: str | None, **extra: object
) -> dict[str, object]:
    """Every legal/financial command records actor scope, authorization basis and key."""
    return {
        "scope": scope,
        "authorization_basis": authorization_basis,
        "idempotency_key": idempotency_key,
        **extra,
    }
