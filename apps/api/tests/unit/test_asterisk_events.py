"""ARI -> ProviderCallEvent mapping and STIR/SHAKEN trust rules (no DB, no network)."""

from __future__ import annotations

import pytest

from callshield.telephony.asterisk.events import (
    build_attestation,
    channel_id,
    dtmf_digit,
    fetch_attestation,
    is_inbound_start,
    map_ari_event,
)
from callshield.telephony.attestation import parse_attestation_level
from callshield.telephony.base import MalformedEventError, ProviderEventType
from tests.asterisk_support import (
    CALLER_E164,
    LINE_E164,
    FakeAri,
    ari_start,
    passed_identity,
    run,
)


def test_stasis_start_maps_to_call_received() -> None:
    event = map_ari_event(ari_start("c1"))
    assert event is not None
    assert event.event_type is ProviderEventType.CALL_RECEIVED
    assert (event.provider, event.call_id) == ("ari", "c1")
    assert event.to_number == LINE_E164 and event.from_number == CALLER_E164
    assert event.endpoint_id == "trunk" and event.direction == "INBOUND"


def test_non_inbound_stasis_start_is_ignored() -> None:
    data = ari_start("c1")
    data["args"] = ["outbound"]
    assert not is_inbound_start(data)
    assert map_ari_event(data) is None


@pytest.mark.parametrize(
    ("ari_type", "extra", "expected"),
    [
        ("ChannelDestroyed", {}, ProviderEventType.CALL_ENDED),
        ("ChannelHangupRequest", {}, ProviderEventType.CALL_ENDED),
        ("BlindTransfer", {}, ProviderEventType.CALL_TRANSFERRED),
        ("ChannelStateChange", {"state": "Up"}, ProviderEventType.CALL_ANSWERED),
    ],
)
def test_lifecycle_events_map(ari_type: str, extra: dict[str, str], expected: object) -> None:
    data = {"type": ari_type, "timestamp": "2026-10-27T12:00:01.000+0000"}
    data["channel"] = {"id": "c1", "name": "PJSIP/trunk-00000001", **extra}  # type: ignore[assignment]
    event = map_ari_event(data)
    assert event is not None and event.event_type is expected
    assert event.to_number is None and event.attestation == {}  # only the start carries these


def test_stasis_end_is_not_a_call_end_and_ringing_is_ignored() -> None:
    chan = {"id": "c1", "name": "PJSIP/trunk-1"}
    assert map_ari_event({"type": "StasisEnd", "channel": chan}) is None
    assert (
        map_ari_event({"type": "ChannelStateChange", "channel": {**chan, "state": "Ring"}}) is None
    )


def test_registration_events() -> None:
    down = map_ari_event(
        {
            "type": "PeerStatusChange",
            "endpoint": {"resource": "trunk"},
            "peer": {"peer_status": "Unreachable"},
        }
    )
    up = map_ari_event(
        {"type": "EndpointStateChange", "endpoint": {"resource": "trunk", "state": "online"}}
    )
    assert down is not None and down.event_type is ProviderEventType.REGISTRATION_FAILED
    assert down.endpoint_id is None or down.endpoint_id == "trunk"
    assert up is not None and up.event_type is ProviderEventType.REGISTRATION_OK


def test_event_ids_are_deterministic_for_redelivery() -> None:
    a, b = map_ari_event(ari_start("c1")), map_ari_event(ari_start("c1"))
    assert a is not None and b is not None and a.event_id == b.event_id


def test_malformed_events_raise_or_ignore() -> None:
    with pytest.raises(MalformedEventError):
        map_ari_event(
            {"type": "StasisStart", "args": ["inbound", "not a number"], "channel": {"id": "c1"}}
        )
    with pytest.raises(MalformedEventError):
        map_ari_event({"type": "ChannelDestroyed"})
    assert map_ari_event({"type": "SomethingElse"}) is None


def test_dtmf_helpers() -> None:
    data = {"type": "ChannelDtmfReceived", "digit": "5", "channel": {"id": "c1"}}
    assert dtmf_digit(data) == "5" and channel_id(data) == "c1"
    assert dtmf_digit({"type": "ChannelDtmfReceived", "digit": "55"}) is None


# ------------------------------------------------------------------ attestation
def test_passed_verification_keeps_identity_level() -> None:
    attestation = build_attestation(passed_identity("A"), None, None, "TN-Validation-Passed-A")
    assert parse_attestation_level(attestation) == "A"


def test_forged_identity_without_verification_is_none() -> None:
    forged = build_attestation(passed_identity("A"), "A", None, None)
    assert parse_attestation_level(forged) == "NONE"
    failed = build_attestation(passed_identity("A"), "A", "Failure", "TN-Validation-Failed")
    assert parse_attestation_level(failed) == "NONE"


def test_verify_result_success_with_attest_value() -> None:
    attestation = build_attestation(None, "B", "Success", None)
    assert parse_attestation_level(attestation) == "B"


def test_fetch_attestation_reads_channel_variables() -> None:
    ari = FakeAri()
    ari.variables = {
        "PJSIP_HEADER(read,Identity)": passed_identity("A"),
        "STIR_SHAKEN(0,verify_result)": "Success",
    }
    attestation = run(fetch_attestation(ari, "c1"))
    assert parse_attestation_level(attestation) == "A"
    stripped = run(fetch_attestation(FakeAri(), "c1"))  # nothing set: stripped by the trunk
    assert parse_attestation_level(stripped) == "NONE"


def test_attestation_rides_on_the_received_event() -> None:
    attestation = build_attestation(None, "A", None, "TN-Validation-Passed-A")
    event = map_ari_event(ari_start("c1"), attestation)
    assert event is not None and parse_attestation_level(event.attestation) == "A"
