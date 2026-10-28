"""Human descriptions and related gates for each env var (consumed by the reference doc)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SettingMeta:
    description: str
    gate: str = ""


def _m(description: str, gate: str = "") -> SettingMeta:
    return SettingMeta(description, gate)


META: dict[str, SettingMeta] = {
    # --- core ---
    "CALLSHIELD_ENVIRONMENT": _m("Deployment mode: dev, test or prod."),
    "CALLSHIELD_DB_URL": _m("SQLAlchemy database URL. SQLite for local use, Postgres for real."),
    "CALLSHIELD_DB_ECHO": _m("Log SQL statements (debug only; may expose data)."),
    "CALLSHIELD_OUTBOUND_ENABLED": _m("Master switch for any outbound call.", "G1,G2,G4,G5"),
    "CALLSHIELD_LIVE_STT_ENABLED": _m("Live speech-to-text on calls.", "G3"),
    "CALLSHIELD_AI_OUTBOUND_ENABLED": _m("AI voice on outbound calls.", "G1,G4,G5"),
    "CALLSHIELD_COMMUNITY_ENABLED": _m("Community signal sharing (not built).", "counsel"),
    "CALLSHIELD_FILING_SUBMISSION_ENABLED": _m("Programmatic agency filing.", "counsel"),
    "CALLSHIELD_JWT_SECRET": _m("Signs login tokens. Must be random and private."),
    "CALLSHIELD_JWT_ALGORITHM": _m("JWT signing algorithm."),
    "CALLSHIELD_ACCESS_TOKEN_TTL_SECONDS": _m("Login token lifetime in seconds."),
    "CALLSHIELD_ALLOW_BOOTSTRAP": _m("Allow creating more tenants through the open endpoint."),
    "CALLSHIELD_OIDC_ISSUER": _m("OIDC issuer (not implemented)."),
    "CALLSHIELD_OIDC_CLIENT_ID": _m("OIDC client id (not implemented)."),
    "CALLSHIELD_OIDC_CLIENT_SECRET": _m("OIDC client secret (not implemented)."),
    "CALLSHIELD_WEBHOOK_SECRET": _m("HMAC secret that signs provider webhooks."),
    "CALLSHIELD_WEBHOOK_REPLAY_WINDOW_SECONDS": _m("How old a signed webhook may be."),
    "CALLSHIELD_WEBHOOK_RECEIPT_RETENTION_DAYS": _m("Days to keep replay nonces."),
    "CALLSHIELD_CORS_ALLOW_ORIGINS": _m("JSON list of browser origins allowed; never '*'."),
    "CALLSHIELD_FILING_UNKNOWN_VERIFY_AFTER_HOURS": _m(
        "Hours before an UNKNOWN filing asks you to verify it."
    ),
    "CALLSHIELD_MAX_CALL_SECONDS": _m("Hard cap on a single call's length."),
    "CALLSHIELD_OUTBOUND_COOLDOWN_SECONDS": _m("Minimum gap between outbound attempts."),
    "CALLSHIELD_MAX_ATTEMPTS_PER_CASE_PER_DAY": _m("Outbound attempts per case per day."),
    "CALLSHIELD_MAX_ATTEMPTS_PER_TENANT_PER_DAY": _m("Outbound attempts per tenant per day."),
    "CALLSHIELD_RETENTION_METADATA_DAYS": _m("Retention for call metadata (design default)."),
    "CALLSHIELD_RETENTION_RECORDING_DAYS": _m("Retention for recordings (design default)."),
    "CALLSHIELD_RETENTION_TRANSCRIPT_DAYS": _m("Retention for transcripts (design default)."),
    "CALLSHIELD_RETENTION_CASE_DAYS": _m("Retention for cases (design default)."),
    # --- callback ---
    "CALLSHIELD_CALLBACK_CALLBACK_ENABLED": _m("Enable the automated callback flow.", "G1-G5"),
    "CALLSHIELD_CALLBACK_CALLBACK_WINDOW_HOURS": _m(
        "How long after an inbound call a callback may be requested."
    ),
    "CALLSHIELD_CALLBACK_LISTEN_WINDOW_SECONDS": _m(
        "Seconds of listening before disclosure.", "G3"
    ),
    "CALLSHIELD_CALLBACK_BARE_GREETING_DISCLOSE_MS": _m(
        "Silence before disclosure after a bare greeting."
    ),
    "CALLSHIELD_CALLBACK_MAX_ATTEMPTS_PER_INBOUND": _m("Callbacks per inbound call (ceiling 2)."),
    "CALLSHIELD_CALLBACK_MAX_ATTEMPTS_PER_NUMBER_30D": _m(
        "Callbacks per number in 30 days (ceiling 2)."
    ),
    "CALLSHIELD_CALLBACK_MAX_ATTEMPTS_PER_USER_DAY": _m("Callbacks per user per day (ceiling 5)."),
    "CALLSHIELD_CALLBACK_MAX_QUESTION_TURNS": _m("Scripted question turns per callback."),
    "CALLSHIELD_CALLBACK_MAX_CALL_SECONDS": _m("Maximum callback duration."),
    "CALLSHIELD_CALLBACK_OUTBOUND_RECORDING": _m(
        "Record callbacks (needs a recording notice).", "G3"
    ),
    "CALLSHIELD_CALLBACK_REMOTE_AI_PROCESSING": _m("Locked to false: no remote AI on callbacks."),
    "CALLSHIELD_CALLBACK_ALLOWED_ATTESTATION": _m(
        "Locked to A: only A-attested inbound calls.", "G2"
    ),
    "CALLSHIELD_CALLBACK_DISCLOSURE_TEMPLATE_ID": _m("Which built-in disclosure script is spoken."),
    "CALLSHIELD_CALLBACK_HMAC_SECRET": _m("Key that signs callback authorizations."),
    "CALLSHIELD_CALLBACK_STORAGE_ROOT": _m("Directory for callback artifacts."),
    # --- llm ---
    "CALLSHIELD_LLM_ENABLED": _m("Enable LLM classification/summaries.", "G5"),
    "CALLSHIELD_LLM_BASE_URL": _m("OpenAI-compatible endpoint (prefer local)."),
    "CALLSHIELD_LLM_MODEL": _m("Model name at that endpoint."),
    "CALLSHIELD_LLM_API_KEY": _m("API key for the endpoint, if any."),
    "CALLSHIELD_LLM_TIMEOUT_SECONDS": _m("Seconds before an LLM call is abandoned."),
    # --- instance / ari / voice ---
    "CALLSHIELD_INSTANCE_JURISDICTION": _m("Your jurisdiction code (e.g. US-CA), informational."),
    "CALLSHIELD_INSTANCE_TIMEZONE": _m("IANA timezone for calling-hour checks."),
    "CALLSHIELD_INSTANCE_PROVIDER_PROFILE": _m("Provider profile id chosen in init."),
    "CALLSHIELD_INSTANCE_PJSIP_PATH": _m("Path of the generated pjsip snippet."),
    "CALLSHIELD_INSTANCE_TRUNK_NAME": _m("PJSIP trunk/endpoint name (used by doctor)."),
    "CALLSHIELD_INSTANCE_BACKUP_NOTE": _m("Free text: where your backups go (checked by doctor)."),
    "CALLSHIELD_ARI_ENABLED": _m("Connect to Asterisk ARI.", "G5"),
    "CALLSHIELD_ARI_URL": _m("ARI base URL. Keep it on localhost."),
    "CALLSHIELD_ARI_USERNAME": _m("ARI user."),
    "CALLSHIELD_ARI_PASSWORD": _m("ARI password; must equal ari.conf."),
    "CALLSHIELD_ARI_APP": _m("Stasis application name."),
    "CALLSHIELD_VOICE_STT_ENGINE": _m("Speech-to-text engine: none, vosk, faster-whisper."),
    "CALLSHIELD_VOICE_TTS_ENGINE": _m("Text-to-speech engine: none, piper."),
    "CALLSHIELD_VOICE_MODEL_DIR": _m("Directory holding downloaded voice models."),
}
