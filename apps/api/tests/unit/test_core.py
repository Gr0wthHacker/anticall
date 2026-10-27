from __future__ import annotations

import pytest

from callshield.core.config import Settings
from callshield.core.hashing import canonical_json, sha256_hex, sha256_json
from callshield.core.idempotency import make_idempotency_key, provider_event_key
from callshield.core.phone import (
    InvalidPhoneNumberError,
    is_reserved_test_number,
    mask_phone,
    normalize_e164,
)
from callshield.core.redaction import redact_structure, redact_text


def test_risky_capabilities_default_off() -> None:
    s = Settings()
    assert s.enabled_capabilities() == {
        "outbound": False,
        "live_stt": False,
        "ai_outbound": False,
        "community": False,
        "filing_submission": False,
    }
    assert s.allow_bootstrap is False


def test_conservative_limits() -> None:
    s = Settings()
    assert s.outbound_cooldown_seconds == 900
    assert s.max_attempts_per_case_per_day == 3
    assert s.max_call_seconds <= 900


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLSHIELD_OUTBOUND_ENABLED", "true")
    assert Settings().outbound_enabled is True


def test_mask_phone() -> None:
    assert mask_phone("+14155550123") == "+1415***0123"
    assert mask_phone(None) == "***"
    assert "5550123" not in mask_phone("+14155550123")


def test_normalize_e164() -> None:
    assert normalize_e164("(415) 555-0123") == "+14155550123"
    assert normalize_e164("1-415-555-0123") == "+14155550123"
    assert normalize_e164("+44 20 7946 0958") == "+442079460958"
    with pytest.raises(InvalidPhoneNumberError):
        normalize_e164("12345")


def test_reserved_test_numbers() -> None:
    assert is_reserved_test_number("+14155550123")
    assert not is_reserved_test_number("+14155551234")
    assert not is_reserved_test_number("+14155550200")


def test_canonical_json_and_hash_are_stable() -> None:
    assert canonical_json({"b": 1, "a": [2, 1]}) == '{"a":[2,1],"b":1}'
    assert sha256_json({"b": 1, "a": 2}) == sha256_json({"a": 2, "b": 1})
    assert sha256_hex("abc") == sha256_hex(b"abc")
    assert len(sha256_hex("x")) == 64


def test_idempotency_keys() -> None:
    key = provider_event_key("twilio", "CA1", "call.received")
    assert key == "provider:twilio:CA1:call.received"
    long_key = make_idempotency_key("x" * 500)
    assert len(long_key) < 200
    assert long_key == make_idempotency_key("x" * 500)


def test_redaction_masks_numbers_and_secrets() -> None:
    assert "5550123" not in redact_text("call me at +1 415 555 0123 now")
    out = redact_structure({"password": "x", "from": "+14155550123", "n": [{"token": "t"}]})
    assert out["password"] == "[REDACTED]"
    assert out["from"] == "+1415***0123"
    assert out["n"][0]["token"] == "[REDACTED]"
