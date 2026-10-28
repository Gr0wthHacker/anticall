"""doctor / config / voice / db / user / run subcommands via main(argv)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path

import pytest

from callshield.cli.context import CliContext
from callshield.cli.main import main
from callshield.cli.voice_cmd import download_model
from callshield.configreg.voice_catalog import VoiceModel
from callshield.doctor.deps import check_db
from callshield.doctor.model import DbStatus
from callshield.providers.render_env import render_env
from tests.doctor_support import fake_deps

SECRET = "super-secret-value-" + "q" * 30
GOOD = {
    "CALLSHIELD_JWT_SECRET": SECRET,
    "CALLSHIELD_WEBHOOK_SECRET": "w" * 40,
    "CALLSHIELD_CALLBACK_HMAC_SECRET": "h" * 40,
    "CALLSHIELD_ASTERISK_ARI_PASSWORD": "a" * 40,
    "CALLSHIELD_INSTANCE_JURISDICTION": "US-CA",
    "CALLSHIELD_INSTANCE_BACKUP_NOTE": "nas",
}


def _env(tmp_path: Path, extra: dict[str, str] | None = None) -> str:
    db = f"sqlite:///{(tmp_path / 'cli.db').as_posix()}"
    path = tmp_path / ".env"
    path.write_text(
        render_env({**GOOD, "CALLSHIELD_DB_URL": db, **(extra or {})}), encoding="utf-8"
    )
    path.chmod(0o600)
    return str(path)


def test_no_command_is_a_usage_error() -> None:
    assert main([]) == 2


def test_doctor_text_and_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = _env(tmp_path)
    assert main(["--env-file", env, "doctor"], CliContext(doctor_deps=fake_deps())) == 0
    assert "PASS" in capsys.readouterr().out
    down = fake_deps(db=DbStatus(False, None, "OperationalError"))
    assert main(["--env-file", env, "doctor"], CliContext(doctor_deps=down)) == 1
    text = capsys.readouterr().out
    assert "FAIL" in text and "fix:" in text


def test_doctor_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = CliContext(doctor_deps=fake_deps())
    assert main(["--env-file", _env(tmp_path), "doctor", "--json"], ctx) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["counts"]["FAIL"] == 0
    assert {c["status"] for c in data["checks"]} <= {"PASS", "WARN", "FAIL", "SKIP"}
    assert SECRET not in json.dumps(data)


def test_config_show_redacts_secrets(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--env-file", _env(tmp_path), "config", "show"]) == 0
    out = capsys.readouterr().out
    assert SECRET not in out and "***set***" in out
    assert "CALLSHIELD_OUTBOUND_ENABLED=False" in out
    assert main(["--env-file", _env(tmp_path), "config", "show", "--json"]) == 0
    assert SECRET not in capsys.readouterr().out


def test_config_validate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--env-file", _env(tmp_path), "config", "validate"]) == 0
    bad = _env(tmp_path, {"CALLSHIELD_LLM_ENABLED": "true"})
    assert main(["--env-file", bad, "config", "validate"]) == 1
    assert "CALLSHIELD_LLM_BASE_URL" in capsys.readouterr().err
    out_of_range = _env(tmp_path, {"CALLSHIELD_CALLBACK_MAX_ATTEMPTS_PER_USER_DAY": "50"})
    assert main(["--env-file", out_of_range, "config", "show"]) == 1


def test_voice_models_prints_instructions(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["voice", "models"]) == 0
    out = capsys.readouterr().out
    assert "vosk" in out and "piper" in out and "UNVERIFIED" in out


def test_voice_download_refused_without_pinned_checksum(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["voice", "models", "--download", "vosk"], CliContext(fetch=_boom)) == 2
    assert "sha256" in capsys.readouterr().err
    assert main(["voice", "models", "--download", "nope"]) == 2


def _boom(_url: str) -> bytes:
    raise AssertionError("must not download")


def _entry(payload: bytes) -> VoiceModel:
    return VoiceModel(
        "vosk",
        "stt",
        "m",
        "voice",
        "https://example.test/m.zip",
        "https://example.test",
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def test_download_verifies_checksum_and_needs_confirmation(tmp_path: Path) -> None:
    data = b"model-bytes"
    yes = CliContext(input_fn=lambda _p: "download")
    assert download_model(_entry(data), tmp_path / "ok", yes, fetch=lambda _u: data) == 0
    assert (tmp_path / "ok" / "m.zip").read_bytes() == data
    assert download_model(_entry(data), tmp_path / "bad", yes, fetch=lambda _u: b"tampered") == 1
    assert not (tmp_path / "bad" / "m.zip").exists()
    no = CliContext(input_fn=lambda _p: "no")
    assert download_model(_entry(data), tmp_path / "no", no, fetch=_boom) == 1


def test_db_upgrade_then_real_check_reports_head(tmp_path: Path) -> None:
    env = _env(tmp_path)
    url = f"sqlite:///{(tmp_path / 'cli.db').as_posix()}"
    assert check_db(url).at_head is False  # file not created yet
    assert not (tmp_path / "cli.db").exists()
    assert main(["--env-file", env, "db", "upgrade"]) == 0
    status = check_db(url)
    assert status.reachable and status.at_head is True


def test_create_admin(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = _env(tmp_path)
    assert main(["--env-file", env, "db", "upgrade"]) == 0
    password = "a-long-enough-password"
    ctx = CliContext(secret_fn=lambda _p: password)
    argv = ["--env-file", env, "user", "create-admin", "--email", "Me@Example.test"]
    assert main(argv, ctx) == 0
    assert main(argv, ctx) == 1  # duplicate
    with sqlite3.connect(tmp_path / "cli.db") as conn:
        assert conn.execute("SELECT email FROM users").fetchall() == [("me@example.test",)]
    shown = capsys.readouterr()
    assert password not in shown.out + shown.err


def test_create_admin_rejects_mismatch_and_short_password(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert main(["--env-file", env, "db", "upgrade"]) == 0
    answers = iter(["a-long-enough-password", "different-password-here"])
    ctx = CliContext(secret_fn=lambda _p: next(answers))
    assert main(["--env-file", env, "user", "create-admin", "--email", "a@example.test"], ctx) == 1
    short = CliContext(secret_fn=lambda _p: "short")
    assert (
        main(["--env-file", env, "user", "create-admin", "--email", "a@example.test"], short) == 1
    )


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, argv: list[str], env: Mapping[str, str]) -> int:
        self.calls.append((argv, dict(env)))
        return 0


def test_run_api_and_worker_use_fake_runner(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeRunner()
    ctx = CliContext(runner=runner)
    env = _env(tmp_path)
    assert main(["--env-file", env, "run", "api", "--port", "8123"], ctx) == 0
    argv, child_env = runner.calls[0]
    assert "uvicorn" in argv and "--factory" in argv and "127.0.0.1" in argv and "8123" in argv
    assert child_env["CALLSHIELD_INSTANCE_JURISDICTION"] == "US-CA"
    assert "Starting api" in capsys.readouterr().out
    assert main(["--env-file", env, "run", "worker"], ctx) == 0
    assert runner.calls[1][0][-1] == "callshield.worker"


def test_run_refuses_public_bind_without_flag() -> None:
    runner = FakeRunner()
    assert main(["run", "api", "--host", "0.0.0.0"], CliContext(runner=runner)) == 2
    assert runner.calls == []
    assert (
        main(["run", "api", "--host", "0.0.0.0", "--allow-public"], CliContext(runner=runner)) == 0
    )


def test_run_telephony_unavailable_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("callshield.cli.run_cmd._module_exists", lambda _name: False)
    runner = FakeRunner()
    assert main(["run", "telephony"], CliContext(runner=runner)) == 2
    assert runner.calls == []
    monkeypatch.setattr("callshield.cli.run_cmd._module_exists", lambda _name: True)
    assert main(["run", "telephony"], CliContext(runner=runner)) == 0
