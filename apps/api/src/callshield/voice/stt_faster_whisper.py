"""faster-whisper STT (local, CTranslate2). Streaming is an approximation.

faster-whisper is a batch model. The stream segments audio with the endpointer, re-transcribes
the open utterance about once per `partial_interval_s` to emit partials, and transcribes the
whole utterance when a pause closes it (final, word timestamps -> start/end). Models load with
`local_files_only=True`: nothing is ever downloaded. Never logs audio or text.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from callshield.voice.base import Endpointer, SttEvent
from callshield.voice.endpoint import VadEndpointer
from callshield.voice.errors import VoiceEngineUnavailable, import_optional
from callshield.voice.pcm import BYTES_PER_SAMPLE
from callshield.voice.resample import resample_pcm16
from callshield.voice.vad import Vad

logger = logging.getLogger(__name__)
WHISPER_RATE = 16000
PREROLL_S = 0.25
TAIL_S = 0.2

VadFactory = Callable[[int], Vad]
AudioConverter = Callable[[bytes], Any]


def pcm16_to_float32(pcm16: bytes) -> Any:
    np = import_optional("numpy", purpose="faster-whisper")
    usable = pcm16[: len(pcm16) - len(pcm16) % BYTES_PER_SAMPLE]
    return np.frombuffer(usable, dtype="<i2").astype(np.float32) / 32768.0


class FasterWhisperStt:
    name = "faster-whisper"
    local_only = True

    def __init__(
        self,
        model: Any,
        vad_factory: VadFactory,
        *,
        language: str = "en",
        segment_pause_ms: int = 500,
        partial_interval_s: float = 1.0,
        audio_converter: AudioConverter = pcm16_to_float32,
    ) -> None:
        self._model = model
        self._vad_factory = vad_factory
        self.language = language
        self.segment_pause_ms = segment_pause_ms
        self.partial_interval_s = partial_interval_s
        self._convert = audio_converter

    @classmethod
    def load(
        cls,
        model_name_or_path: str,
        *,
        vad_factory: VadFactory,
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: str | None = None,
        language: str = "en",
    ) -> FasterWhisperStt:
        module = import_optional("faster_whisper", purpose="the faster-whisper STT engine")
        try:
            model = module.WhisperModel(
                model_name_or_path,
                device=device,
                compute_type=compute_type,
                download_root=download_root,
                local_files_only=True,
            )
        except Exception as exc:
            raise VoiceEngineUnavailable(
                f"faster-whisper model '{model_name_or_path}' could not be loaded offline. "
                "Download it first and set CALLSHIELD_VOICE_STT_MODEL_DIR to the folder "
                "that contains it (see docs/deployment/voice.md)."
            ) from exc
        return cls(model, vad_factory, language=language)

    def transcribe(
        self, pcm16: bytes, sample_rate: int, offset_s: float, final: bool
    ) -> list[SttEvent]:
        """Transcribe one utterance buffer; returns zero or one event."""
        if sample_rate != WHISPER_RATE:
            pcm16 = resample_pcm16(pcm16, sample_rate, WHISPER_RATE)
        segments, _info = self._model.transcribe(
            self._convert(pcm16),
            language=self.language,
            word_timestamps=True,
            vad_filter=False,
            beam_size=5 if final else 1,
            condition_on_previous_text=False,
        )
        return _events_from_segments(list(segments), offset_s, final)

    def new_stream(self, sample_rate: int = 16000) -> WhisperStream:
        endpointer = VadEndpointer(
            self._vad_factory(sample_rate),
            sample_rate,
            pause_ms=self.segment_pause_ms,
            bare_greeting_ms=10_000_000,
        )
        return WhisperStream(self, endpointer, sample_rate)


def _events_from_segments(segments: list[Any], offset_s: float, final: bool) -> list[SttEvent]:
    text = " ".join(str(s.text).strip() for s in segments).strip()
    if not text:
        return []
    words = [w for s in segments for w in (getattr(s, "words", None) or [])]
    if words:
        start, end = float(words[0].start), float(words[-1].end)
    else:
        start, end = float(segments[0].start), float(segments[-1].end)
    return [SttEvent(text, final, offset_s + start, offset_s + end)]


class WhisperStream:
    def __init__(self, engine: FasterWhisperStt, endpointer: Endpointer, sample_rate: int) -> None:
        self._engine = engine
        self._ep = endpointer
        self._rate = sample_rate
        self._buf = bytearray()
        self._buf_start = 0  # absolute sample index of buf[0]
        self._total = 0
        self._in_speech = False
        self._last_partial_total = 0
        self._last_partial_text = ""
        self.finals = 0

    def feed(self, pcm16: bytes) -> list[SttEvent]:
        usable = pcm16[: len(pcm16) - len(pcm16) % BYTES_PER_SAMPLE]
        self._buf.extend(usable)
        self._total += len(usable) // BYTES_PER_SAMPLE
        events: list[SttEvent] = []
        for ep in self._ep.feed(usable):
            if ep.kind == "speech_start":
                self._on_start(ep.at_s)
            elif ep.kind == "pause":
                events += self._close(ep.at_s - ep.pause_ms / 1000 + TAIL_S)
        events += self._maybe_partial()
        if not self._in_speech:
            self._drop_before(self._total - round(2 * PREROLL_S * self._rate))
        return events

    def finish(self) -> list[SttEvent]:
        events = self._close(self._total / self._rate) if self._in_speech else []
        self._ep.reset()
        logger.debug(
            "whisper stream closed finals=%d audio_s=%.1f", self.finals, self._total / self._rate
        )
        return events

    def _on_start(self, at_s: float) -> None:
        self._in_speech = True
        self._last_partial_total = self._total
        self._last_partial_text = ""
        self._drop_before(round((at_s - PREROLL_S) * self._rate))

    def _close(self, end_s: float) -> list[SttEvent]:
        end_idx = min(self._total, round(end_s * self._rate))
        events = self._transcribe(end_idx, final=True)
        self._in_speech = False
        self._drop_before(end_idx)
        self.finals += len(events)
        return events

    def _maybe_partial(self) -> list[SttEvent]:
        due = (
            self._total - self._last_partial_total
        ) / self._rate >= self._engine.partial_interval_s
        if not (self._in_speech and due):
            return []
        self._last_partial_total = self._total
        events = self._transcribe(self._total, final=False)
        if events and events[0].text == self._last_partial_text:
            return []
        self._last_partial_text = events[0].text if events else self._last_partial_text
        return events

    def _transcribe(self, end_idx: int, *, final: bool) -> list[SttEvent]:
        count = max(0, end_idx - self._buf_start) * BYTES_PER_SAMPLE
        chunk = bytes(self._buf[:count])
        if not chunk:
            return []
        return self._engine.transcribe(chunk, self._rate, self._buf_start / self._rate, final)

    def _drop_before(self, sample_idx: int) -> None:
        drop = min(len(self._buf) // BYTES_PER_SAMPLE, max(0, sample_idx - self._buf_start))
        if drop:
            del self._buf[: drop * BYTES_PER_SAMPLE]
            self._buf_start += drop
