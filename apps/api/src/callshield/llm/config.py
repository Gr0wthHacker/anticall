"""LLM settings (env prefix CALLSHIELD_LLM_). Disabled unless explicitly turned on."""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from callshield.llm.client import LLMClient
from callshield.llm.openai_compat import OpenAICompatibleClient


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CALLSHIELD_LLM_", env_file=".env", extra="ignore")

    enabled: bool = False
    base_url: str = ""  # e.g. a local llama.cpp / Ollama / vLLM OpenAI-compatible endpoint
    model: str = ""
    api_key: SecretStr | None = None
    timeout_seconds: float = 2.0


def build_llm_client(config: LLMSettings | None = None) -> LLMClient | None:
    """None unless explicitly enabled AND fully configured."""
    cfg = config or LLMSettings()
    if not cfg.enabled or not cfg.base_url or not cfg.model:
        return None
    key = cfg.api_key.get_secret_value() if cfg.api_key else None
    return OpenAICompatibleClient(cfg.base_url, cfg.model, api_key=key)
