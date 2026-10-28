"""Vosk STT (local Kaldi models): true streaming partial/final results.

Models are loaded from a local directory only (`Model(model_path=...)`); Vosk's name-based
auto-download is never used. Never logs audio or text.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from callshield.voice.base import SttEvent
from callshield.voice.errors import VoiceEngineUnavailable, import_optional
from callshield.voice.pcm import BYTES_PER_SAMPLE
from callshield.voice.resample import resample_pcm16

logger = logging.getLogger(__name__)
VOSK_RATE = 16000


class VoskStt:
    name = "vosk"
    local_only = True

    def __init__(self, module: Any, model: Any) -> None:
        self._module = module
        self._model = model

    @classmethod
    def load(cls, model_path: Path) -> VoskStt:
        module = import_optional("vosk", purpose="the vosk STT engine")
        if not model_path.is_dir():
            raise VoiceEngineUnavailable(
                f"vosk model folder not found: {model_path}. Download and unzip a model from "
                "the official list (see docs/deployment/voice.md) into "
                "CALLSHIELD_VOICE_STT_MODEL_DIR."
            )
        if hasattr(module, "SetLogLevel"):
            module.SetLogLevel(-1)
        return cls(module, module.Model(model_path=str(model_path)))

    def new_stream(self, sample_rate: int = 16000) -> VoskStream:
        recognizer = self._module.KaldiRecognizer(self._model, VOSK_RATE)
        if hasattr(recognizer, "SetWords"):
            recognizer.SetWords(True)
        return VoskStream(recognizer, sample_rate)


class VoskStream:
    def __init__(self, recognizer: Any, sample_rate: int) -> None:
        self._rec = recognizer
        self._rate = sample_rate
        self._samples = 0
        self._last_partial = ""
        self.finals = 0

    @property
    def clock_s(self) -> float:
        return self._samples / self._rate

    def feed(self, pcm16: bytes) -> list[SttEvent]:
        usable = pcm16[: len(pcm16) - len(pcm16) % BYTES_PER_SAMPLE]
        self._samples += len(usable) // BYTES_PER_SAMPLE
        if self._rate != VOSK_RATE:
            usable = resample_pcm16(usable, self._rate, VOSK_RATE)
        if self._rec.AcceptWaveform(usable):
            return self._final(json.loads(self._rec.Result()))
        partial = str(json.loads(self._rec.PartialResult()).get("partial", "")).strip()
        if not partial or partial == self._last_partial:
            return []
        self._last_partial = partial
        return [SttEvent(partial, False, 0.0, self.clock_s)]

    def finish(self) -> list[SttEvent]:
        events = self._final(json.loads(self._rec.FinalResult()))
        logger.debug("vosk stream closed finals=%d audio_s=%.1f", self.finals, self.clock_s)
        return events

    def _final(self, result: dict[str, Any]) -> list[SttEvent]:
        self._last_partial = ""
        text = str(result.get("text", "")).strip()
        if not text:
            return []
        words = result.get("result") or []
        if words:
            start, end = float(words[0]["start"]), float(words[-1]["end"])
        else:
            start, end = 0.0, self.clock_s
        self.finals += 1
        return [SttEvent(text, True, start, end)]
