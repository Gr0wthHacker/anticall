"""Callback configuration: keys, defaults, ranges and who may change them (spec table).

Ceilings are hard-coded constants, not settings: a config value above them fails validation
(`Field(le=...)`), so no environment, tenant override or API call can exceed the spec.
Locked keys (`remote_ai_processing`, `allowed_attestation`) reject any other value even when
set through the environment (construction fails loudly).

Env prefix: CALLSHIELD_CALLBACK_  (e.g. CALLSHIELD_CALLBACK_CALLBACK_ENABLED=true).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator
from pydantic import field_validator as _fv
from pydantic_settings import BaseSettings, SettingsConfigDict

from callshield.callback.disclosure import (
    DEFAULT_TEMPLATE_ID,
    TEMPLATES,
    template_has_recording_notice,
)

# ---- hard ceilings (spec "Caps, rate limits and caller ID") -- not configurable upward
INSTANCE_MAX_PER_NUMBER_30D = 3
CEIL_PER_INBOUND = 2
CEIL_PER_NUMBER_30D = 2
CEIL_PER_USER_DAY = 5
MIN_MINUTES_BETWEEN_ATTEMPTS = 10
AUTHORIZATION_TTL_MINUTES = 30
NUMBER_WINDOW_DAYS = 30
DEV_HMAC_SECRET = "dev-insecure-callback-hmac-secret-change-me"


class Who(StrEnum):
    USER = "user"
    OPERATOR = "operator"
    LOCKED = "locked"
    PER_CALL = "per_call"


# key -> who can change (spec table)
CHANGEABLE_BY: dict[str, Who] = {
    "callback_enabled": Who.USER,
    "callback_window_hours": Who.USER,
    "listen_window_seconds": Who.USER,
    "bare_greeting_disclose_ms": Who.OPERATOR,
    "max_attempts_per_inbound": Who.USER,
    "max_attempts_per_number_30d": Who.USER,
    "max_attempts_per_user_day": Who.USER,
    "max_question_turns": Who.OPERATOR,
    "max_call_seconds": Who.OPERATOR,
    "outbound_recording": Who.PER_CALL,
    "remote_ai_processing": Who.LOCKED,
    "allowed_attestation": Who.LOCKED,
}


def _check_locked(settings_like: Any) -> None:
    if settings_like.remote_ai_processing is not False:
        raise ValueError("remote_ai_processing is locked to false for callbacks")
    if settings_like.allowed_attestation != "A":
        raise ValueError("allowed_attestation is locked to 'A'")


class CallbackSettings(BaseSettings):
    """Instance-level defaults (env). Per-tenant/user overrides are layered on top."""

    model_config = SettingsConfigDict(
        env_prefix="CALLSHIELD_CALLBACK_", env_file=".env", extra="ignore"
    )

    callback_enabled: bool = False
    callback_window_hours: int = Field(72, ge=1, le=168)
    listen_window_seconds: int = Field(10, ge=0, le=10)
    bare_greeting_disclose_ms: int = Field(1500, ge=500, le=3000)
    max_attempts_per_inbound: int = Field(2, ge=1, le=2)
    max_attempts_per_number_30d: int = Field(2, ge=1, le=2)
    max_attempts_per_user_day: int = Field(5, ge=1, le=5)
    max_question_turns: int = Field(4, ge=1, le=4)
    max_call_seconds: int = Field(120, ge=30, le=120)
    outbound_recording: bool = False
    remote_ai_processing: bool = False
    allowed_attestation: str = "A"
    disclosure_template_id: str = DEFAULT_TEMPLATE_ID
    hmac_secret: SecretStr = SecretStr(DEV_HMAC_SECRET)
    storage_root: str = "./var/callback"

    @_fv("remote_ai_processing")
    @classmethod
    def _remote_locked(cls, value: bool) -> bool:
        if value is not False:
            raise ValueError("remote_ai_processing is locked to false for callbacks")
        return value

    @_fv("allowed_attestation")
    @classmethod
    def _attestation_locked(cls, value: str) -> str:
        if value != "A":
            raise ValueError("allowed_attestation is locked to 'A'")
        return value

    @model_validator(mode="after")
    def _template_consistent(self) -> CallbackSettings:
        if self.disclosure_template_id not in TEMPLATES:
            raise ValueError("unknown disclosure_template_id")
        if self.outbound_recording and not template_has_recording_notice(
            self.disclosure_template_id
        ):
            raise ValueError("outbound_recording requires a disclosure with the recording notice")
        return self


class EffectiveSettings(BaseModel):
    """Per-user effective values, validated against the same ranges and locks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    callback_enabled: bool = False
    callback_window_hours: int = Field(72, ge=1, le=168)
    listen_window_seconds: int = Field(10, ge=0, le=10)
    bare_greeting_disclose_ms: int = Field(1500, ge=500, le=3000)
    max_attempts_per_inbound: int = Field(2, ge=1, le=2)
    max_attempts_per_number_30d: int = Field(2, ge=1, le=2)
    max_attempts_per_user_day: int = Field(5, ge=1, le=5)
    max_question_turns: int = Field(4, ge=1, le=4)
    max_call_seconds: int = Field(120, ge=30, le=120)
    outbound_recording: bool = False
    remote_ai_processing: bool = False
    allowed_attestation: str = "A"
    disclosure_template_id: str = DEFAULT_TEMPLATE_ID

    @model_validator(mode="after")
    def _locks_and_template(self) -> EffectiveSettings:
        _check_locked(self)
        if self.outbound_recording and not template_has_recording_notice(
            self.disclosure_template_id
        ):
            raise ValueError("outbound_recording requires a disclosure with the recording notice")
        return self


def base_values(base: CallbackSettings) -> dict[str, Any]:
    return {k: getattr(base, k) for k in EffectiveSettings.model_fields}


def merge_effective(base: CallbackSettings, *layers: dict[str, Any]) -> EffectiveSettings:
    """Env defaults < tenant (operator) overrides < user overrides. Invalid stored values
    (e.g. a hand-edited row above a ceiling) fall back to the base instead of widening."""
    values = base_values(base)
    for layer in layers:
        candidate = {**values, **{k: v for k, v in layer.items() if k in values}}
        try:
            EffectiveSettings(**candidate)
        except ValidationError:
            continue
        values = candidate
    return EffectiveSettings(**values)


class SettingsRejectedError(ValueError):
    def __init__(self, code: str, key: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.key = key


def validate_patch(
    patch: dict[str, Any], *, is_operator: bool, current: EffectiveSettings
) -> dict[str, Any]:
    """Role + range validation for PATCH /v1/callback/settings. Returns accepted values."""
    for key, value in patch.items():
        who = CHANGEABLE_BY.get(key)
        if who is None:
            raise SettingsRejectedError("UNKNOWN_KEY", key, f"unknown setting {key}")
        if who is Who.LOCKED:
            raise SettingsRejectedError("KEY_LOCKED", key, f"{key} is locked for callbacks")
        if who is Who.PER_CALL:
            raise SettingsRejectedError(
                "USE_PER_CALL", key, f"{key} is chosen per callback request"
            )
        if (who is Who.OPERATOR) != is_operator:
            code = "OPERATOR_ONLY" if who is Who.OPERATOR else "USER_ONLY"
            raise SettingsRejectedError(code, key, f"{key} cannot be changed by this role")
        if isinstance(value, bool) != isinstance(current_value(current, key), bool):
            raise SettingsRejectedError("INVALID_VALUE", key, f"{key} has the wrong type")
    try:
        EffectiveSettings(**{**current.model_dump(), **patch})
    except ValidationError as exc:
        first = exc.errors()[0]
        key = str(first["loc"][0]) if first["loc"] else "unknown"
        raise SettingsRejectedError(
            "OUT_OF_RANGE", key, f"{key} is outside its allowed range"
        ) from exc
    return patch


def current_value(current: EffectiveSettings, key: str) -> Any:
    return getattr(current, key)
