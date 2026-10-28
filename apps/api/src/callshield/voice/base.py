"""Engine contracts shared by the voice package and the telephony layer.

Audio is always PCM16 little-endian mono. Times are seconds on the audio clock of the stream
(derived from the number of samples fed, never the wall clock).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class SttEvent:
    text: str
    is_final: bool
    start_s: float
    end_s: float


@runtime_checkable
class SttStream(Protocol):
    def feed(self, pcm16: bytes) -> list[SttEvent]: ...

    def finish(self) -> list[SttEvent]: ...


@runtime_checkable
class SttEngine(Protocol):
    name: str
    local_only: bool

    def new_stream(self, sample_rate: int = 16000) -> SttStream: ...


@runtime_checkable
class TtsEngine(Protocol):
    name: str
    local_only: bool
    sample_rate: int

    def synthesize(self, text: str) -> bytes:
        """PCM16 mono at `sample_rate`."""
        ...


EndpointKind = Literal["speech_start", "pause", "bare_greeting_silence"]


@dataclass(frozen=True)
class EndpointEvent:
    kind: EndpointKind
    at_s: float
    pause_ms: int


@runtime_checkable
class Endpointer(Protocol):
    def feed(self, pcm16: bytes) -> list[EndpointEvent]: ...

    def reset(self) -> None: ...
