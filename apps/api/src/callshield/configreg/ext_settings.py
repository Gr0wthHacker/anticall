"""Instance, ARI and voice-engine settings written by ``callshield init``.

Kept separate from core.config (shared file). All default to the safe/disabled state.
"""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

VOICE_STT_ENGINES = ("none", "vosk", "faster-whisper")
VOICE_TTS_ENGINES = ("none", "piper")


class InstanceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CALLSHIELD_INSTANCE_", env_file=".env", extra="ignore"
    )

    jurisdiction: str = "UNKNOWN"
    timezone: str = "UTC"
    provider_profile: str = "generic_sip"
    pjsip_path: str = ""
    trunk_name: str = ""
    backup_note: str = ""


class AriSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CALLSHIELD_ARI_", env_file=".env", extra="ignore")

    enabled: bool = False
    url: str = "http://127.0.0.1:8088/ari"
    username: str = "callshield"
    password: SecretStr = SecretStr("")
    app: str = "callshield"


class VoiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CALLSHIELD_VOICE_", env_file=".env", extra="ignore"
    )

    stt_engine: str = "none"
    tts_engine: str = "none"
    model_dir: str = "./var/models"
