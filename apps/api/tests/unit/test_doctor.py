"""Doctor checks with fake probes."""

from __future__ import annotations

from pathlib import Path

from callshield.doctor.engine import run_checks, run_doctor_from_env
from callshield.doctor.loader import load_config
from callshield.doctor.model import AriProbe, DbStatus
from callshield.providers.render_env import render_env
from tests.doctor_support import fake_deps

GOOD = {
    "CALLSHIELD_JWT_SECRET": "j" * 40,
    "CALLSHIELD_WEBHOOK_SECRET": "w" * 40,
    "CALLSHIELD_CALLBACK_HMAC_SECRET": "h" * 40,
    "CALLSHIELD_ASTERISK_ARI_PASSWORD": "a" * 40,
    "CALLSHIELD_INSTANCE_JURISDICTION": "US-CA",
    "CALLSHIELD_INSTANCE_BACKUP_NOTE": "nightly copy to my NAS",
}


def _env(tmp_path: Path, extra: dict[str, str] | None = None) -> str:
    path = tmp_path / ".env"
    path.write_text(render_env({**GOOD, **(extra or {})}), encoding="utf-8")
    path.chmod(0o600)
    return str(path)


def _statuses(env_file: str, **deps_kwargs: object) -> dict[str, str]:
    report = run_doctor_from_env(env_file, fake_deps(**deps_kwargs))  # type: ignore[arg-type]
    return {r.id: r.status for r in report.results}


def test_healthy_default_install_has_no_failures(tmp_path: Path) -> None:
    report = run_doctor_from_env(_env(tmp_path), fake_deps())
    assert report.ok
    statuses = {r.id: r.status for r in report.results}
    assert statuses["ari"] == "SKIP"
    assert statuses["risky"] == "PASS"
    assert statuses["secrets"] == "PASS"


def test_unreachable_database_fails_with_fix(tmp_path: Path) -> None:
    down = DbStatus(False, None, "OperationalError")
    report = run_doctor_from_env(_env(tmp_path), fake_deps(db=down))
    failed = [r for r in report.results if r.status == "FAIL"]
    assert [r.id for r in failed] == ["database"]
    assert failed[0].fix
    assert not report.ok


def test_migrations_behind_head_fail(tmp_path: Path) -> None:
    behind = DbStatus(True, False, "revision 0030")
    assert _statuses(_env(tmp_path), db=behind)["migrations"] == "FAIL"


def test_default_secret_is_warn_in_dev_and_fail_in_prod(tmp_path: Path) -> None:
    weak = {"CALLSHIELD_JWT_SECRET": "CHANGE_ME"}
    assert _statuses(_env(tmp_path, weak))["secrets"] == "WARN"
    prod = {**weak, "CALLSHIELD_ENVIRONMENT": "prod"}
    assert _statuses(_env(tmp_path, prod))["secrets"] == "FAIL"


def test_risky_flags_are_listed(tmp_path: Path) -> None:
    env = _env(tmp_path, {"CALLSHIELD_CALLBACK_CALLBACK_ENABLED": "true"})
    report = run_doctor_from_env(env, fake_deps())
    risky = next(r for r in report.results if r.id == "risky")
    assert risky.status == "WARN"
    assert "CALLSHIELD_CALLBACK_CALLBACK_ENABLED" in risky.detail


def test_ari_enabled_probes_and_reports_trunk(tmp_path: Path) -> None:
    env = _env(tmp_path, {"CALLSHIELD_ASTERISK_ENABLED": "true"})
    ok = _statuses(env, ari=AriProbe(True, "online", "ok"))
    assert ok["ari"] == "PASS" and ok["trunk"] == "PASS"
    down = _statuses(env, ari=AriProbe(False, None, "ConnectError"))
    assert down["ari"] == "FAIL" and down["trunk"] == "SKIP"
    offline = _statuses(env, ari=AriProbe(True, "offline", "ok"))
    assert offline["trunk"] == "WARN"


def test_non_local_ari_url_warns_about_ports(tmp_path: Path) -> None:
    env = _env(tmp_path, {"CALLSHIELD_ASTERISK_ARI_URL": "http://203.0.113.5:8088"})
    assert _statuses(env)["ports"] == "WARN"


def test_voice_models_and_packages(tmp_path: Path) -> None:
    models = tmp_path / "models"
    extra = {"CALLSHIELD_VOICE_STT_ENGINE": "vosk", "CALLSHIELD_VOICE_STT_MODEL_DIR": str(models)}
    env = _env(tmp_path, extra)
    assert _statuses(env)["models"] == "FAIL"
    (models / "vosk-model-small-en-us-0.15").mkdir(parents=True)
    (models / "vosk-model-small-en-us-0.15" / "model.bin").write_text("x")
    assert _statuses(env)["models"] == "PASS"
    assert _statuses(env, missing_modules=("vosk",))["voice_extras"] == "FAIL"


def test_unknown_jurisdiction_and_missing_backup_warn(tmp_path: Path) -> None:
    env = _env(
        tmp_path,
        {"CALLSHIELD_INSTANCE_JURISDICTION": "UNKNOWN", "CALLSHIELD_INSTANCE_BACKUP_NOTE": ""},
    )
    statuses = _statuses(env)
    assert statuses["jurisdiction"] == "WARN"
    assert statuses["backup"] == "WARN"


def test_bad_timezone_warns(tmp_path: Path) -> None:
    env = _env(tmp_path, {"CALLSHIELD_INSTANCE_TIMEZONE": "Mars/Base"})
    assert _statuses(env)["timezone"] == "WARN"


def test_invalid_config_is_one_fail_without_leaking_values(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("CALLSHIELD_CALLBACK_MAX_CALL_SECONDS=99999\n", encoding="utf-8")
    report = run_doctor_from_env(str(path), fake_deps())
    assert [r.id for r in report.results] == ["config"]
    assert "99999" not in report.results[0].detail
    assert not report.ok


def test_crashing_check_is_reported_not_raised(tmp_path: Path) -> None:
    cfg = load_config(Path(_env(tmp_path)))
    deps = fake_deps()

    def boom(_url: str) -> DbStatus:
        raise RuntimeError("boom")

    deps.check_db = boom
    report = run_checks(cfg, deps)
    assert any(r.status == "FAIL" and "crashed" in r.detail for r in report.results)


def test_open_bootstrap_warns(tmp_path: Path) -> None:
    assert _statuses(_env(tmp_path, {"CALLSHIELD_ALLOW_BOOTSTRAP": "true"}))["bootstrap"] == "WARN"
