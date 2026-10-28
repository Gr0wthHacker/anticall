"""Deterministic endpointing on top of a VAD (spec "Call flow" step 4).

Events (time comes from audio length, never the wall clock):
* `speech_start`: voiced audio for at least `min_speech_ms`; `at_s` is where speech began.
* `pause`: `pause_ms` (default 800) of silence after speech; `at_s` is when the condition held,
  so speech ended at `at_s - pause_ms/1000`.
* `bare_greeting_silence`: `bare_greeting_ms` (default 1500) of silence after a very short
  utterance (at most `short_utterance_ms` of voiced audio). A short utterance emits `pause`
  first (at 800 ms) and this event later; the consumer decides from the transcript whether the
  greeting was bare. Speech resuming before an event cancels that event.
"""

from __future__ import annotations

from callshield.voice.base import EndpointEvent, EndpointKind
from callshield.voice.vad import Vad

_EPS = 1e-6


class VadEndpointer:
    def __init__(
        self,
        vad: Vad,
        sample_rate: int = 16000,
        *,
        pause_ms: int = 800,
        bare_greeting_ms: int = 1500,
        min_speech_ms: int = 60,
        short_utterance_ms: int = 1200,
    ) -> None:
        self._vad = vad
        self._frame_bytes = vad.frame_samples * 2
        self._frame_ms = vad.frame_samples * 1000.0 / sample_rate
        self._pause_ms = pause_ms
        self._bare_ms = bare_greeting_ms
        self._min_speech_ms = min_speech_ms
        self._short_ms = short_utterance_ms
        self.reset()

    def reset(self) -> None:
        self._vad.reset()
        self._buf = bytearray()
        self._frames = 0
        self._open = False  # an utterance has started (until superseded)
        self._pause_fired = False
        self._bare_fired = False
        self._run_ms = 0.0
        self._run_start_s = 0.0
        self._voiced_ms = 0.0
        self._silence_ms = 0.0

    def feed(self, pcm16: bytes) -> list[EndpointEvent]:
        self._buf.extend(pcm16)
        events: list[EndpointEvent] = []
        while len(self._buf) >= self._frame_bytes:
            frame = bytes(self._buf[: self._frame_bytes])
            del self._buf[: self._frame_bytes]
            start_s = self._frames * self._frame_ms / 1000
            self._frames += 1
            if self._vad.is_speech(frame):
                self._on_voiced(start_s, events)
            else:
                self._on_silent(start_s + self._frame_ms / 1000, events)
        return events

    def _emit(self, events: list[EndpointEvent], kind: EndpointKind, at_s: float) -> None:
        events.append(EndpointEvent(kind, round(at_s, 6), round(self._silence_ms)))

    def _on_voiced(self, frame_start_s: float, events: list[EndpointEvent]) -> None:
        if self._open and not self._pause_fired:
            self._voiced_ms += self._frame_ms
            self._silence_ms = 0.0
            return
        if self._run_ms == 0.0:
            self._run_start_s = frame_start_s
        self._run_ms += self._frame_ms
        if self._run_ms + _EPS >= self._min_speech_ms:
            self._open, self._pause_fired, self._bare_fired = True, False, False
            self._voiced_ms, self._silence_ms = self._run_ms, 0.0
            self._run_ms = 0.0
            self._emit(events, "speech_start", self._run_start_s)

    def _on_silent(self, frame_end_s: float, events: list[EndpointEvent]) -> None:
        self._run_ms = 0.0
        if not self._open:
            return
        self._silence_ms += self._frame_ms
        if not self._pause_fired and self._silence_ms + _EPS >= self._pause_ms:
            self._pause_fired = True
            self._emit(events, "pause", frame_end_s)
        short = self._voiced_ms <= self._short_ms + _EPS
        if (
            self._pause_fired
            and not self._bare_fired
            and short
            and self._silence_ms + _EPS >= self._bare_ms
        ):
            self._bare_fired = True
            self._emit(events, "bare_greeting_silence", frame_end_s)
