"""Pure OutboundPolicy tests (PRD 8.1 critical control)."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from callshield.domain.enums import ResolutionState, Role
from callshield.domain.state_machines import Machine, can_transition
from callshield.resolve.policy import (
    AuthorizationFacts,
    DestinationFacts,
    OutboundPolicy,
    PolicyInput,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
USER = uuid.uuid4()
CASE = uuid.uuid4()
DEST = uuid.uuid4()
AUTH = uuid.uuid4()


def base_input(**overrides: object) -> PolicyInput:
    inp = PolicyInput(
        now=NOW,
        outbound_enabled=True,
        dialer_configured=True,
        idempotency_key="k1",
        actor_user_id=USER,
        actor_role=Role.HOUSEHOLD_ADMIN,
        case_id=CASE,
        case_owner_user_id=USER,
        case_state=ResolutionState.USER_APPROVED_CONTACT.value,
        case_policy_region="US-CA",
        line_verified=True,
        destination_id=DEST,
        destination=DestinationFacts(
            case_id=CASE,
            provenance="INDEPENDENTLY_VERIFIED",
            verification_status="VERIFIED",
            has_evidence=True,
            is_current=True,
            valid_e164=True,
        ),
        authorization_id=AUTH,
        authorization=AuthorizationFacts(
            case_id=CASE,
            destination_id=DEST,
            user_id=USER,
            scope="OUTBOUND_CALL_ONCE",
            expires_at=NOW + timedelta(minutes=10),
            consumed_at=None,
        ),
        last_attempt_at=None,
        attempts_case_24h=0,
        attempts_tenant_24h=0,
        active_attempt_on_case=False,
        active_attempt_on_destination=False,
        cooldown_seconds=900,
        max_attempts_per_case_per_day=3,
        max_attempts_per_tenant_per_day=10,
        max_call_seconds=600,
    )
    return replace(inp, **overrides)  # type: ignore[arg-type]


def codes(inp: PolicyInput) -> set[str]:
    return set(OutboundPolicy.evaluate(inp).codes)


def test_fully_valid_request_is_allowed_with_finite_duration() -> None:
    decision = OutboundPolicy.evaluate(base_input())
    assert decision.allowed and decision.reasons == ()
    assert decision.max_call_seconds == 600


def test_duration_is_capped_by_hard_maximum() -> None:
    assert not OutboundPolicy.evaluate(base_input(max_call_seconds=99999)).allowed
    assert not OutboundPolicy.evaluate(base_input(max_call_seconds=0)).allowed


def test_each_gate_blocks_independently() -> None:
    dest = base_input().destination
    auth = base_input().authorization
    assert dest and auth
    cases: dict[str, dict[str, object]] = {
        "OUTBOUND_DISABLED": {"outbound_enabled": False},
        "DIALER_NOT_CONFIGURED": {"dialer_configured": False},
        "IDEMPOTENCY_KEY_MISSING": {"idempotency_key": None},
        "NOT_AUTHENTICATED": {"actor_user_id": None},
        "ROLE_NOT_PERMITTED": {"actor_role": Role.REVIEWER},
        "NOT_CASE_OWNER": {"case_owner_user_id": uuid.uuid4()},
        "CASE_STATE_INVALID": {"case_state": ResolutionState.VERIFIED_DESTINATION.value},
        "POLICY_REGION_MISSING": {"case_policy_region": "  "},
        "LINE_NOT_VERIFIED": {"line_verified": False},
        "DESTINATION_MISSING": {"destination": None},
        "DESTINATION_CALLER_ID_ONLY": {"destination": replace(dest, provenance="CALLER_ID_ONLY")},
        "DESTINATION_UNVERIFIED": {
            "destination": replace(dest, provenance="USER_PROVIDED_UNVERIFIED")
        },
        "DESTINATION_NO_EVIDENCE": {"destination": replace(dest, has_evidence=False)},
        "DESTINATION_SUPERSEDED": {"destination": replace(dest, is_current=False)},
        "DESTINATION_INVALID_NUMBER": {"destination": replace(dest, valid_e164=False)},
        "AUTHORIZATION_MISSING": {"authorization_id": None, "authorization": None},
        "AUTHORIZATION_MISMATCH": {"authorization": replace(auth, user_id=uuid.uuid4())},
        "AUTHORIZATION_CONSUMED": {"authorization": replace(auth, consumed_at=NOW)},
        "AUTHORIZATION_EXPIRED": {"authorization": replace(auth, expires_at=NOW)},
        "AUTHORIZATION_SCOPE_INVALID": {"authorization": replace(auth, scope="EVERYTHING")},
        "COOLDOWN_ACTIVE": {"last_attempt_at": NOW - timedelta(seconds=60)},
        "CASE_DAILY_CAP": {"attempts_case_24h": 3},
        "TENANT_DAILY_CAP": {"attempts_tenant_24h": 10},
        "ACTIVE_ATTEMPT_ON_CASE": {"active_attempt_on_case": True},
        "ACTIVE_ATTEMPT_ON_DESTINATION": {"active_attempt_on_destination": True},
    }
    for code, override in cases.items():
        decision = OutboundPolicy.evaluate(base_input(**override))
        assert not decision.allowed, code
        assert code in decision.codes, (code, decision.codes)


def test_destination_of_another_case_is_missing() -> None:
    dest = base_input().destination
    assert dest
    inp = base_input(destination=replace(dest, case_id=uuid.uuid4()))
    assert "DESTINATION_MISSING" in codes(inp)


def test_authorization_for_other_destination_or_case_rejected() -> None:
    auth = base_input().authorization
    assert auth
    assert "AUTHORIZATION_MISMATCH" in codes(
        base_input(authorization=replace(auth, destination_id=uuid.uuid4()))
    )
    assert "AUTHORIZATION_MISMATCH" in codes(
        base_input(authorization=replace(auth, case_id=uuid.uuid4()))
    )


def test_cooldown_reports_retry_time_and_elapses() -> None:
    last = NOW - timedelta(seconds=100)
    blocked = OutboundPolicy.evaluate(base_input(last_attempt_at=last))
    assert blocked.retry_after == last + timedelta(seconds=900)
    assert OutboundPolicy.evaluate(base_input(last_attempt_at=NOW - timedelta(seconds=901))).allowed


def test_multiple_reasons_are_all_reported() -> None:
    decision = OutboundPolicy.evaluate(
        base_input(outbound_enabled=False, authorization_id=None, authorization=None)
    )
    assert {"OUTBOUND_DISABLED", "AUTHORIZATION_MISSING"} <= set(decision.codes)
    assert decision.max_call_seconds == 0


def test_retry_count_cannot_substitute_for_authorization() -> None:
    """There is no 'retries' field at all; absent authorization always blocks."""
    fields = set(PolicyInput.__dataclass_fields__)
    assert not {"retries", "retry_count", "phone", "number"} & fields
    assert "AUTHORIZATION_MISSING" in codes(base_input(authorization_id=None, authorization=None))


def test_eleven_separate_attempts_each_representable_with_own_authorization() -> None:
    """No hard lifetime cutoff: attempt N is allowed once cooldown + daily window clear."""
    now = NOW
    for n in range(11):
        auth = replace(
            base_input().authorization, consumed_at=None, expires_at=now + timedelta(minutes=5)
        )  # type: ignore[type-var]
        inp = base_input(
            now=now,
            authorization=auth,
            last_attempt_at=None if n == 0 else now - timedelta(days=2),
            attempts_case_24h=0,
            attempts_tenant_24h=0,
        )
        assert OutboundPolicy.evaluate(inp).allowed, n
        now += timedelta(days=2)


def test_resolution_machine_has_no_suppressed_state_and_guards_fail_closed() -> None:
    assert not any("SUPPRESS" in s.value for s in ResolutionState)
    edge = (Machine.RESOLUTION, ResolutionState.USER_APPROVED_CONTACT, ResolutionState.IN_PROGRESS)
    assert not can_transition(*edge, {})
    assert can_transition(
        *edge,
        {
            "policy_approved": True,
            "user_authorization_id": "a",
            "destination_independently_verified": True,
        },
    )
    assert not can_transition(
        Machine.RESOLUTION, ResolutionState.ACKNOWLEDGED, ResolutionState.REOPENED
    )
    assert not can_transition(
        Machine.RESOLUTION, ResolutionState.MONITORING, ResolutionState.REOPENED
    )
