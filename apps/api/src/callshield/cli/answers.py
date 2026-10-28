"""Setup answers: parse, validate, and translate to env variables."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from callshield.acknowledgment.service import item_ids
from callshield.configreg.ext_settings import VOICE_STT_ENGINES, VOICE_TTS_ENGINES
from callshield.configreg.gates import (
    CALLBACK_CONFIRMATION_SENTENCE,
    RISKY_FEATURES,
    risky_by_key,
)
from callshield.providers.registry import profile_ids

DEFAULT_DB_URL = "sqlite:///./callshield.db"
DEFAULT_ORIGINS = '["http://127.0.0.1:3000"]'


class AnswersError(ValueError):
    """The answers are incomplete, inconsistent, or unsafe."""


@dataclass
class Answers:
    db_url: str = DEFAULT_DB_URL
    environment: str = "dev"
    admin_email: str = ""
    admin_password: str = ""
    tenant_name: str = "My household"
    jurisdiction: str = "UNKNOWN"
    timezone: str = "UTC"
    provider_profile: str = "generic_sip"
    provider_inputs: dict[str, str] = field(default_factory=dict)
    ari_url: str = "http://127.0.0.1:8088"
    ari_username: str = "callshield"
    ari_app: str = "callshield"
    stt_engine: str = "none"
    tts_engine: str = "none"
    stt_model_dir: str = "./var/models/stt"
    tts_model_dir: str = "./var/models/tts"
    features: dict[str, bool] = field(default_factory=dict)
    acknowledgment_items: list[str] = field(default_factory=list)
    callback_confirmation: str = ""
    cors_origins: str = DEFAULT_ORIGINS
    backup_note: str = ""

    def enabled_keys(self) -> list[str]:
        return [f.key for f in RISKY_FEATURES if self.features.get(f.key)]


_SCALARS = (
    "db_url",
    "environment",
    "admin_email",
    "admin_password",
    "tenant_name",
    "jurisdiction",
    "timezone",
    "provider_profile",
    "ari_url",
    "ari_username",
    "ari_app",
    "stt_engine",
    "tts_engine",
    "stt_model_dir",
    "tts_model_dir",
    "callback_confirmation",
    "cors_origins",
    "backup_note",
)


def answers_from_dict(data: Mapping[str, Any]) -> Answers:
    unknown = set(data) - set(_SCALARS) - {"provider_inputs", "features", "acknowledgment_items"}
    if unknown:
        raise AnswersError("unknown answer keys: " + ", ".join(sorted(unknown)))
    ans = Answers()
    for key in _SCALARS:
        if key in data:
            if not isinstance(data[key], str):
                raise AnswersError(f"{key} must be a string")
            setattr(ans, key, data[key])
    ans.provider_inputs = {str(k): str(v) for k, v in dict(data.get("provider_inputs", {})).items()}
    ans.features = {str(k): bool(v) for k, v in dict(data.get("features", {})).items()}
    ans.acknowledgment_items = [str(i) for i in data.get("acknowledgment_items", [])]
    return ans


def _check_features(ans: Answers) -> None:
    known = risky_by_key()
    bad = set(ans.features) - set(known)
    if bad:
        raise AnswersError("unknown feature keys: " + ", ".join(sorted(bad)))
    enabled = ans.enabled_keys()
    if not enabled:
        return
    missing = [i for i in item_ids() if i not in ans.acknowledgment_items]
    if missing:
        raise AnswersError(
            "enabling a gated feature requires accepting every acknowledgment-v1 item; "
            "missing: " + ", ".join(missing)
        )
    if ans.features.get("callback") and (
        ans.callback_confirmation.strip() != CALLBACK_CONFIRMATION_SENTENCE
    ):
        raise AnswersError("callbacks need the exact confirmation sentence; refusing to enable")


def validate_answers(ans: Answers) -> None:
    if ans.provider_profile not in profile_ids():
        raise AnswersError("unknown provider_profile; choose one of: " + ", ".join(profile_ids()))
    if ans.stt_engine not in VOICE_STT_ENGINES or ans.tts_engine not in VOICE_TTS_ENGINES:
        raise AnswersError("unknown voice engine")
    if ans.environment not in ("dev", "prod"):
        raise AnswersError("environment must be dev or prod")
    if ans.admin_password and len(ans.admin_password) < 12:
        raise AnswersError("admin password must be at least 12 characters")
    if ans.admin_password and not ans.admin_email:
        raise AnswersError("admin_password given without admin_email")
    _check_features(ans)


def _flag(ans: Answers, key: str) -> str:
    return "true" if ans.features.get(key) else "false"


def answers_to_env(ans: Answers, new_secret: Callable[[], str], trunk_name: str) -> dict[str, str]:
    """Env variable map. Generates strong random secrets; everything risky defaults false."""
    env = {
        "CALLSHIELD_ENVIRONMENT": ans.environment,
        "CALLSHIELD_DB_URL": ans.db_url,
        "CALLSHIELD_JWT_SECRET": new_secret(),
        "CALLSHIELD_WEBHOOK_SECRET": new_secret(),
        "CALLSHIELD_CALLBACK_HMAC_SECRET": new_secret(),
        "CALLSHIELD_ASTERISK_ARI_PASSWORD": new_secret(),
        "CALLSHIELD_CORS_ALLOW_ORIGINS": ans.cors_origins,
        "CALLSHIELD_INSTANCE_JURISDICTION": ans.jurisdiction,
        "CALLSHIELD_INSTANCE_TIMEZONE": ans.timezone,
        "CALLSHIELD_INSTANCE_PROVIDER_PROFILE": ans.provider_profile,
        "CALLSHIELD_ASTERISK_TRUNK_ENDPOINT": trunk_name,
        "CALLSHIELD_ASTERISK_ARI_URL": ans.ari_url,
        "CALLSHIELD_ASTERISK_ARI_USERNAME": ans.ari_username,
        "CALLSHIELD_ASTERISK_APP_NAME": ans.ari_app,
        "CALLSHIELD_VOICE_STT_ENGINE": ans.stt_engine,
        "CALLSHIELD_VOICE_TTS_ENGINE": ans.tts_engine,
        "CALLSHIELD_ALLOW_BOOTSTRAP": "false",
    }
    if ans.backup_note:
        env["CALLSHIELD_INSTANCE_BACKUP_NOTE"] = ans.backup_note
    if ans.stt_engine != "none":
        env["CALLSHIELD_VOICE_STT_MODEL_DIR"] = ans.stt_model_dir
    if ans.tts_engine != "none":
        env["CALLSHIELD_VOICE_TTS_MODEL_DIR"] = ans.tts_model_dir
    for feature in RISKY_FEATURES:
        env[feature.env_var] = _flag(ans, feature.key)
    return env
