"""Application settings. Every risky capability defaults to OFF (PRD 20.3)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
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
    webhook_receipt_retention_days: int = Field(
        default=30, ge=1
    )  # webhook.purge_receipts deletes older nonces

    # --- CORS: empty list = no CORS headers at all. Set as a JSON list in the environment. ---
    cors_allow_origins: list[str] = []

    # --- filing: an UNKNOWN submission older than this asks the user to verify it ---
    filing_unknown_verify_after_hours: int = Field(default=24, ge=1)

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

    @field_validator("cors_allow_origins")
    @classmethod
    def _explicit_origins_only(cls, origins: list[str]) -> list[str]:
        cleaned = [o.strip().rstrip("/") for o in origins if o.strip()]
        if "*" in cleaned:
            raise ValueError("cors_allow_origins must list explicit origins, never '*'")
        return cleaned

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
