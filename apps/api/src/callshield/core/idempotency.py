"""Idempotency-key helpers. Keys are stable and derived from the logical operation."""

from __future__ import annotations

import re

from callshield.core.hashing import sha256_hex

_MAX_LEN = 200
_UNSAFE = re.compile(r"[^A-Za-z0-9:_.\-]")


def make_idempotency_key(*parts: object) -> str:
    """Compose a key from ordered parts, e.g. ('provider', 'call-1', 'call.received')."""
    joined = ":".join(_UNSAFE.sub("_", str(p)) for p in parts)
    if len(joined) <= _MAX_LEN:
        return joined
    return joined[:120] + ":" + sha256_hex(joined)[:32]


def provider_event_key(provider: str, provider_event_id: str, topic: str) -> str:
    return make_idempotency_key("provider", provider, provider_event_id, topic)


def command_key(actor_id: object, scope: str, client_key: str) -> str:
    """Key for user-issued commands (PRD 20.3: actor_id, scope, idempotency_key)."""
    return make_idempotency_key("cmd", actor_id, scope, client_key)
