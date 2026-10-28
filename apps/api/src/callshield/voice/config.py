"""Voice settings. Everything is off by default; env prefix CALLSHIELD_VOICE_.

Example (all local, nothing downloaded automatically):
    CALLSHIELD_VOICE_STT_ENGINE=faster-whisper
    CALLSHIELD_VOICE_STT_MODEL=small.en
    CALLSHIELD_VOICE_STT_MODEL_DIR=/opt/callshield/models/stt
    CALLSHIELD_VOICE_TTS_ENGINE=piper
    CALLSHIELD_VOICE_TTS_MODEL_DIR=/opt/callshield/models/tts
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_WHISPER_MODEL = "small.en"
DEFAULT_VOSK_MODEL = "vosk-model-small-en-us-0.15"
DEFAULT_PIPER_VOICE = "en_US-lessac-medium"
_HELP = "Install with pip install 'callshield[voice]'; models: docs/deployment/voice.md."


class VoiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CALLSHIELD_VOICE_", env_file=".env", extra="ignore"
    )

    stt_engine: Literal["faster-whisper", "vosk", "none"] = "none"
    stt_model: str | None = None  # whisper size/name, or the vosk model directory name
    stt_model_dir: str | None = None
    stt_device: Literal["cpu", "cuda"] = "cpu"
    stt_compute_type: str | None = None  # default: int8 on cpu, float16 on cuda
    stt_language: str = "en"
    tts_engine: Literal["piper", "none"] = "none"
    tts_voice: str = DEFAULT_PIPER_VOICE
    tts_model_dir: str | None = None
    tts_model_path: str | None = None  # explicit .onnx path (overrides dir + voice)
    vad: Literal["energy", "silero"] = "energy"
    vad_model_path: str | None = None
    vad_energy_threshold: float = 400.0
    sample_rate: Literal[8000, 16000] = 16000

    @field_validator("sample_rate", mode="before")
    @classmethod
    def _sample_rate_from_env(cls, value: object) -> object:
        """Env values are strings; Literal[8000, 16000] would otherwise reject "16000"."""
        if isinstance(value, str) and value.strip().isdigit():
            return int(value)
        return value

    @model_validator(mode="after")
    def _engines_have_what_they_need(self) -> VoiceSettings:
        if self.stt_engine == "vosk" and not self.stt_model_dir:
            raise ValueError(
                "stt_engine=vosk needs CALLSHIELD_VOICE_STT_MODEL_DIR (folder containing the "
                f"unpacked model, e.g. {DEFAULT_VOSK_MODEL}). {_HELP}"
            )
        if self.tts_engine == "piper" and not (self.tts_model_path or self.tts_model_dir):
            raise ValueError(
                "tts_engine=piper needs CALLSHIELD_VOICE_TTS_MODEL_DIR (folder with "
                f"<voice>.onnx and <voice>.onnx.json) or TTS_MODEL_PATH. {_HELP}"
            )
        if self.vad == "silero" and not self.vad_model_path:
            raise ValueError(
                f"vad=silero needs CALLSHIELD_VOICE_VAD_MODEL_PATH (silero_vad.onnx). {_HELP}"
            )
        return self

    @property
    def effective_stt_model(self) -> str:
        if self.stt_model:
            return self.stt_model
        return DEFAULT_VOSK_MODEL if self.stt_engine == "vosk" else DEFAULT_WHISPER_MODEL

    @property
    def effective_compute_type(self) -> str:
        return self.stt_compute_type or ("float16" if self.stt_device == "cuda" else "int8")

    def stt_model_path(self) -> Path | None:
        """Local directory holding the configured STT model, if one is configured."""
        if not self.stt_model_dir:
            return None
        return Path(self.stt_model_dir) / self.effective_stt_model

    def piper_model_path(self) -> Path | None:
        if self.tts_model_path:
            return Path(self.tts_model_path)
        if self.tts_model_dir:
            return Path(self.tts_model_dir) / f"{self.tts_voice}.onnx"
        return None
