"""Callback dialer seam: extends the resolve `OutboundDialer` with what callbacks need.

* The caller ID is NOT a free parameter: `VerifiedCaller` can only be built by
  `callback.lines.verified_caller` after the line passed verification, the ownership check
  and the outbound-A-attestation requirement. Adapters resolve the SIP From from it.
* Handles add `start_recording()/stop_recording()`, called ONLY after the disclosure
  completes and only when outbound_recording is on, so pre-disclosure audio is never kept.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from callshield.core.errors import PolicyBlockedError
from callshield.resolve.dialer import CallEvent, CallEventKind, CallHandle, OutboundDialer


class MediaEventKind(StrEnum):
    ANSWERED = "ANSWERED"
    SPEECH = "SPEECH"
    HANGUP = "HANGUP"
    NO_ANSWER = "NO_ANSWER"
    BUSY = "BUSY"
    FAILED = "FAILED"
    STREAM_LOST = "STREAM_LOST"
    RINGING = "RINGING"


@dataclass(frozen=True)
class MediaEvent:
    """`offset_seconds` is when the event ENDED (virtual call clock); `duration_seconds` is
    the utterance length, so speech started at offset - duration."""

    kind: MediaEventKind
    text: str = ""
    offset_seconds: float = 0.0
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class VerifiedCaller:
    line_id: uuid.UUID
    e164: str


@runtime_checkable
class CallbackCallHandle(Protocol):
    """Superset of the resolve `CallHandle` with a richer `poll` (MediaEvent) and recording."""

    call_ref: str

    def poll(self) -> CallEvent | MediaEvent | None: ...

    def send_dtmf(self, digits: str) -> None: ...

    def speak(self, text: str) -> bool: ...

    def hangup(self) -> None: ...

    def start_recording(self) -> None: ...

    def stop_recording(self) -> bytes: ...


@runtime_checkable
class CallbackDialer(OutboundDialer, Protocol):
    def place_callback(
        self,
        destination_e164: str,
        session_id: uuid.UUID,
        *,
        caller: VerifiedCaller,
        max_seconds: int,
    ) -> CallbackCallHandle: ...


class DisabledCallbackDialer:
    kind = "disabled"
    configured = False

    def place_call(
        self, destination_e164: str, session_id: uuid.UUID, *, max_seconds: int
    ) -> CallHandle:
        raise PolicyBlockedError(
            "use place_callback with a verified caller", code="DIALER_NOT_CONFIGURED"
        )

    def place_callback(
        self,
        destination_e164: str,
        session_id: uuid.UUID,
        *,
        caller: VerifiedCaller,
        max_seconds: int,
    ) -> CallbackCallHandle:
        raise PolicyBlockedError(
            "Outbound calling is disabled: no telephony adapter is configured.",
            code="DIALER_NOT_CONFIGURED",
        )


_KIND_MAP = {
    CallEventKind.RINGING: MediaEventKind.RINGING,
    CallEventKind.ANSWERED: MediaEventKind.ANSWERED,
    CallEventKind.BUSY: MediaEventKind.BUSY,
    CallEventKind.NO_ANSWER: MediaEventKind.NO_ANSWER,
    CallEventKind.SPEECH: MediaEventKind.SPEECH,
    CallEventKind.HANGUP: MediaEventKind.HANGUP,
    CallEventKind.STREAM_LOST: MediaEventKind.STREAM_LOST,
    CallEventKind.FAILED: MediaEventKind.FAILED,
}


def normalize_event(event: CallEvent | MediaEvent) -> MediaEvent:
    """Accept either the legacy resolve CallEvent or the richer MediaEvent."""
    if isinstance(event, MediaEvent):
        return event
    return MediaEvent(_KIND_MAP[event.kind], event.text, event.offset_seconds, 0.0)
