"""Deterministic fakes and synthetic audio for tests (importable by other packages' tests).

`FakeStt` replays scripted `SttEvent`s as the fed audio clock passes each event's `end_s`
(or an explicit release time). `FakeTts` renders text to a short sine tone whose length is a
fixed function of the text. Neither touches a model, file or network.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from callshield.voice.base import SttEvent
from callshield.voice.pcm import BYTES_PER_SAMPLE, from_samples


def sine_pcm(
    seconds: float, sample_rate: int = 16000, freq: float = 300.0, amplitude: int = 8000
) -> bytes:
    count = round(seconds * sample_rate)
    return from_samples(
        [round(amplitude * math.sin(2 * math.pi * freq * i / sample_rate)) for i in range(count)]
    )


def silence_pcm(seconds: float, sample_rate: int = 16000) -> bytes:
    return bytes(round(seconds * sample_rate) * BYTES_PER_SAMPLE)


def chunked(pcm16: bytes, sample_rate: int = 16000, chunk_ms: int = 20) -> list[bytes]:
    size = sample_rate * chunk_ms // 1000 * BYTES_PER_SAMPLE
    return [pcm16[i : i + size] for i in range(0, len(pcm16), size)]


@dataclass(frozen=True)
class ScriptedEvent:
    event: SttEvent
    release_s: float | None = None  # audio time at which it is returned (default event.end_s)


class FakeSttStream:
    def __init__(self, script: Sequence[ScriptedEvent], sample_rate: int) -> None:
        self._pending = sorted(script, key=lambda s: s.release_s or s.event.end_s)
        self._rate = sample_rate
        self._samples = 0
        self.bytes_fed = 0
        self.finished = False

    @property
    def clock_s(self) -> float:
        return self._samples / self._rate

    def feed(self, pcm16: bytes) -> list[SttEvent]:
        self.bytes_fed += len(pcm16)
        self._samples += len(pcm16) // BYTES_PER_SAMPLE
        due = [s for s in self._pending if (s.release_s or s.event.end_s) <= self.clock_s + 1e-9]
        self._pending = [s for s in self._pending if s not in due]
        return [s.event for s in due]

    def finish(self) -> list[SttEvent]:
        self.finished = True
        rest, self._pending = self._pending, []
        return [s.event for s in rest]


@dataclass
class FakeStt:
    script: Sequence[SttEvent | ScriptedEvent] = ()
    name: str = "fake-stt"
    local_only: bool = True
    streams: list[FakeSttStream] = field(default_factory=list)

    def new_stream(self, sample_rate: int = 16000) -> FakeSttStream:
        items = [s if isinstance(s, ScriptedEvent) else ScriptedEvent(s) for s in self.script]
        stream = FakeSttStream(items, sample_rate)
        self.streams.append(stream)
        return stream


@dataclass
class FakeTts:
    sample_rate: int = 16000
    seconds_per_word: float = 0.3
    name: str = "fake-tts"
    local_only: bool = True
    calls: int = 0

    def synthesize(self, text: str) -> bytes:
        self.calls += 1
        words = max(1, len(text.split()))
        return sine_pcm(words * self.seconds_per_word, self.sample_rate)
