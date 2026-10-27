"""Typed view over ProtectedLine.routing_config (JSON). Unknown keys are rejected on write,
ignored on read so that old rows never break call handling."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from callshield.core.phone import InvalidPhoneNumberError, normalize_e164

FailureFallback = Literal["TRANSFER", "VOICEMAIL", "HANGUP"]
UnknownAction = Literal["SCREEN", "VOICEMAIL", "RING"]
UnwantedAction = Literal["REJECT", "VOICEMAIL"]


class LineRoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_number: str | None = None  # where "ring the user" goes
    unknown_action: UnknownAction = "SCREEN"  # applies to unknown callers
    unscreened_unknown_action: Literal["VOICEMAIL", "RING"] = "VOICEMAIL"
    known_unwanted_action: UnwantedAction = "VOICEMAIL"
    scam_action: UnwantedAction = "VOICEMAIL"
    unknown_after_screening: Literal["TRANSFER", "VOICEMAIL"] = "TRANSFER"
    ai_failure_fallback: FailureFallback = "TRANSFER"
    ai_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    max_hops: int = Field(default=3, ge=1, le=10)
    jurisdiction: str = "UNKNOWN"
    allow_remote_stt: bool = False
    removal_statement_enabled: bool = False
    removal_statement_template_id: str = "standard_v1"

    @field_validator("user_number")
    @classmethod
    def _normalize_user_number(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        try:
            return normalize_e164(value)
        except InvalidPhoneNumberError as exc:
            raise ValueError("user_number must be a valid phone number") from exc


def parse_routing_config(raw: dict[str, Any] | None) -> LineRoutingConfig:
    """Lenient read: drop unknown keys; fall back to safe defaults on invalid values."""
    data = dict(raw or {})
    allowed = set(LineRoutingConfig.model_fields)
    cleaned = {k: v for k, v in data.items() if k in allowed}
    for _ in range(len(cleaned) + 1):
        try:
            return LineRoutingConfig(**cleaned)
        except ValidationError as exc:
            bad = {str(err["loc"][0]) for err in exc.errors() if err["loc"]}
            if not bad & set(cleaned):
                break
            for key in bad:
                cleaned.pop(key, None)  # drop only the invalid fields, keep the rest
    return LineRoutingConfig()
