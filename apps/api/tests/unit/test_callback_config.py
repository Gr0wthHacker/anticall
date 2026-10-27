"""Callback config: spec-table defaults, ranges, locked keys, who-can-change, ceilings."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from callshield.callback import disclosure
from callshield.callback.config import (
    CEIL_PER_INBOUND,
    CEIL_PER_NUMBER_30D,
    CEIL_PER_USER_DAY,
    CHANGEABLE_BY,
    INSTANCE_MAX_PER_NUMBER_30D,
    CallbackSettings,
    EffectiveSettings,
    SettingsRejectedError,
    Who,
    merge_effective,
    validate_patch,
)

SPEC_DEFAULTS = {
    "callback_enabled": False,
    "callback_window_hours": 72,
    "listen_window_seconds": 10,
    "bare_greeting_disclose_ms": 1500,
    "max_attempts_per_inbound": 2,
    "max_attempts_per_number_30d": 2,
    "max_attempts_per_user_day": 5,
    "max_question_turns": 4,
    "max_call_seconds": 120,
    "outbound_recording": False,
    "remote_ai_processing": False,
    "allowed_attestation": "A",
}
# key -> (low, high) inclusive allowed range
SPEC_RANGES = {
    "callback_window_hours": (1, 168),
    "listen_window_seconds": (0, 10),
    "bare_greeting_disclose_ms": (500, 3000),
    "max_attempts_per_inbound": (1, 2),
    "max_attempts_per_number_30d": (1, 2),
    "max_attempts_per_user_day": (1, 5),
    "max_question_turns": (1, 4),
    "max_call_seconds": (30, 120),
}


def test_defaults_match_spec_table() -> None:
    settings = CallbackSettings()
    for key, expected in SPEC_DEFAULTS.items():
        assert getattr(settings, key) == expected, key
    assert EffectiveSettings().model_dump(exclude={"disclosure_template_id"}) == SPEC_DEFAULTS


def make_settings(**kwargs: Any) -> CallbackSettings:
    return CallbackSettings(**kwargs)


@pytest.mark.parametrize("key", sorted(SPEC_RANGES))
def test_ranges_enforced_at_both_ends(key: str) -> None:
    low, high = SPEC_RANGES[key]
    assert getattr(make_settings(**{key: low}), key) == low
    assert getattr(make_settings(**{key: high}), key) == high
    for bad in (low - 1, high + 1):
        with pytest.raises(ValidationError):
            make_settings(**{key: bad})
        with pytest.raises(ValidationError):
            EffectiveSettings(**{key: bad})


def test_ceilings_cannot_be_exceeded_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLSHIELD_CALLBACK_MAX_ATTEMPTS_PER_INBOUND", "3")
    with pytest.raises(ValidationError):
        CallbackSettings()
    monkeypatch.setenv("CALLSHIELD_CALLBACK_MAX_ATTEMPTS_PER_INBOUND", "1")
    assert CallbackSettings().max_attempts_per_inbound == 1
    assert (CEIL_PER_INBOUND, CEIL_PER_NUMBER_30D, CEIL_PER_USER_DAY) == (2, 2, 5)
    assert INSTANCE_MAX_PER_NUMBER_30D == 3
    assert "instance_max_per_number_30d" not in CallbackSettings.model_fields  # constant


@pytest.mark.parametrize(
    ("env_name", "bad_value"),
    [
        ("CALLSHIELD_CALLBACK_REMOTE_AI_PROCESSING", "true"),
        ("CALLSHIELD_CALLBACK_ALLOWED_ATTESTATION", "B"),
        ("CALLSHIELD_CALLBACK_ALLOWED_ATTESTATION", "A,B"),
    ],
)
def test_locked_keys_reject_any_other_value_even_via_env(
    monkeypatch: pytest.MonkeyPatch, env_name: str, bad_value: str
) -> None:
    monkeypatch.setenv(env_name, bad_value)
    with pytest.raises(ValidationError):
        CallbackSettings()


def test_locked_keys_accept_only_their_fixed_value_in_effective_settings() -> None:
    with pytest.raises(ValidationError):
        EffectiveSettings(remote_ai_processing=True)
    with pytest.raises(ValidationError):
        EffectiveSettings(allowed_attestation="B")


def test_env_prefix_is_callshield_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLSHIELD_CALLBACK_CALLBACK_ENABLED", "true")
    monkeypatch.setenv("CALLSHIELD_CALLBACK_CALLBACK_WINDOW_HOURS", "24")
    settings = CallbackSettings()
    assert settings.callback_enabled is True and settings.callback_window_hours == 24


def test_who_can_change_matches_spec() -> None:
    expected = {
        "callback_enabled": Who.USER,
        "callback_window_hours": Who.USER,
        "listen_window_seconds": Who.USER,
        "bare_greeting_disclose_ms": Who.OPERATOR,
        "max_attempts_per_inbound": Who.USER,
        "max_attempts_per_number_30d": Who.USER,
        "max_attempts_per_user_day": Who.USER,
        "max_question_turns": Who.OPERATOR,
        "max_call_seconds": Who.OPERATOR,
        "outbound_recording": Who.PER_CALL,
        "remote_ai_processing": Who.LOCKED,
        "allowed_attestation": Who.LOCKED,
    }
    assert expected == CHANGEABLE_BY


@pytest.mark.parametrize(
    ("patch", "is_operator", "code"),
    [
        ({"callback_window_hours": 24}, False, None),
        ({"callback_window_hours": 169}, False, "OUT_OF_RANGE"),
        ({"callback_window_hours": 24}, True, "USER_ONLY"),
        ({"max_call_seconds": 60}, True, None),
        ({"max_call_seconds": 60}, False, "OPERATOR_ONLY"),
        ({"max_call_seconds": 121}, True, "OUT_OF_RANGE"),
        ({"remote_ai_processing": True}, False, "KEY_LOCKED"),
        ({"allowed_attestation": "B"}, True, "KEY_LOCKED"),
        ({"outbound_recording": True}, False, "USE_PER_CALL"),
        ({"max_attempts_per_inbound": 3}, False, "OUT_OF_RANGE"),
        ({"caller_id": "+14155550100"}, False, "UNKNOWN_KEY"),
        ({"callback_enabled": "yes"}, False, "INVALID_VALUE"),
    ],
)
def test_validate_patch_role_and_range(
    patch: dict[str, object], is_operator: bool, code: str | None
) -> None:
    current = EffectiveSettings()
    if code is None:
        assert validate_patch(patch, is_operator=is_operator, current=current) == patch
        return
    with pytest.raises(SettingsRejectedError) as exc:
        validate_patch(patch, is_operator=is_operator, current=current)
    assert exc.value.code == code


def test_merge_ignores_stored_values_above_ceiling() -> None:
    base = CallbackSettings()
    merged = merge_effective(base, {"max_attempts_per_inbound": 9}, {"callback_enabled": True})
    assert merged.max_attempts_per_inbound == 2 and merged.callback_enabled is True


def test_recording_requires_a_disclosure_that_contains_the_notice() -> None:
    assert disclosure.template_has_recording_notice("v1")
    assert not disclosure.template_has_recording_notice("v1-no-recording-notice")
    with pytest.raises(ValidationError):
        CallbackSettings(outbound_recording=True, disclosure_template_id="v1-no-recording-notice")
    with pytest.raises(ValidationError):
        EffectiveSettings(outbound_recording=True, disclosure_template_id="v1-no-recording-notice")
    with pytest.raises(ValueError, match="recording"):
        disclosure.render_disclosure(
            "v1-no-recording-notice",
            first_name="Jordan",
            call_time=__import__("datetime").datetime(2026, 1, 1, 9, 5),
            outbound_recording=True,
        )


def test_there_is_no_caller_id_setting() -> None:
    names = set(CallbackSettings.model_fields) | set(EffectiveSettings.model_fields)
    forbidden = ("caller", "from_number", "cnam", "display_number", "outbound_number", "cli")
    assert not [n for n in names if any(f in n for f in forbidden)]
