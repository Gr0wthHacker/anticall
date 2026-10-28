"""Doctor data types and the injectable dependencies (so tests never touch the network)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError
from pydantic_settings import BaseSettings

from callshield.callback.config import CallbackSettings
from callshield.configreg.ext_settings import InstanceSettings
from callshield.core.config import Settings
from callshield.llm.config import LLMSettings
from callshield.telephony.asterisk.settings import AsteriskSettings
from callshield.voice.config import VoiceSettings

Status = Literal["PASS", "WARN", "FAIL", "SKIP"]


@dataclass(frozen=True)
class CheckResult:
    id: str
    title: str
    status: Status
    detail: str = ""
    fix: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "fix": self.fix,
        }


@dataclass(frozen=True)
class DbStatus:
    reachable: bool
    at_head: bool | None = None  # None = could not determine
    detail: str = ""


@dataclass(frozen=True)
class AriProbe:
    reachable: bool
    trunk_state: str | None = None  # best effort, e.g. "online"
    detail: str = ""


@dataclass
class DoctorDeps:
    check_db: Callable[[str], DbStatus]
    probe_ari: Callable[[AsteriskSettings, str], AriProbe]
    module_available: Callable[[str], bool]


@dataclass
class LoadedConfig:
    settings: Settings
    callback: CallbackSettings
    llm: LLMSettings
    instance: InstanceSettings
    asterisk: AsteriskSettings
    voice: VoiceSettings
    env_file: Path | None = None
    extra: dict[str, str] = field(default_factory=dict)

    def all_models(self) -> list[BaseSettings]:
        return [self.settings, self.callback, self.llm, self.instance, self.asterisk, self.voice]


def safe_validation_message(exc: ValidationError) -> str:
    """Field names and reasons only. Pydantic's default text echoes input values (secrets)."""
    parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return "; ".join(parts)
