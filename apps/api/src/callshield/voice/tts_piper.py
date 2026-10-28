"""Piper TTS (local ONNX voices). Output is resampled to the engine `sample_rate`.

Supports both piper-tts APIs: `synthesize_stream_raw` (1.2.x) and chunked `synthesize`
(1.3+, chunks expose `audio_int16_bytes`). The voice file is loaded from a local path only.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from callshield.voice.errors import VoiceEngineUnavailable, import_optional
from callshield.voice.resample import resample_pcm16

logger = logging.getLogger(__name__)


class PiperTts:
    name = "piper"
    local_only = True

    def __init__(self, voice: Any, sample_rate: int = 16000) -> None:
        self._voice = voice
        self.sample_rate = sample_rate
        self.voice_rate = int(voice.config.sample_rate)

    @classmethod
    def load(
        cls, model_path: Path, *, sample_rate: int = 16000, use_cuda: bool = False
    ) -> PiperTts:
        module = import_optional("piper", purpose="the piper TTS engine")
        config = Path(str(model_path) + ".json")
        if not model_path.is_file() or not config.is_file():
            raise VoiceEngineUnavailable(
                f"piper voice not found: need {model_path} and {config}. Download both files "
                "(see docs/deployment/voice.md) into CALLSHIELD_VOICE_TTS_MODEL_DIR."
            )
        voice = module.PiperVoice.load(str(model_path), config_path=str(config), use_cuda=use_cuda)
        return cls(voice, sample_rate)

    def synthesize(self, text: str) -> bytes:
        pcm = b"".join(self._raw_chunks(text))
        logger.debug("piper synthesized chars=%d bytes=%d", len(text), len(pcm))
        return resample_pcm16(pcm, self.voice_rate, self.sample_rate)

    def _raw_chunks(self, text: str) -> list[bytes]:
        if hasattr(self._voice, "synthesize_stream_raw"):
            return [bytes(c) for c in self._voice.synthesize_stream_raw(text)]
        return [bytes(c.audio_int16_bytes) for c in self._voice.synthesize(text)]
