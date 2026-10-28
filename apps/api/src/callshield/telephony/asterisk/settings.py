"""Asterisk integration settings (env prefix CALLSHIELD_ASTERISK_). Disabled by default.

There is deliberately NO caller-ID field: outbound caller ID comes only from a
`VerifiedCaller` (see `callback.lines`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


class AsteriskSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CALLSHIELD_ASTERISK_", env_file=".env", extra="ignore", populate_by_name=True
    )

    enabled: bool = False
    # --- ARI control plane: keep on localhost / an internal network ---
    # The CALLSHIELD_ARI_* names written by `callshield init` are accepted as fallbacks.
    ari_url: str = Field(
        default="http://127.0.0.1:8088",
        validation_alias=AliasChoices(
            "CALLSHIELD_ASTERISK_ARI_URL", "CALLSHIELD_ARI_URL", "ari_url"
        ),
    )
    ari_username: str = Field(
        default="callshield",
        validation_alias=AliasChoices(
            "CALLSHIELD_ASTERISK_ARI_USERNAME", "CALLSHIELD_ARI_USERNAME", "ari_username"
        ),
    )
    ari_password: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "CALLSHIELD_ASTERISK_ARI_PASSWORD", "CALLSHIELD_ARI_PASSWORD", "ari_password"
        ),
    )
    app_name: str = Field(  # inbound Stasis app
        default="callshield",
        validation_alias=AliasChoices(
            "CALLSHIELD_ASTERISK_APP_NAME", "CALLSHIELD_ARI_APP", "app_name"
        ),
    )
    outbound_app_name: str = "callshield-out"  # callback legs (separate app, no event overlap)
    # --- media plane (RTP); Asterisk sends to host:port, we bind `external_media_bind` ---
    external_media_host: str = "127.0.0.1"
    external_media_bind: str = "127.0.0.1"
    external_media_port: int = Field(default=4000, ge=1024, le=65000)
    external_media_port_count: int = Field(default=100, ge=1, le=2000)
    codec: Literal["ulaw", "slin16"] = "ulaw"
    # --- trunk: any PJSIP endpoint name; the dial string is a template ---
    trunk_endpoint: str = "trunk"
    dial_format: str = "PJSIP/{number}@{trunk}"
    dial_strip_plus: bool = False
    user_dial_format: str = "PJSIP/{number}@{trunk}"  # ringing the user (call forwarding)
    # --- dialplan hand-offs (see integrations/asterisk/extensions.conf) ---
    voicemail_context: str = "callshield-route"
    voicemail_extension: str = "voicemail"
    fallback_context: str = "callshield-route"
    fallback_extension: str = "ring-user"
    # --- timing / limits ---
    ring_timeout_seconds: int = Field(default=45, ge=5, le=120)
    max_call_seconds_cap: int = Field(default=120, ge=1, le=120)
    screening_no_speech_seconds: float = Field(default=8.0, ge=1.0, le=30.0)
    reconnect_min_seconds: float = Field(default=0.5, gt=0)
    reconnect_max_seconds: float = Field(default=30.0, gt=0)
    # --- recordings (callbacks, post-disclosure only) ---
    recording_dir: Path | None = None
    # --- runner health endpoint (0 disables); bind to loopback ---
    health_host: str = "127.0.0.1"
    health_port: int = Field(default=8089, ge=0, le=65535)

    @field_validator("ari_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("ari_url must be an http(s) URL")
        if parts.username or parts.password:
            raise ValueError("put ARI credentials in ari_username/ari_password, not the URL")
        trimmed = value.rstrip("/")
        return trimmed[: -len("/ari")] if trimmed.endswith("/ari") else trimmed  # legacy form

    @field_validator("dial_format", "user_dial_format")
    @classmethod
    def _has_number(cls, value: str) -> str:
        if "{number}" not in value:
            raise ValueError("dial_format must contain {number}")
        return value

    def exposure_warnings(self) -> list[str]:
        """Human-readable deployment warnings (logged by the runner, never fatal)."""
        out: list[str] = []
        parts = urlsplit(self.ari_url)
        if parts.hostname not in LOOPBACK and parts.scheme == "http":
            out.append("ARI is reachable over plain http on a non-loopback host; use TLS/VPN")
        if not self.ari_password.get_secret_value():
            out.append("ari_password is empty")
        if self.external_media_bind not in LOOPBACK:
            out.append("RTP is bound to a non-loopback address; firewall the media port range")
        return out

    def ws_url(self, apps: tuple[str, ...]) -> str:
        parts = urlsplit(self.ari_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return f"{scheme}://{parts.netloc}{parts.path}/ari/events?app={','.join(apps)}"
