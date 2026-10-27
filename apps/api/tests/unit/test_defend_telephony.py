"""Telephony: HMAC signing, adapters, normalized events, mock provider."""

from __future__ import annotations

import hmac
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from callshield.core.clock import FrozenClock, set_clock
from callshield.telephony import signing
from callshield.telephony.adapters import AriAdapter, GenericWebhookAdapter
from callshield.telephony.base import (
    MalformedEventError,
    ProviderAdapter,
    ProviderEventType,
    WebhookVerificationError,
)
from callshield.telephony.mock import (
    FICTIONAL_CALLER,
    FICTIONAL_LINE,
    MockProvider,
    ari_stasis_start,
    inbound_call_payload,
    signed_request,
)
from callshield.telephony.registry import (
    adapter_names,
    get_adapter,
    register_adapter,
    reset_adapters,
)

SECRET = "unit-test-secret"
BODY = b'{"hello": "world"}'


@pytest.fixture(autouse=True)
def _clock() -> FrozenClock:
    clock = FrozenClock(datetime(2026, 3, 1, 12, 0, tzinfo=UTC))
    set_clock(clock)
    return clock


def _now() -> int:
    return int(datetime(2026, 3, 1, 12, 0, tzinfo=UTC).timestamp())


def _headers(
    secret: str = SECRET, ts: int | None = None, nonce: str = "nonce-12345678"
) -> dict[str, str]:
    return signing.sign_headers(secret, ts if ts is not None else _now(), nonce, BODY)


def test_valid_signature_returns_nonce() -> None:
    assert signing.verify_signed_request(_headers(), BODY, SECRET, 300) == "nonce-12345678"


def test_header_names_are_case_insensitive() -> None:
    lowered = {k.lower(): v for k, v in _headers().items()}
    assert signing.verify_signed_request(lowered, BODY, SECRET, 300)


@pytest.mark.parametrize(
    "missing", ["X-CallShield-Timestamp", "X-CallShield-Nonce", "X-CallShield-Signature"]
)
def test_missing_header_rejected(missing: str) -> None:
    headers = _headers()
    headers.pop(missing)
    with pytest.raises(WebhookVerificationError) as exc:
        signing.verify_signed_request(headers, BODY, SECRET, 300)
    assert exc.value.code == "MISSING_HEADERS"


def test_wrong_secret_rejected() -> None:
    with pytest.raises(WebhookVerificationError) as exc:
        signing.verify_signed_request(_headers("other-secret"), BODY, SECRET, 300)
    assert exc.value.code == "BAD_SIGNATURE"


def test_tampered_body_rejected() -> None:
    with pytest.raises(WebhookVerificationError):
        signing.verify_signed_request(_headers(), BODY + b" ", SECRET, 300)


def test_signature_binds_timestamp_and_nonce() -> None:
    headers = _headers()
    headers["X-CallShield-Nonce"] = "different-nonce-1"
    with pytest.raises(WebhookVerificationError):
        signing.verify_signed_request(headers, BODY, SECRET, 300)
    headers = _headers()
    headers["X-CallShield-Timestamp"] = str(_now() + 1)
    with pytest.raises(WebhookVerificationError):
        signing.verify_signed_request(headers, BODY, SECRET, 300)


@pytest.mark.parametrize("offset", [-301, 301, -86400])
def test_timestamp_outside_tolerance_rejected(offset: int) -> None:
    headers = _headers(ts=_now() + offset)
    with pytest.raises(WebhookVerificationError) as exc:
        signing.verify_signed_request(headers, BODY, SECRET, 300)
    assert exc.value.code == "STALE_TIMESTAMP"


def test_timestamp_inside_tolerance_accepted() -> None:
    assert signing.verify_signed_request(_headers(ts=_now() - 299), BODY, SECRET, 300)


def test_empty_secret_never_verifies() -> None:
    with pytest.raises(WebhookVerificationError) as exc:
        signing.verify_signed_request(_headers(""), BODY, "", 300)
    assert exc.value.code == "NO_SECRET"


@pytest.mark.parametrize(
    "ts,nonce", [("abc", "nonce-12345678"), ("12", "x"), ("1" * 20, "nonce-12345678")]
)
def test_malformed_timestamp_or_nonce_rejected(ts: str, nonce: str) -> None:
    headers = {
        "X-CallShield-Timestamp": ts,
        "X-CallShield-Nonce": nonce,
        "X-CallShield-Signature": "v1=00",
    }
    with pytest.raises(WebhookVerificationError) as exc:
        signing.verify_signed_request(headers, BODY, SECRET, 300)
    assert exc.value.code == "MALFORMED_HEADERS"


def test_signature_compare_is_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = hmac.compare_digest

    def spy(a: Any, b: Any) -> bool:
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    signing.verify_signed_request(_headers(), BODY, SECRET, 300)
    assert calls


def test_adapter_verify_uses_tolerance_argument() -> None:
    adapter = GenericWebhookAdapter()
    stale = _headers(ts=_now() - 100)
    assert adapter.verify_webhook(stale, BODY, SECRET, 300)
    with pytest.raises(WebhookVerificationError):
        adapter.verify_webhook(stale, BODY, SECRET, 50)


# ------------------------------------------------------------------ generic adapter


def test_generic_parse_inbound_call() -> None:
    payload = inbound_call_payload(
        attestation={"attest": "A"}, hop_count=1, diversions=["+14155550150"]
    )
    event = GenericWebhookAdapter().parse_event(json.dumps(payload).encode())
    assert event.event_type == ProviderEventType.CALL_RECEIVED
    assert event.to_number == FICTIONAL_LINE
    assert event.from_number == FICTIONAL_CALLER
    assert event.attestation == {"attest": "A"}
    assert event.diversions == ("+14155550150",)
    assert event.hop_count == 1


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"type": "nope", "event_id": "e"}',
        b'{"type": "call.received", "event_id": ""}',
        json.dumps(inbound_call_payload(to="12")).encode(),
        json.dumps(inbound_call_payload(hop_count=-1)).encode(),
        json.dumps(inbound_call_payload(hop_count=1000)).encode(),
        json.dumps(inbound_call_payload(attestation="x")).encode(),
        json.dumps(inbound_call_payload(timestamp="not a time")).encode(),
        pytest.param(b"x" * 70_000, id="oversize"),
    ],
)
def test_generic_malformed_bodies_rejected(body: bytes) -> None:
    with pytest.raises(MalformedEventError):
        GenericWebhookAdapter().parse_event(body)


def test_generic_strips_control_characters_from_caller_id() -> None:
    payload = inbound_call_payload(caller="+1415\x00555\x010123")
    event = GenericWebhookAdapter().parse_event(json.dumps(payload).encode())
    assert event.from_number is not None and "\x00" not in event.from_number


def test_anonymous_caller_has_no_number() -> None:
    event = GenericWebhookAdapter().parse_event(
        json.dumps(inbound_call_payload(caller=None)).encode()
    )
    assert event.from_number is None


def test_generic_registration_event_parses_without_call() -> None:
    body = json.dumps({"event_id": "r1", "type": "registration.failed", "detail": "503"}).encode()
    event = GenericWebhookAdapter().parse_event(body)
    assert event.event_type == ProviderEventType.REGISTRATION_FAILED
    assert event.call_id is None


# ------------------------------------------------------------------ ARI adapter


def test_ari_stasis_start_normalizes() -> None:
    payload = ari_stasis_start(
        "chan-1", FICTIONAL_LINE, FICTIONAL_CALLER, args=["attest=B", "hops=2"]
    )
    event = AriAdapter().parse_event(json.dumps(payload).encode())
    assert event.event_type == ProviderEventType.CALL_RECEIVED
    assert event.call_id == "chan-1"
    assert event.to_number == FICTIONAL_LINE
    assert event.endpoint_id == "trunk1"
    assert event.attestation == {"attest": "B"}
    assert event.hop_count == 2


@pytest.mark.parametrize(
    "status,expected",
    [
        ("Unreachable", ProviderEventType.REGISTRATION_FAILED),
        ("Rejected", ProviderEventType.REGISTRATION_FAILED),
        ("Reachable", ProviderEventType.REGISTRATION_OK),
    ],
)
def test_ari_peer_status_maps_to_registration(status: str, expected: ProviderEventType) -> None:
    body = {"type": "PeerStatusChange", "peer": {"name": "trunk1", "peer_status": status}}
    event = AriAdapter().parse_event(json.dumps(body).encode())
    assert event.event_type == expected
    assert event.endpoint_id == "trunk1"


def test_ari_answer_and_end_events() -> None:
    up = {"type": "ChannelStateChange", "channel": {"id": "c", "state": "Up", "name": "PJSIP/t-1"}}
    assert (
        AriAdapter().parse_event(json.dumps(up).encode()).event_type
        == ProviderEventType.CALL_ANSWERED
    )
    end = {"type": "StasisEnd", "channel": {"id": "c", "name": "PJSIP/t-1"}}
    assert (
        AriAdapter().parse_event(json.dumps(end).encode()).event_type
        == ProviderEventType.CALL_ENDED
    )


@pytest.mark.parametrize(
    "body",
    [
        {"type": "ChannelDtmfReceived", "channel": {"id": "c"}},
        {"type": "StasisStart", "channel": "not-a-dict"},
        {"type": "ChannelStateChange", "channel": {"id": "c", "state": "Ringing"}},
    ],
)
def test_ari_unsupported_or_malformed_rejected(body: dict[str, Any]) -> None:
    with pytest.raises(MalformedEventError):
        AriAdapter().parse_event(json.dumps(body).encode())


# ------------------------------------------------------------------ mock + registry


def test_mock_provider_records_route_actions() -> None:
    mock = MockProvider()
    mock.answer("c1")
    mock.transfer("c1", "+14155550101", "TRUSTED")
    mock.voicemail("c1")
    mock.hangup("c1")
    mock.play("c1", "hello")
    assert [a.op for a in mock.actions] == ["answer", "transfer", "voicemail", "hangup", "play"]
    assert mock.actions[1].target == "+14155550101"


def test_mock_provider_satisfies_adapter_protocol() -> None:
    for adapter in (MockProvider(), GenericWebhookAdapter(), AriAdapter()):
        assert isinstance(adapter, ProviderAdapter)


def test_registry_defaults_and_override() -> None:
    reset_adapters()
    assert {"generic", "ari", "mock"} <= set(adapter_names())
    assert get_adapter("nope") is None
    custom = MockProvider()
    register_adapter(custom)
    assert get_adapter("mock") is custom
    reset_adapters()


def test_signed_request_helper_uses_injected_clock() -> None:
    body, headers = signed_request(SECRET, {"a": 1}, "nonce-abcdefgh")
    assert headers["X-CallShield-Timestamp"] == str(_now())
    assert signing.verify_signed_request(headers, body, SECRET, 300) == "nonce-abcdefgh"
