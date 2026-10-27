"""Pure eligibility gates: each gate, fail-closed defaults, calling hours, caps."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from callshield.callback.gates import (
    AuthorizationFacts,
    AuthorizationMode,
    CapLimits,
    CapsFacts,
    GateInputs,
    GateName,
    evaluate_gates,
)
from callshield.callback.timeutil import resolve_timezone

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
USER_TZ = timezone(timedelta(hours=-5))  # 10:00 local
LIMITS = CapLimits(2, 2, 5)
NO_CAPS = CapsFacts(0, 0, 0, None, 0)


def good(**overrides: object) -> GateInputs:
    base = GateInputs(
        now=NOW,
        inbound_found=True,
        destination_present=True,
        inbound_at=NOW - timedelta(hours=1),
        attestation_level="A",
        window_hours=72,
        authorization=AuthorizationFacts(True, NOW + timedelta(minutes=30), False),
        blocked_by_user=False,
        blocked_by_instance=False,
        caps=NO_CAPS,
        limits=LIMITS,
        user_tz=USER_TZ,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def at(now: datetime, **overrides: object) -> GateInputs:
    """`good()` re-based to another clock (authorization and inbound call move with it)."""
    auth = AuthorizationFacts(True, now + timedelta(minutes=30), False)
    return good(now=now, inbound_at=now - timedelta(hours=1), authorization=auth, **overrides)


def failed(inputs: GateInputs) -> dict[str, str]:
    return {r.gate.value: r.reason for r in evaluate_gates(inputs).failures}


def test_all_gates_pass_and_all_seven_are_reported() -> None:
    report = evaluate_gates(good())
    assert report.allowed
    assert [r.gate for r in report.results] == list(GateName)
    assert len(report.results) == 7
    assert all(r.message and r.reason for r in report.results)


def test_everything_missing_fails_closed() -> None:
    report = evaluate_gates(GateInputs(now=NOW))
    assert not report.allowed
    assert {r.gate for r in report.failures} == set(GateName)
    assert all(r.message for r in report.failures)


@pytest.mark.parametrize(
    ("field", "value", "gate"),
    [
        ("inbound_found", False, "INBOUND_RECORD"),
        ("destination_present", False, "INBOUND_RECORD"),
        ("inbound_at", None, "WITHIN_WINDOW"),
        ("window_hours", None, "WITHIN_WINDOW"),
        ("authorization", None, "USER_AUTHORIZATION"),
        ("blocked_by_user", None, "NOT_BLOCKLISTED"),
        ("blocked_by_instance", None, "NOT_BLOCKLISTED"),
        ("caps", None, "CAPS"),
        ("limits", None, "CAPS"),
        ("user_tz", None, "CALLING_HOURS"),
        ("attestation_level", None, "ATTESTATION_A"),
    ],
)
def test_each_missing_field_blocks_exactly_its_gate(field: str, value: object, gate: str) -> None:
    assert set(failed(good(**{field: value}))) == {gate}


@pytest.mark.parametrize(
    ("level", "stripped", "reason"),
    [
        ("B", False, "ATTESTATION_NOT_A"),
        ("C", False, "ATTESTATION_NOT_A"),
        ("NONE", False, "ATTESTATION_MISSING"),
        (None, False, "ATTESTATION_MISSING"),
        ("garbage", False, "ATTESTATION_MISSING"),
        ("A", True, "ATTESTATION_MISSING"),  # stripped by forwarding == missing
    ],
)
def test_attestation_must_be_a_and_present(level: str | None, stripped: bool, reason: str) -> None:
    report = evaluate_gates(good(attestation_level=level, attestation_stripped=stripped))
    assert failed(good(attestation_level=level, attestation_stripped=stripped)) == {
        "ATTESTATION_A": reason
    }
    assert report.complaint_flow_only
    assert report.as_dict()["complaint_flow_offered"] is True


def test_window_boundaries() -> None:
    edge = good(inbound_at=NOW - timedelta(hours=72))
    assert evaluate_gates(edge).allowed
    assert failed(good(inbound_at=NOW - timedelta(hours=72, seconds=1))) == {
        "WITHIN_WINDOW": "OUTSIDE_WINDOW"
    }
    assert failed(good(inbound_at=NOW + timedelta(hours=2))) == {
        "WITHIN_WINDOW": "INBOUND_TIME_INVALID"
    }
    assert failed(good(window_hours=1, inbound_at=NOW - timedelta(hours=2))) == {
        "WITHIN_WINDOW": "OUTSIDE_WINDOW"
    }


def test_authorization_states() -> None:
    assert failed(good(authorization=None)) == {"USER_AUTHORIZATION": "AUTHORIZATION_MISSING"}
    expired = AuthorizationFacts(True, NOW, False)
    assert failed(good(authorization=expired)) == {"USER_AUTHORIZATION": "AUTHORIZATION_EXPIRED"}
    used = AuthorizationFacts(True, NOW + timedelta(minutes=5), True)
    assert failed(good(authorization=used)) == {"USER_AUTHORIZATION": "AUTHORIZATION_USED"}
    other = AuthorizationFacts(False, NOW + timedelta(minutes=5), False)
    assert failed(good(authorization=other)) == {"USER_AUTHORIZATION": "AUTHORIZATION_MISMATCH"}


def test_preview_mode_defers_gate_four_to_the_tap() -> None:
    report = evaluate_gates(good(authorization=None, authorization_mode=AuthorizationMode.PREVIEW))
    assert report.allowed
    gate = next(r for r in report.results if r.gate is GateName.USER_AUTHORIZATION)
    assert gate.reason == "AWAITING_TAP"


def test_blocklists() -> None:
    assert failed(good(blocked_by_user=True)) == {"NOT_BLOCKLISTED": "BLOCKED_USER"}
    assert failed(good(blocked_by_instance=True)) == {"NOT_BLOCKLISTED": "BLOCKED_INSTANCE"}
    assert failed(good(blocked_by_user=None)) == {"NOT_BLOCKLISTED": "BLOCKLIST_UNKNOWN"}


@pytest.mark.parametrize(
    ("caps", "reason"),
    [
        (CapsFacts(2, 0, 0, None, 0), "CAP_PER_INBOUND_CALL"),
        (CapsFacts(0, 2, 0, None, 0), "CAP_PER_NUMBER_30D"),
        (CapsFacts(0, 0, 5, None, 0), "CAP_PER_USER_DAY"),
        (CapsFacts(0, 0, 1, NOW - timedelta(minutes=9), 0), "CAP_MIN_SPACING"),
        (CapsFacts(0, 0, 0, None, 3), "CAP_INSTANCE_30D"),
    ],
)
def test_each_cap(caps: CapsFacts, reason: str) -> None:
    assert failed(good(caps=caps)) == {"CAPS": reason}


def test_cap_boundaries_and_config_cannot_exceed_ceilings() -> None:
    assert evaluate_gates(good(caps=CapsFacts(1, 1, 4, NOW - timedelta(minutes=10), 2))).allowed
    over = CapLimits(99, 99, 99)  # even a mis-set limit is clamped to the ceilings
    assert failed(good(limits=over, caps=CapsFacts(2, 0, 0, None, 0))) == {
        "CAPS": "CAP_PER_INBOUND_CALL"
    }
    assert failed(good(limits=over, caps=CapsFacts(0, 0, 5, None, 0))) == {
        "CAPS": "CAP_PER_USER_DAY"
    }
    tight = CapLimits(1, 1, 1)
    assert failed(good(limits=tight, caps=CapsFacts(1, 0, 0, None, 0))) == {
        "CAPS": "CAP_PER_INBOUND_CALL"
    }


@pytest.mark.parametrize(
    ("hour_utc", "ok"),
    [(12, False), (13, True), (20, True), (2, False)],
)
def test_user_local_calling_hours(hour_utc: int, ok: bool) -> None:
    # user tz is UTC-5: 13:00 UTC = 08:00 local (first allowed hour); 02:00 UTC = 21:00 local
    assert evaluate_gates(at(NOW.replace(hour=hour_utc))).allowed is ok


def test_hours_edges_8am_inclusive_9pm_exclusive() -> None:
    eight = datetime(2026, 10, 27, 13, 0, tzinfo=UTC)  # 08:00 local
    nine_pm = datetime(2026, 10, 28, 2, 0, tzinfo=UTC)  # 21:00 local
    just_before = nine_pm - timedelta(seconds=1)  # 20:59:59 local
    for now, ok in ((eight, True), (just_before, True), (nine_pm, False)):
        assert evaluate_gates(at(now)).allowed is ok
    seven_59 = eight - timedelta(seconds=1)
    assert failed(at(seven_59)) == {"CALLING_HOURS": "OUTSIDE_USER_HOURS"}


def test_unknown_user_timezone_fails_closed() -> None:
    assert failed(good(user_tz=None)) == {"CALLING_HOURS": "USER_TIMEZONE_UNKNOWN"}
    assert resolve_timezone("Not/AZone") is None and resolve_timezone(None) is None


def test_destination_local_time_only_applies_when_a_geography_is_provided() -> None:
    pacific = timezone(timedelta(hours=-8))  # 07:00 at NOW -> outside hours there
    assert evaluate_gates(good()).allowed  # no geography: user's time only
    blocked = good(destination_geography_provided=True, destination_tz=pacific)
    assert failed(blocked) == {"CALLING_HOURS": "OUTSIDE_DESTINATION_HOURS"}
    eastern = timezone(timedelta(hours=-4))
    assert evaluate_gates(good(destination_geography_provided=True, destination_tz=eastern)).allowed
    unresolvable = good(destination_geography_provided=True, destination_tz=None)
    assert failed(unresolvable) == {"CALLING_HOURS": "DESTINATION_TIMEZONE_INVALID"}


def test_fixed_offset_timezone_names_resolve() -> None:
    assert resolve_timezone("UTC") == UTC
    tz = resolve_timezone("UTC-05:30")
    assert tz is not None and NOW.astimezone(tz).hour == 9 and NOW.astimezone(tz).minute == 30
    assert resolve_timezone("UTC+15:00") is None


def test_report_dict_shape_for_the_api() -> None:
    body = evaluate_gates(good(attestation_level="B")).as_dict()
    assert body["allowed"] is False and body["blocked_by"] == ["ATTESTATION_A"]
    assert {g["gate"] for g in body["gates"]} == {g.value for g in GateName}
    assert all({"reason", "message", "passed"} <= set(g) for g in body["gates"])
