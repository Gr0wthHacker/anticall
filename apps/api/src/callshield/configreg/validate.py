"""Cross-field validation beyond what each settings class already enforces."""

from __future__ import annotations

from callshield.configreg.ext_settings import VOICE_STT_ENGINES, VOICE_TTS_ENGINES
from callshield.doctor.model import LoadedConfig
from callshield.providers.registry import profile_ids


def semantic_problems(cfg: LoadedConfig) -> list[str]:
    problems: list[str] = []
    if cfg.llm.enabled and not (cfg.llm.base_url and cfg.llm.model):
        problems.append("CALLSHIELD_LLM_ENABLED needs CALLSHIELD_LLM_BASE_URL and _MODEL")
    if cfg.ari.enabled and not cfg.ari.password.get_secret_value():
        problems.append("CALLSHIELD_ARI_ENABLED needs CALLSHIELD_ARI_PASSWORD")
    if cfg.voice.stt_engine not in VOICE_STT_ENGINES:
        problems.append(f"CALLSHIELD_VOICE_STT_ENGINE must be one of {VOICE_STT_ENGINES}")
    if cfg.voice.tts_engine not in VOICE_TTS_ENGINES:
        problems.append(f"CALLSHIELD_VOICE_TTS_ENGINE must be one of {VOICE_TTS_ENGINES}")
    if cfg.instance.provider_profile not in profile_ids():
        problems.append("CALLSHIELD_INSTANCE_PROVIDER_PROFILE is not a shipped profile id")
    if cfg.settings.environment not in ("dev", "test", "prod"):
        problems.append("CALLSHIELD_ENVIRONMENT must be dev, test or prod")
    if cfg.callback.callback_enabled and not cfg.ari.enabled:
        problems.append("callbacks are enabled but ARI telephony is off, so none can be placed")
    return problems
