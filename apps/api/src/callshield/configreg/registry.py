"""Introspect every settings class into rows (env var, type, default, range, who, gate)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings

from callshield.callback.config import CHANGEABLE_BY, CallbackSettings, Who
from callshield.configreg.ext_settings import AriSettings, InstanceSettings, VoiceSettings
from callshield.configreg.meta import META
from callshield.core.config import Settings
from callshield.llm.config import LLMSettings

SETTINGS_CLASSES: tuple[type[BaseSettings], ...] = (
    Settings,
    CallbackSettings,
    LLMSettings,
    InstanceSettings,
    AriSettings,
    VoiceSettings,
)
LOCKED_VALUES = {
    "CALLSHIELD_CALLBACK_REMOTE_AI_PROCESSING": "locked: false",
    "CALLSHIELD_CALLBACK_ALLOWED_ATTESTATION": "locked: A",
}
_WHO_TEXT = {
    Who.USER: "user (dashboard), within range",
    Who.OPERATOR: "operator",
    Who.LOCKED: "nobody (locked)",
    Who.PER_CALL: "chosen per request",
}


@dataclass(frozen=True)
class ConfigRow:
    env: str
    field: str
    group: str
    type_name: str
    default: str
    range_text: str
    who: str
    gate: str
    description: str
    secret: bool
    raw_default: Any


def _is_secret(info: FieldInfo) -> bool:
    ann = info.annotation
    return ann is SecretStr or SecretStr in getattr(ann, "__args__", ())


def _type_name(info: FieldInfo) -> str:
    if _is_secret(info):
        return "secret"
    ann = info.annotation
    name = getattr(ann, "__name__", None)
    if name and not getattr(ann, "__args__", None):
        return str(name)
    return str(ann).replace("typing.", "").replace("<class '", "").replace("'>", "")


def _default_text(info: FieldInfo) -> str:
    value = info.default
    if isinstance(value, SecretStr):
        return "(empty)" if not value.get_secret_value() else "insecure dev placeholder"
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "(unset)"
    if isinstance(value, list):
        return json.dumps(value)
    return str(value) if str(value) != "" else '""'


def _range_text(env: str, info: FieldInfo) -> str:
    if env in LOCKED_VALUES:
        return LOCKED_VALUES[env]
    bounds: dict[str, Any] = {}
    for item in info.metadata:
        for key in ("ge", "gt", "le", "lt"):
            if hasattr(item, key):
                bounds[key] = getattr(item, key)
    low = bounds.get("ge", bounds.get("gt"))
    high = bounds.get("le", bounds.get("lt"))
    if low is not None or high is not None:
        return f"{'' if low is None else low}-{'' if high is None else high}"
    return "true/false" if _type_name(info) == "bool" else ""


def _who(cls: type[BaseSettings], field: str) -> str:
    if cls is CallbackSettings and field in CHANGEABLE_BY:
        return _WHO_TEXT[CHANGEABLE_BY[field]]
    return "operator (.env)"


def _rows_for(cls: type[BaseSettings]) -> list[ConfigRow]:
    prefix = str(cls.model_config.get("env_prefix", ""))
    rows: list[ConfigRow] = []
    for field, info in cls.model_fields.items():
        env = (prefix + field).upper()
        meta = META.get(env)
        rows.append(
            ConfigRow(
                env=env,
                field=field,
                group=cls.__name__,
                type_name=_type_name(info),
                default=_default_text(info),
                range_text=_range_text(env, info),
                who=_who(cls, field),
                gate=meta.gate if meta else "",
                description=meta.description if meta else "",
                secret=_is_secret(info),
                raw_default=info.default,
            )
        )
    return rows


def all_rows() -> list[ConfigRow]:
    rows: list[ConfigRow] = []
    for cls in SETTINGS_CLASSES:
        rows.extend(_rows_for(cls))
    return rows


def row_by_env() -> dict[str, ConfigRow]:
    return {row.env: row for row in all_rows()}
