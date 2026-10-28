"""Individual doctor checks. Each returns one CheckResult and never raises or leaks secrets."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from callshield.doctor.model import CheckResult, DoctorDeps, LoadedConfig
from callshield.providers.registry import get_profile

CheckFn = Callable[[LoadedConfig, DoctorDeps], CheckResult]
REQUIRED_MODULES = ("fastapi", "sqlalchemy", "pydantic", "httpx", "alembic", "argon2", "jwt")
ENGINE_MODULES = {
    "vosk": ("vosk",),
    "faster-whisper": ("faster_whisper",),
    "piper": ("piper",),
}
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
_BAD_SECRETS = ("change_me", "changeme", "dev-insecure")


def _r(cid: str, title: str, status: str, detail: str = "", fix: str = "") -> CheckResult:
    return CheckResult(cid, title, status, detail, fix)  # type: ignore[arg-type]


def is_weak_secret(value: str) -> bool:
    lowered = value.lower()
    return len(value) < 24 or any(bad in lowered for bad in _BAD_SECRETS)


def risky_enabled(cfg: LoadedConfig) -> list[str]:
    flags = {
        "CALLSHIELD_ASTERISK_ENABLED": cfg.asterisk.enabled,
        "CALLSHIELD_LIVE_STT_ENABLED": cfg.settings.live_stt_enabled,
        "CALLSHIELD_OUTBOUND_ENABLED": cfg.settings.outbound_enabled,
        "CALLSHIELD_AI_OUTBOUND_ENABLED": cfg.settings.ai_outbound_enabled,
        "CALLSHIELD_COMMUNITY_ENABLED": cfg.settings.community_enabled,
        "CALLSHIELD_FILING_SUBMISSION_ENABLED": cfg.settings.filing_submission_enabled,
        "CALLSHIELD_CALLBACK_CALLBACK_ENABLED": cfg.callback.callback_enabled,
        "CALLSHIELD_CALLBACK_OUTBOUND_RECORDING": cfg.callback.outbound_recording,
        "CALLSHIELD_LLM_ENABLED": cfg.llm.enabled,
    }
    return [name for name, on in flags.items() if on]


def check_python(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    v = sys.version_info
    if v >= (3, 11):
        return _r("python", "Python version", "PASS", f"Python {v.major}.{v.minor}")
    return _r("python", "Python version", "FAIL", f"Python {v.major}.{v.minor}", "Install 3.11+.")


def check_dependencies(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    missing = [m for m in REQUIRED_MODULES if not deps.module_available(m)]
    if missing:
        return _r(
            "deps",
            "Core dependencies",
            "FAIL",
            "missing: " + ", ".join(missing),
            "pip install -e 'apps/api[dev]'",
        )
    if cfg.settings.db_url.startswith("postgres") and not deps.module_available("psycopg"):
        return _r(
            "deps",
            "Core dependencies",
            "FAIL",
            "psycopg missing for Postgres",
            "pip install -e 'apps/api[postgres]'",
        )
    if cfg.asterisk.enabled and not deps.module_available("aiohttp"):
        return _r(
            "deps",
            "Core dependencies",
            "FAIL",
            "aiohttp missing for ARI",
            "pip install -e 'apps/api[asterisk]'",
        )
    return _r("deps", "Core dependencies", "PASS", "required packages present")


def check_extras(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    engines = [e for e in (cfg.voice.stt_engine, cfg.voice.tts_engine) if e != "none"]
    if not engines:
        return _r("voice_extras", "Voice engine packages", "SKIP", "no voice engine selected")
    missing = [
        m for e in engines for m in ENGINE_MODULES.get(e, ()) if not deps.module_available(m)
    ]
    if missing:
        return _r(
            "voice_extras",
            "Voice engine packages",
            "FAIL",
            "missing: " + ", ".join(missing),
            "pip install -e 'apps/api[voice]'",
        )
    return _r("voice_extras", "Voice engine packages", "PASS", ", ".join(engines))


def check_database(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    status = deps.check_db(cfg.settings.db_url)
    if not status.reachable:
        return _r(
            "database",
            "Database reachable",
            "FAIL",
            status.detail,
            "Check CALLSHIELD_DB_URL and that the server is running.",
        )
    return _r("database", "Database reachable", "PASS", "connected")


def check_migrations(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    status = deps.check_db(cfg.settings.db_url)
    if not status.reachable:
        return _r("migrations", "Migrations at head", "SKIP", "database not reachable")
    if status.at_head is False:
        return _r(
            "migrations", "Migrations at head", "FAIL", status.detail, "Run: callshield db upgrade"
        )
    if status.at_head is None:
        return _r(
            "migrations",
            "Migrations at head",
            "WARN",
            status.detail,
            "Run `alembic upgrade head` from apps/api.",
        )
    return _r("migrations", "Migrations at head", "PASS", status.detail)


def check_secrets(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    named = {
        "CALLSHIELD_JWT_SECRET": cfg.settings.jwt_secret.get_secret_value(),
        "CALLSHIELD_WEBHOOK_SECRET": cfg.settings.webhook_secret.get_secret_value(),
    }
    if cfg.callback.callback_enabled:
        named["CALLSHIELD_CALLBACK_HMAC_SECRET"] = cfg.callback.hmac_secret.get_secret_value()
    if cfg.asterisk.enabled:
        named["CALLSHIELD_ASTERISK_ARI_PASSWORD"] = cfg.asterisk.ari_password.get_secret_value()
    bad = sorted(k for k, v in named.items() if is_weak_secret(v))
    if not bad:
        return _r("secrets", "Secrets set and non-default", "PASS", "all required secrets set")
    strict = cfg.settings.environment == "prod" or bool(risky_enabled(cfg))
    return _r(
        "secrets",
        "Secrets set and non-default",
        "FAIL" if strict else "WARN",
        "weak, default or missing: " + ", ".join(bad),
        "Run `callshield init` or set each to `openssl rand -hex 32`.",
    )


def check_ari(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    if not cfg.asterisk.enabled:
        return _r("ari", "Asterisk ARI reachable", "SKIP", "ARI disabled")
    probe = deps.probe_ari(cfg.asterisk, cfg.asterisk.trunk_endpoint)
    if probe.reachable:
        return _r("ari", "Asterisk ARI reachable", "PASS", probe.detail)
    return _r(
        "ari",
        "Asterisk ARI reachable",
        "FAIL",
        probe.detail,
        "Start Asterisk; match CALLSHIELD_ASTERISK_ARI_URL/_USERNAME/_PASSWORD to ari.conf.",
    )


def check_trunk(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    title = "Trunk status (best effort)"
    if not cfg.asterisk.enabled:
        return _r("trunk", title, "SKIP", "ARI disabled")
    probe = deps.probe_ari(cfg.asterisk, cfg.asterisk.trunk_endpoint)
    if not probe.reachable:
        return _r("trunk", title, "SKIP", "ARI not reachable")
    if probe.trunk_state is None:
        return _r(
            "trunk",
            title,
            "WARN",
            "state unknown",
            "Set CALLSHIELD_ASTERISK_TRUNK_ENDPOINT; check `pjsip show registrations`.",
        )
    if probe.trunk_state.lower() in ("online", "reachable"):
        return _r("trunk", title, "PASS", probe.trunk_state)
    return _r(
        "trunk",
        title,
        "WARN",
        probe.trunk_state,
        "Check credentials, firewall and provider portal.",
    )


def check_models(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    voice = cfg.voice
    if voice.stt_engine == "none" and voice.tts_engine == "none":
        return _r("models", "Voice models on disk", "SKIP", "no voice engine selected")
    missing: list[str] = []
    if voice.stt_engine != "none":
        stt = voice.stt_model_path()
        if stt is None:
            return _r(
                "models",
                "Voice models on disk",
                "WARN",
                f"{voice.stt_engine}: CALLSHIELD_VOICE_STT_MODEL_DIR unset, cannot check",
                "Set CALLSHIELD_VOICE_STT_MODEL_DIR; run: callshield voice models",
            )
        if not _has_files(stt):
            missing.append(f"stt {stt}")
    if voice.tts_engine != "none":
        tts = voice.piper_model_path()
        if tts is None or not (tts.is_file() and Path(f"{tts}.json").is_file()):
            missing.append(f"tts {tts}(.json)")
    if missing:
        return _r(
            "models",
            "Voice models on disk",
            "FAIL",
            "not found: " + ", ".join(missing),
            "Run: callshield voice models  (manual download steps)",
        )
    return _r("models", "Voice models on disk", "PASS", voice.stt_engine + "/" + voice.tts_engine)


def _has_files(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def check_ports(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    problems = []
    host = urlparse(cfg.asterisk.ari_url).hostname or ""
    if host and host not in LOOPBACK:
        problems.append(f"ARI URL host '{host}' is not localhost")
    pjsip = cfg.instance.pjsip_path
    if pjsip and Path(pjsip).is_file() and "bind=0.0.0.0" in Path(pjsip).read_text(errors="ignore"):
        problems.append("pjsip transport binds 0.0.0.0")
    if problems:
        return _r(
            "ports",
            "Ports bound to localhost",
            "WARN",
            "; ".join(problems),
            "Bind ARI/HTTP to 127.0.0.1; expose SIP only to your provider via firewall.",
        )
    return _r("ports", "Ports bound to localhost", "PASS", "no public bindings detected")


def check_risky(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    on = risky_enabled(cfg)
    if not on:
        return _r("risky", "Risky features", "PASS", "all gated features are off")
    return _r(
        "risky",
        "Risky features",
        "WARN",
        "enabled: " + ", ".join(on),
        "Each needs its release gate cleared: docs/deployment/going-live-checklist.md",
    )


def check_backup(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    s = cfg.settings
    notes = [f"retention: metadata {s.retention_metadata_days}d, cases {s.retention_case_days}d"]
    if s.db_url.startswith("sqlite"):
        notes.append("SQLite file: copy it to back up")
    if not cfg.instance.backup_note:
        return _r(
            "backup",
            "Backup and retention",
            "WARN",
            "; ".join(notes),
            "Decide where backups go and set CALLSHIELD_INSTANCE_BACKUP_NOTE.",
        )
    return _r("backup", "Backup and retention", "PASS", "; ".join(notes))


def check_timezone(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    name = cfg.instance.timezone
    if name.upper() != "UTC":
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return _r(
                "timezone",
                "Timezone",
                "WARN",
                f"unknown timezone '{name}'",
                "Use an IANA name like America/Chicago (pip install tzdata on Windows).",
            )
    return _r("timezone", "Timezone", "PASS", f"{name}; system clock is not verified remotely")


def check_jurisdiction(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    if cfg.instance.jurisdiction.upper() == "UNKNOWN":
        return _r(
            "jurisdiction",
            "Jurisdiction set",
            "WARN",
            "UNKNOWN denies live speech, recording and transcripts",
            "Set CALLSHIELD_INSTANCE_JURISDICTION (e.g. US-CA); counsel must still review.",
        )
    return _r("jurisdiction", "Jurisdiction set", "PASS", cfg.instance.jurisdiction)


def check_env_file(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    path = cfg.env_file
    if path is None or not path.exists():
        return _r(
            "env_file",
            "Env file",
            "WARN",
            "no .env file found",
            "Run `callshield init` to create one.",
        )
    if os.name == "posix" and path.stat().st_mode & 0o077:
        return _r(
            "env_file",
            "Env file",
            "WARN",
            f"{path.name} is readable by other users",
            f"chmod 600 {path.name}",
        )
    return _r("env_file", "Env file", "PASS", path.name)


def check_bootstrap(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    if cfg.settings.allow_bootstrap:
        return _r(
            "bootstrap",
            "Open tenant bootstrap",
            "WARN",
            "CALLSHIELD_ALLOW_BOOTSTRAP=true",
            "Set it to false after creating your admin.",
        )
    return _r("bootstrap", "Open tenant bootstrap", "PASS", "closed")


def check_profile(cfg: LoadedConfig, deps: DoctorDeps) -> CheckResult:
    try:
        profile = get_profile(cfg.instance.provider_profile)
    except KeyError:
        return _r(
            "profile",
            "Provider profile",
            "WARN",
            "unknown profile id",
            "Use `callshield init` to pick one of the shipped profiles.",
        )
    if profile.verified or profile.local_only or not cfg.asterisk.enabled:
        return _r("profile", "Provider profile", "PASS", profile.id)
    return _r(
        "profile",
        "Provider profile",
        "WARN",
        f"{profile.id} is an UNVERIFIED template",
        f"Confirm values and terms with your provider: {profile.acceptable_use_url or 'ask them'}",
    )


ALL_CHECKS: tuple[CheckFn, ...] = (
    check_python,
    check_dependencies,
    check_extras,
    check_env_file,
    check_database,
    check_migrations,
    check_secrets,
    check_bootstrap,
    check_ari,
    check_trunk,
    check_profile,
    check_models,
    check_ports,
    check_risky,
    check_jurisdiction,
    check_backup,
    check_timezone,
)
