"""MockProvider and request builders for tests/fixtures. Fictional numbers only."""

from __future__ import annotations

import json
from typing import Any

from callshield.core.clock import utcnow
from callshield.telephony.adapters import GenericWebhookAdapter
from callshield.telephony.base import RouteInstruction
from callshield.telephony.signing import sign_headers

FICTIONAL_LINE = "+14155550100"
FICTIONAL_USER = "+14155550101"
FICTIONAL_CALLER = "+14155550123"


class MockProvider(GenericWebhookAdapter):
    """Generic payload format plus an inspectable log of route actions and health."""

    name = "mock"

    def __init__(self) -> None:
        self.actions: list[RouteInstruction] = []
        self.fallbacks_restored: list[str | None] = []
        self.status = "OK"

    def _log(self, instruction: RouteInstruction) -> RouteInstruction:
        self.actions.append(instruction)
        return instruction

    def answer(self, call_id: str) -> RouteInstruction:
        return self._log(super().answer(call_id))

    def transfer(self, call_id: str, target: str, reason: str = "") -> RouteInstruction:
        return self._log(super().transfer(call_id, target, reason))

    def voicemail(self, call_id: str, reason: str = "") -> RouteInstruction:
        return self._log(super().voicemail(call_id, reason))

    def hangup(self, call_id: str, reason: str = "") -> RouteInstruction:
        return self._log(super().hangup(call_id, reason))

    def play(self, call_id: str, text: str) -> RouteInstruction:
        return self._log(super().play(call_id, text))

    def health(self) -> dict[str, str]:
        return {"status": self.status, "detail": "mock provider"}

    def restore_fallback(self, endpoint_id: str | None) -> bool:
        self.fallbacks_restored.append(endpoint_id)
        return True


def generic_event(event_type: str, event_id: str, **fields: Any) -> dict[str, Any]:
    """Build a generic-webhook payload dict."""
    body: dict[str, Any] = {"event_id": event_id, "type": event_type}
    if not event_type.startswith("registration."):
        body["call_id"] = fields.pop("call_id", "call-1")
    body.update(fields)
    return body


def inbound_call_payload(
    call_id: str = "call-1",
    event_id: str | None = None,
    to: str = FICTIONAL_LINE,
    caller: str | None = FICTIONAL_CALLER,
    **extra: Any,
) -> dict[str, Any]:
    return generic_event(
        "call.received",
        event_id or f"evt-{call_id}-recv",
        call_id=call_id,
        to=to,
        **{"from": caller},
        **extra,
    )


def ari_stasis_start(
    channel_id: str, to: str, caller: str, endpoint: str = "trunk1", args: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "StasisStart",
        "timestamp": "2026-01-01T12:00:00.000+0000",
        "application": "callshield",
        "args": args or [],
        "channel": {
            "id": channel_id,
            "name": f"PJSIP/{endpoint}-00000001",
            "caller": {"number": caller},
            "dialplan": {"exten": to},
        },
    }


def signed_request(
    secret: str, payload: dict[str, Any] | bytes, nonce: str, timestamp: int | None = None
) -> tuple[bytes, dict[str, str]]:
    """Serialize and sign a payload; `timestamp` defaults to the (injectable) clock."""
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    ts = timestamp if timestamp is not None else int(utcnow().timestamp())
    headers = sign_headers(secret, ts, nonce, body)
    headers["Content-Type"] = "application/json"
    return body, headers
