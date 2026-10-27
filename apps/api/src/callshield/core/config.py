"""Application settings. Every risky capability defaults to OFF (PRD 20.3)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CALLSHIELD_", env_file=".env", extra="ignore")

    environment: str = "dev"  # dev | test | prod
    db_url: str = "sqlite:///./callshield.db"
    db_echo: bool = False

    # --- gated capabilities: ALL default off ---
    outbound_enabled: bool = False
    live_stt_enabled: bool = False
    ai_outbound_enabled: bool = False
    community_enabled: bool = False
    filing_submission_enabled: bool = False

    # --- auth ---
    jwt_secret: SecretStr = SecretStr("dev-insecure-change-me-dev-insecure-change-me")
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 3600
    allow_bootstrap: bool = False  # allow POST /v1/tenants when a tenant already exists
    # TODO(oidc): OIDC login + MFA are not implemented; see callshield.auth.mfa.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None

    # --- provider webhooks ---
    webhook_secret: SecretStr = SecretStr("")
    webhook_replay_window_seconds: int = 300

    # --- outbound safety limits (conservative) ---
    max_call_seconds: int = 600
    outbound_cooldown_seconds: int = 900
    max_attempts_per_case_per_day: int = 3
    max_attempts_per_tenant_per_day: int = 10

    # --- retention defaults (PRD 11.3; design defaults, not legal advice) ---
    retention_metadata_days: int = 90
    retention_recording_days: int = 30
    retention_transcript_days: int = 30
    retention_case_days: int = 365

    def enabled_capabilities(self) -> dict[str, bool]:
        return {
            "outbound": self.outbound_enabled,
            "live_stt": self.live_stt_enabled,
            "ai_outbound": self.ai_outbound_enabled,
            "community": self.community_enabled,
            "filing_submission": self.filing_submission_enabled,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
