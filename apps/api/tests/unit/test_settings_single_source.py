"""One settings class per variable; `callshield init` output round-trips through them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import AliasChoices, SecretStr
from pydantic_settings import BaseSettings

from callshield.cli.main import main
from callshield.configreg.registry import SETTINGS_CLASSES
from callshield.doctor.loader import load_config
from callshield.providers.render_env import known_env_names
from callshield.telephony.asterisk.settings import AsteriskSettings
from callshield.voice.config import VoiceSettings
from tests.unit.test_cli_init import Script, _ctx


def _accepted_names(cls: type[BaseSettings]) -> list[str]:
    """Every env var name this class reads (primary names and documented aliases)."""
    prefix = str(cls.model_config.get("env_prefix", ""))
    names: list[str] = []
    for field, info in cls.model_fields.items():
        alias = info.validation_alias
        if isinstance(alias, AliasChoices):
            names += [str(c).upper() for c in alias.choices if str(c) != field]
        elif isinstance(alias, str):
            names.append(alias.upper())
        else:
            names.append((prefix + field).upper())
    return names


def test_registry_uses_the_real_asterisk_and_voice_classes() -> None:
    assert AsteriskSettings in SETTINGS_CLASSES and VoiceSettings in SETTINGS_CLASSES
    assert [c.__name__ for c in SETTINGS_CLASSES].count("VoiceSettings") == 1


def test_no_env_var_is_declared_by_two_settings_classes() -> None:
    owners: dict[str, str] = {}
    clashes: list[str] = []
    for cls in SETTINGS_CLASSES:
        for name in _accepted_names(cls):
            if name in owners:
                clashes.append(f"{name}: {owners[name]} and {cls.__name__}")
            owners[name] = cls.__name__
    assert clashes == []


def test_every_settings_class_reads_dotenv() -> None:
    missing = [c.__name__ for c in SETTINGS_CLASSES if not c.model_config.get("env_file")]
    assert missing == []


def _plain(value: object) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return json.dumps(value)
    return str(value)


def test_init_output_round_trips_through_the_real_settings(tmp_path: Path) -> None:
    answers = tmp_path / "answers.json"
    answers.write_text(
        json.dumps(
            {
                "db_url": f"sqlite:///{(tmp_path / 'cs.db').as_posix()}",
                "provider_profile": "telnyx",
                "provider_inputs": {
                    "trunk_name": "tnx",
                    "sip_host": "sip.example.test",
                    "auth_mode": "ip_auth",
                    "ip_auth_match": "192.0.2.0/24",
                },
                "ari_url": "http://127.0.0.1:9088",
                "ari_username": "shield",
                "ari_app": "shieldapp",
                "stt_engine": "vosk",
                "tts_engine": "piper",
            }
        ),
        encoding="utf-8",
    )
    argv = ["init", "--non-interactive", "--answers", str(answers), "--output-dir", str(tmp_path)]
    assert main(argv, _ctx(Script())) == 0
    env_path = tmp_path / ".env"
    written = {
        line.split("=", 1)[0]: line.split("=", 1)[1].strip("'")
        for line in env_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("CALLSHIELD_")
    }
    primary = {n for cls in SETTINGS_CLASSES for n in _primary(cls)}
    assert sorted(set(written) - primary) == [], "init wrote keys no settings class reads"
    assert set(written) <= known_env_names()

    cfg = load_config(env_path)  # also proves VoiceSettings validation accepts the output
    by_name = {n: (m, f) for m in cfg.all_models() for n, f in _primary_fields(type(m))}
    for name, text in written.items():
        model, field = by_name[name]
        assert _plain(getattr(model, field)) == text, name
    assert cfg.asterisk.ari_url == "http://127.0.0.1:9088"
    assert cfg.asterisk.app_name == "shieldapp"


def _primary_fields(cls: type[BaseSettings]) -> list[tuple[str, str]]:
    prefix = str(cls.model_config.get("env_prefix", ""))
    return [((prefix + field).upper(), field) for field in cls.model_fields]


def _primary(cls: type[BaseSettings]) -> list[str]:
    return [name for name, _ in _primary_fields(cls)]


def test_legacy_ari_names_are_fallbacks_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLSHIELD_ARI_ENABLED", "true")
    assert AsteriskSettings().enabled is False
    monkeypatch.setenv("CALLSHIELD_ARI_APP", "old")
    assert AsteriskSettings().app_name == "old"
    monkeypatch.setenv("CALLSHIELD_ASTERISK_APP_NAME", "new")
    assert AsteriskSettings().app_name == "new"
