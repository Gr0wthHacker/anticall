"""Config registry, generated reference doc and env.example stay in sync with the settings."""

from __future__ import annotations

from pathlib import Path

from callshield.configreg.docgen import build_reference, stale_files
from callshield.configreg.gates import RISKY_FEATURES
from callshield.configreg.meta import META
from callshield.configreg.registry import all_rows, row_by_env
from callshield.doctor.loader import load_config
from callshield.providers.render_env import known_env_names, render_env, render_env_example


def test_every_setting_has_a_description() -> None:
    missing = [r.env for r in all_rows() if not r.description]
    assert missing == []
    assert set(META) <= {r.env for r in all_rows()}, "META has entries for unknown variables"


def test_config_reference_and_env_example_are_not_stale() -> None:
    stale = stale_files()
    assert stale == [], f"run: python scripts/gen_config_reference.py --write ({stale})"


def test_reference_covers_every_variable() -> None:
    text = build_reference()
    assert all(f"`{r.env}`" in text for r in all_rows())


def test_env_example_has_every_variable_and_nothing_unknown() -> None:
    example = render_env_example()
    names = {
        line.lstrip("# ").split("=", 1)[0]
        for line in example.splitlines()
        if "=" in line and line.lstrip("# ").split("=", 1)[0].isupper()
    }
    assert {r.env for r in all_rows()} <= names
    assert names <= known_env_names()


def test_every_risky_switch_is_a_real_variable_and_defaults_off() -> None:
    rows = row_by_env()
    for feature in RISKY_FEATURES:
        assert rows[feature.env_var].default == "false", feature.key


def test_rendered_env_loads_through_settings(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(render_env({"CALLSHIELD_JWT_SECRET": "x" * 40}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.settings.outbound_enabled is False
    assert cfg.callback.callback_enabled is False
    assert cfg.settings.jwt_secret.get_secret_value() == "x" * 40
    assert cfg.settings.cors_allow_origins == []


def test_env_example_itself_loads(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(render_env_example(), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.settings.cors_allow_origins == ["http://127.0.0.1:3000"]
