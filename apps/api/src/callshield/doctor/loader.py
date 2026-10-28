"""Load every settings class from an env file (or the working directory's .env)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from callshield.callback.config import CallbackSettings
from callshield.configreg.ext_settings import InstanceSettings
from callshield.core.config import Settings
from callshield.doctor.model import LoadedConfig
from callshield.llm.config import LLMSettings
from callshield.telephony.asterisk.settings import AsteriskSettings
from callshield.voice.config import VoiceSettings


def load_config(env_file: Path | None = None) -> LoadedConfig:
    """Raises pydantic.ValidationError when a value is invalid. ``env_file=None`` uses ./.env."""
    kwargs: dict[str, Any] = {} if env_file is None else {"_env_file": str(env_file)}
    return LoadedConfig(
        settings=Settings(**kwargs),
        callback=CallbackSettings(**kwargs),
        llm=LLMSettings(**kwargs),
        instance=InstanceSettings(**kwargs),
        asterisk=AsteriskSettings(**kwargs),
        voice=VoiceSettings(**kwargs),
        env_file=env_file if env_file is not None else _default_env(),
    )


def _default_env() -> Path | None:
    path = Path(".env")
    return path if path.exists() else None


def config_from_app_settings(settings: Settings) -> LoadedConfig:
    """For the API: reuse the running app's Settings, read the rest from the environment."""
    return LoadedConfig(
        settings=settings,
        callback=CallbackSettings(),
        llm=LLMSettings(),
        instance=InstanceSettings(),
        asterisk=AsteriskSettings(),
        voice=VoiceSettings(),
        env_file=_default_env(),
    )
