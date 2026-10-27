"""Provider adapter abstraction and the normalized event model."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class ProviderEventType(StrEnum):
    CALL_RECEIVED = "call.received"
    CALL_ANSWERED = "call.answered"
    CALL_TRANSFERRED = "call.transferred"
    CALL_ENDED = "call.ended"
    CALLER_UTTERANCE = "caller.utterance"
    SCREENING_TIMEOUT = "screening.timeout"
    REGISTRATION_FAILED = "registration.failed"
    REGISTRATION_OK = "registration.ok"


class ProviderCallEvent(BaseModel):
    """Provider-neutral event. Fields are untrusted until the webhook is verified, and
    even then the tenant is never taken from them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    event_id: str = Field(min_length=1, max_length=200)
    event_type: ProviderEventType
    call_id: str | None = Field(default=None, max_length=200)
    to_number: str | None = None  # normalized E.164
    endpoint_id: str | None = Field(default=None, max_length=120)
    from_number: str | None = Field(default=None, max_length=64)  # displayed CLI, as observed
    direction: str = "INBOUND"
    attestation: dict[str, Any] = Field(default_factory=dict)
    hop_count: int = Field(default=0, ge=0, le=64)
    diversions: tuple[str, ...] = ()
    occurred_at: datetime | None = None
    duration_seconds: int | None = Field(default=None, ge=0, le=86400 * 2)
    text: str | None = Field(default=None, max_length=4000)  # STT utterance (gated upstream)
    detail: str = Field(default="", max_length=200)


class WebhookVerificationError(Exception):
    """Raised when a webhook fails signature / timestamp / replay checks."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MalformedEventError(ValueError):
    """Raised by parse_event for bodies that cannot be normalized."""


class RouteInstruction(BaseModel):
    """What the provider should do with the call (returned in the webhook response)."""

    model_config = ConfigDict(extra="forbid")

    op: str  # answer | transfer | voicemail | hangup | play
    call_id: str
    target: str | None = None
    text: str | None = None
    reason: str = ""


@runtime_checkable
class ProviderAdapter(Protocol):
    name: str

    def verify_webhook(
        self, headers: Mapping[str, str], body: bytes, secret: str, tolerance_seconds: int = 300
    ) -> str:
        """Verify signature/timestamp; return the replay nonce. Raise WebhookVerificationError."""
        ...

    def parse_event(self, body: bytes) -> ProviderCallEvent:
        """Normalize a (verified) body. Raise MalformedEventError."""
        ...

    def answer(self, call_id: str) -> RouteInstruction: ...

    def transfer(self, call_id: str, target: str, reason: str = "") -> RouteInstruction: ...

    def voicemail(self, call_id: str, reason: str = "") -> RouteInstruction: ...

    def hangup(self, call_id: str, reason: str = "") -> RouteInstruction: ...

    def play(self, call_id: str, text: str) -> RouteInstruction: ...

    def health(self) -> dict[str, str]: ...

    def restore_fallback(self, endpoint_id: str | None) -> bool:
        """Best-effort: point the endpoint at ordinary routing after a registration failure."""
        ...
