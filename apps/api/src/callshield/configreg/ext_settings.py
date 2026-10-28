"""Instance settings written by ``callshield init``, plus the voice engine name tuples.

Asterisk (``telephony.asterisk.settings.AsteriskSettings``) and voice
(``voice.config.VoiceSettings``) are defined only in their own packages; the config registry,
wizard and doctor import those real classes. Do not redeclare their variables here.
"""

from __future__ import annotations

from typing import get_args

from pydantic_settings import BaseSettings, SettingsConfigDict

from callshield.voice.config import VoiceSettings

VOICE_STT_ENGINES: tuple[str, ...] = get_args(VoiceSettings.model_fields["stt_engine"].annotation)
VOICE_TTS_ENGINES: tuple[str, ...] = get_args(VoiceSettings.model_fields["tts_engine"].annotation)


class InstanceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CALLSHIELD_INSTANCE_", env_file=".env", extra="ignore"
    )

    jurisdiction: str = "UNKNOWN"
    timezone: str = "UTC"
    provider_profile: str = "generic_sip"
    pjsip_path: str = ""
    backup_note: str = ""
