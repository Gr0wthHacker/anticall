"""`callshield init`: wizard, non-interactive mode, safety refusals."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from callshield.acknowledgment.service import item_ids
from callshield.cli.context import CliContext
from callshield.cli.main import main
from callshield.configreg.gates import CALLBACK_CONFIRMATION_SENTENCE, RISKY_FEATURES
from callshield.doctor.loader import load_config

SIP_PASSWORD = "sip-secret-for-tests-only"
ADMIN_PASSWORD = "admin-password-for-tests"


class Script:
    """Scripted prompts: first rule whose key is in the prompt wins; default is Enter."""

    def __init__(self, rules: dict[str, str] | None = None, accept_all: bool = False) -> None:
        self.rules = rules or {}
        self.accept_all = accept_all
        self.seen: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.seen.append(prompt)
        for key, reply in self.rules.items():
            if key in prompt:
                return reply
        if self.accept_all and "I accept" in prompt:
            return "y"
        return ""


def _ctx(script: Script) -> CliContext:
    counter = iter(range(1, 1000))
    return CliContext(
        input_fn=script,
        secret_fn=lambda prompt: SIP_PASSWORD if "password" in prompt.lower() else "",
        new_secret=lambda: f"generated-secret-{next(counter):04d}-" + "z" * 24,
    )


def _wizard(tmp_path: Path, script: Script, *extra: str) -> int:
    return main(["init", "--output-dir", str(tmp_path), *extra], _ctx(script))


def test_default_wizard_turns_everything_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = Script({"Provider profile": "", "Jurisdiction": "US-CA"})
    assert _wizard(tmp_path, script) == 0
    cfg = load_config(tmp_path / ".env")
    assert cfg.settings.outbound_enabled is False
    assert cfg.callback.callback_enabled is False
    assert cfg.asterisk.enabled is False
    assert cfg.instance.jurisdiction == "US-CA"
    report = (tmp_path / "SETUP_REPORT.md").read_text(encoding="utf-8")
    assert "| ON |" not in report
    assert all(f.env_var in report for f in RISKY_FEATURES)
    assert (tmp_path / "pjsip.callshield.conf").exists()
    assert "Gated features ON: none" in capsys.readouterr().out


def test_secrets_are_generated_distinct_and_never_echoed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _wizard(tmp_path, Script()) == 0
    cfg = load_config(tmp_path / ".env")
    secrets_ = {
        cfg.settings.jwt_secret.get_secret_value(),
        cfg.settings.webhook_secret.get_secret_value(),
        cfg.callback.hmac_secret.get_secret_value(),
        cfg.asterisk.ari_password.get_secret_value(),
    }
    assert len(secrets_) == 4
    shown = capsys.readouterr()
    report = (tmp_path / "SETUP_REPORT.md").read_text(encoding="utf-8")
    for secret in secrets_ | {SIP_PASSWORD}:
        assert secret not in shown.out + shown.err + report
    assert SIP_PASSWORD in (tmp_path / "pjsip.callshield.conf").read_text(encoding="utf-8")
    ari_conf = (tmp_path / "ari.callshield.conf").read_text(encoding="utf-8")
    assert cfg.asterisk.ari_password.get_secret_value() in ari_conf


def test_callback_needs_exact_sentence(tmp_path: Path) -> None:
    rules = {"Enable Automated callback": "y", "> ": "yes I agree"}
    assert _wizard(tmp_path, Script(rules, accept_all=True)) == 0
    assert load_config(tmp_path / ".env").callback.callback_enabled is False


def test_callback_enabled_with_sentence_and_acknowledgment(tmp_path: Path) -> None:
    rules = {"Enable Automated callback": "y", "> ": CALLBACK_CONFIRMATION_SENTENCE}
    assert _wizard(tmp_path, Script(rules, accept_all=True)) == 0
    assert load_config(tmp_path / ".env").callback.callback_enabled is True
    assert "| ON |" in (tmp_path / "SETUP_REPORT.md").read_text(encoding="utf-8")


def test_declining_an_acknowledgment_item_keeps_features_off(tmp_path: Path) -> None:
    rules = {"Enable Outbound calling": "y"}
    assert _wizard(tmp_path, Script(rules, accept_all=False)) == 0
    assert load_config(tmp_path / ".env").settings.outbound_enabled is False


def test_refuses_to_overwrite_existing_env(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("CALLSHIELD_JWT_SECRET=keepme\n", encoding="utf-8")
    assert _wizard(tmp_path, Script()) == 2
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "CALLSHIELD_JWT_SECRET=keepme\n"
    assert _wizard(tmp_path, Script(), "--force") == 0


def _answers(tmp_path: Path, **overrides: object) -> Path:
    data: dict[str, object] = {
        "db_url": f"sqlite:///{(tmp_path / 'cs.db').as_posix()}",
        "jurisdiction": "US-TX",
        "timezone": "UTC",
        "provider_profile": "telnyx",
        "provider_inputs": {
            "trunk_name": "tnx",
            "sip_host": "sip.example.test",
            "auth_mode": "ip_auth",
            "ip_auth_match": "192.0.2.0/24",
        },
    }
    data.update(overrides)
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _non_interactive(tmp_path: Path, answers: Path) -> int:
    argv = ["init", "--non-interactive", "--answers", str(answers), "--output-dir", str(tmp_path)]
    return main(argv, _ctx(Script()))


def test_non_interactive_writes_files_with_chosen_profile(tmp_path: Path) -> None:
    assert _non_interactive(tmp_path, _answers(tmp_path)) == 0
    conf = (tmp_path / "pjsip.callshield.conf").read_text(encoding="utf-8")
    assert "match=192.0.2.0/24" in conf and "profile=telnyx" in conf
    assert load_config(tmp_path / ".env").asterisk.trunk_endpoint == "tnx"


def test_non_interactive_refuses_callback_without_confirmation(tmp_path: Path) -> None:
    answers = _answers(tmp_path, features={"callback": True}, acknowledgment_items=list(item_ids()))
    assert _non_interactive(tmp_path, answers) == 2
    assert not (tmp_path / ".env").exists()


def test_non_interactive_refuses_feature_without_acknowledgment(tmp_path: Path) -> None:
    answers = _answers(tmp_path, features={"outbound": True})
    assert _non_interactive(tmp_path, answers) == 2
    assert not (tmp_path / ".env").exists()


def test_non_interactive_accepts_callback_with_both_confirmations(tmp_path: Path) -> None:
    answers = _answers(
        tmp_path,
        features={"callback": True},
        acknowledgment_items=list(item_ids()),
        callback_confirmation=CALLBACK_CONFIRMATION_SENTENCE,
    )
    assert _non_interactive(tmp_path, answers) == 0
    assert load_config(tmp_path / ".env").callback.callback_enabled is True


def test_non_interactive_rejects_unknown_keys_and_profiles(tmp_path: Path) -> None:
    assert _non_interactive(tmp_path, _answers(tmp_path, bogus="1")) == 2
    assert _non_interactive(tmp_path, _answers(tmp_path, provider_profile="nope")) == 2


def test_non_interactive_without_answers_file_fails(tmp_path: Path) -> None:
    assert main(["init", "--non-interactive", "--output-dir", str(tmp_path)], _ctx(Script())) == 2


def test_init_can_create_the_admin(tmp_path: Path) -> None:
    answers = _answers(tmp_path, admin_email="Owner@Example.test", admin_password=ADMIN_PASSWORD)
    assert _non_interactive(tmp_path, answers) == 0
    with sqlite3.connect(tmp_path / "cs.db") as conn:
        rows = conn.execute("SELECT email, role FROM users").fetchall()
    assert rows == [("owner@example.test", "HOUSEHOLD_ADMIN")]
    assert ADMIN_PASSWORD not in (tmp_path / ".env").read_text(encoding="utf-8")


def test_short_admin_password_is_rejected(tmp_path: Path) -> None:
    answers = _answers(tmp_path, admin_email="a@example.test", admin_password="short")
    assert _non_interactive(tmp_path, answers) == 2


def test_bad_pjsip_input_is_reported_not_traced(tmp_path: Path) -> None:
    inputs = {"sip_host": "sip.example.test", "trunk_name": "bad name"}
    assert _non_interactive(tmp_path, _answers(tmp_path, provider_inputs=inputs)) == 2
