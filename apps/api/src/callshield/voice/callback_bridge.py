"""Adapter from live STT + endpointing to the callback session's `MediaEvent`s.

The synchronous `CallbackSession` consumes `MediaEvent`s on a virtual clock where a SPEECH
event carries the utterance text, the offset at which it ENDED and its duration (see
`callback.mock_scenarios.say`). This bridge produces exactly that from real audio:

* only FINAL STT results become SPEECH events (partials are counted, never forwarded);
* the endpointer's measured speech end (pause event time minus the silence) replaces the STT
  end time when the two agree within `SNAP_TOLERANCE_S`, because VAD edges are tighter than
  recognizer word timestamps and the 800 ms / 1.5 s disclosure triggers key off the end;
* `base_offset_s` maps the stream clock onto the call clock.

Text is passed straight to the session, which keeps it in RAM and discards it at disclosure.
This module never stores or logs audio or text.
"""

from __future__ import annotations

from callshield.callback.dialer_port import MediaEvent, MediaEventKind
from callshield.voice.base import Endpointer, EndpointEvent, SttEvent, SttStream

SNAP_TOLERANCE_S = 1.0


class CallbackSpeechBridge:
    def __init__(
        self, stt: SttStream, endpointer: Endpointer | None = None, *, base_offset_s: float = 0.0
    ) -> None:
        self._stt = stt
        self._ep = endpointer
        self._base = base_offset_s
        self._speech_ends: list[float] = []
        self.partials_seen = 0
        self.pauses_seen = 0

    def feed(self, pcm16: bytes) -> list[MediaEvent]:
        if self._ep is not None:
            self._note_endpoints(self._ep.feed(pcm16))
        return self._convert(self._stt.feed(pcm16))

    def finish(self) -> list[MediaEvent]:
        return self._convert(self._stt.finish())

    def answered(self, offset_s: float) -> MediaEvent:
        return MediaEvent(MediaEventKind.ANSWERED, "", offset_s, 0.0)

    def _note_endpoints(self, events: list[EndpointEvent]) -> None:
        for event in events:
            if event.kind == "pause":
                self.pauses_seen += 1
                self._speech_ends.append(event.at_s - event.pause_ms / 1000)

    def _snapped_end(self, stt_end: float) -> float:
        nearest = min(self._speech_ends, key=lambda e: abs(e - stt_end), default=None)
        if nearest is not None and abs(nearest - stt_end) <= SNAP_TOLERANCE_S:
            return nearest
        return stt_end

    def _convert(self, events: list[SttEvent]) -> list[MediaEvent]:
        out: list[MediaEvent] = []
        for event in events:
            if not event.is_final:
                self.partials_seen += 1
                continue
            text = event.text.strip()
            if not text:
                continue
            end = max(self._snapped_end(event.end_s), event.start_s)
            out.append(
                MediaEvent(
                    MediaEventKind.SPEECH, text, self._base + end, max(0.0, end - event.start_s)
                )
            )
        return out
