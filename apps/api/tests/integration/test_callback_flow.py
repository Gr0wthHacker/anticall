"""Callback API + dialer service: authorization, concurrency, time travel, tenancy, retention."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from callshield.callback import dialer_service, gates
from callshield.callback.mock_scenarios import ANSWERED, CallbackScenario, ev, say
from callshield.callback.retention import sweep
from callshield.callback.runtime import get_runtime
from callshield.db.models import AuditEvent, Case, ProtectedLine
from callshield.db.models.callback_ext import (
    CallbackAttempt,
    CallbackAuthorization,
    CallbackBlocklist,
    CallbackGlobalAttempt,
    CallbackMedia,
    CallbackRequest,
    CallbackResult,
)
from callshield.db.models.platform import Job, LedgerEvent
from callshield.worker.queue import enqueue, registered_kinds
from callshield.worker.runner import load_handler_modules, run_one

from ..callback_support import (
    CALLER_E164,
    CbEnv,
    bypass_queue,
    dial_directly,
    dump_db_text,
    result_of,
    second_env,
)


def status_of(cb: CbEnv, request_id: str | uuid.UUID) -> str:
    with cb.session() as s:
        row = s.execute(
            select(CallbackRequest).where(CallbackRequest.id == uuid.UUID(str(request_id)))
        ).scalar_one()
        return row.status


def reasons_of(cb: CbEnv, request_id: str | uuid.UUID) -> list[str]:
    with cb.session() as s:
        row = s.execute(
            select(CallbackRequest).where(CallbackRequest.id == uuid.UUID(str(request_id)))
        ).scalar_one()
        return list(row.gate_report.get("predial_blocked_by", []))


# ------------------------------------------------------------------ master switch / roles


def test_everything_is_404_when_callback_enabled_is_false(cb: CbEnv) -> None:
    call_id = cb.add_inbound()
    cb.runtime.settings = cb.runtime.settings.model_copy(update={"callback_enabled": False})
    fake = uuid.uuid4()
    probes = [
        ("get", f"/v1/calls/{call_id}/callback-eligibility", None),
        ("post", f"/v1/calls/{call_id}/callbacks", {}),
        ("post", f"/v1/callbacks/{fake}/cancel", {}),
        ("get", "/v1/callbacks", None),
        ("get", f"/v1/callbacks/{fake}", None),
        ("get", "/v1/callback/settings", None),
        ("patch", "/v1/callback/settings", {"callback_window_hours": 24}),
        ("get", "/v1/callback/blocklist", None),
        ("post", f"/v1/lines/{cb.line_id}/outbound-ownership-check", {}),
    ]
    for method, path, body in probes:
        resp = cb.client.request(
            method.upper(), path, json=body, headers={**cb.user.headers, "Idempotency-Key": "k"}
        )
        assert resp.status_code == 404, (method, path, resp.status_code)
    caps = cb.get("/v1/callback/capabilities")
    assert caps.status_code == 200 and caps.json()["callback_enabled"] is False
    assert cb.dialer.calls_placed == 0


def test_default_is_disabled_and_meta_endpoint_is_untouched(client: Any) -> None:
    from callshield.callback.config import CallbackSettings

    assert CallbackSettings().callback_enabled is False
    assert "callback_enabled" not in client.get("/v1/meta/capabilities").text


def test_capabilities_reports_enabled_and_ceilings(cb: CbEnv) -> None:
    body = cb.get("/v1/callback/capabilities").json()
    assert body["callback_enabled"] is True and body["remote_ai_processing"] is False
    assert body["ceilings"]["instance_max_per_number_30d"] == 3


def test_user_must_opt_in_after_the_instance_flag(cb: CbEnv) -> None:
    patched = cb.client.patch(
        "/v1/callback/settings", json={"callback_enabled": False}, headers=cb.user.headers
    )
    assert patched.status_code == 200
    resp = cb.queue(cb.add_inbound())
    assert (
        resp.status_code == 403 and resp.json()["error"]["code"] == "CALLBACK_NOT_ENABLED_FOR_USER"
    )


def test_dial_is_blocked_if_the_instance_flag_is_turned_off_after_queueing(cb: CbEnv) -> None:
    queued = cb.queue(cb.add_inbound()).json()
    cb.runtime.settings = cb.runtime.settings.model_copy(update={"callback_enabled": False})
    cb.run_jobs()
    assert status_of(cb, queued["id"]) == "BLOCKED" and cb.dialer.calls_placed == 0


def test_idempotency_key_is_required_and_replays_return_the_same_request(cb: CbEnv) -> None:
    call_id = cb.add_inbound()
    missing = cb.client.post(f"/v1/calls/{call_id}/callbacks", json={}, headers=cb.user.headers)
    assert (
        missing.status_code == 422 and missing.json()["error"]["code"] == "IDEMPOTENCY_KEY_MISSING"
    )
    first = cb.queue(call_id, key="same-key").json()
    again = cb.queue(call_id, key="same-key").json()
    assert again["id"] == first["id"] and again["replayed"] is True and first["replayed"] is False
    with cb.session() as s:
        assert len(s.execute(select(CallbackAuthorization)).scalars().all()) == 1
        assert len(s.execute(select(Job).where(Job.kind == "callback.dial")).scalars().all()) == 1


# ------------------------------------------------------------------ authorization


def test_authorization_is_logged_with_user_time_call_and_app_version(cb: CbEnv) -> None:
    call_id = cb.add_inbound()
    resp = cb.queue(call_id, app_version="2.4.1")
    assert resp.status_code == 202
    with cb.session() as s:
        auth = s.execute(select(CallbackAuthorization)).scalar_one()
        events = s.execute(
            select(LedgerEvent).where(LedgerEvent.topic == "callback.authorized")
        ).scalar_one()
    assert auth.user_id == cb.user.user_id and auth.inbound_call_id == call_id
    assert auth.app_version == "2.4.1" and auth.authorized_at == cb.clock()
    assert auth.expires_at - auth.authorized_at == timedelta(minutes=30)
    assert events.payload["app_version"] == "2.4.1" and events.payload["user_id"]


def test_authorization_expires_after_30_minutes_if_the_dial_has_not_started(cb: CbEnv) -> None:
    ok = cb.queue(cb.add_inbound()).json()
    cb.clock.advance(minutes=29, seconds=59)
    cb.run_jobs()
    assert status_of(cb, ok["id"]) == "COMPLETED" and cb.dialer.calls_placed == 1
    cb.clock.advance(minutes=11)
    late = cb.queue(cb.add_inbound()).json()
    cb.clock.advance(minutes=30, seconds=1)
    cb.run_jobs()
    assert status_of(cb, late["id"]) == "BLOCKED"
    assert "AUTHORIZATION_EXPIRED" in reasons_of(cb, late["id"])
    assert cb.dialer.calls_placed == 1


def test_authorization_is_single_use(cb: CbEnv) -> None:
    call_id = cb.add_inbound()
    request_id = bypass_queue(cb, call_id)
    first = dial_directly(cb, request_id)
    assert first.placed
    with cb.session() as s:
        auth = s.execute(select(CallbackAuthorization)).scalar_one()
        assert auth.consumed_at is not None
        # a second request tied to the SAME consumed authorization cannot dial
        twin = CallbackRequest(
            tenant_id=cb.user.tenant_id,
            user_id=cb.user.user_id,
            inbound_call_id=call_id,
            line_id=cb.line_id,
            authorization_id=auth.id,
            destination_key=cb.runtime.keyer.key(CALLER_E164),
            destination_masked="+1415***0123",
            idempotency_key="twin",
        )
        s.add(twin)
        s.commit()
        twin_id = twin.id
    cb.clock.advance(minutes=11)
    result = dial_directly(cb, twin_id)
    assert result.status == "BLOCKED" and "AUTHORIZATION_USED" in result.reasons
    assert cb.dialer.calls_placed == 1


def test_cancel_before_dial_stops_the_call_and_revokes_the_authorization(cb: CbEnv) -> None:
    queued = cb.queue(cb.add_inbound()).json()
    cancelled = cb.post(f"/v1/callbacks/{queued['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    cb.run_jobs()
    assert cb.dialer.calls_placed == 0
    assert cb.post(f"/v1/callbacks/{queued['id']}/cancel").status_code == 409
    again = cb.queue(cb.add_inbound(), key="after-cancel")
    assert again.status_code == 202  # the slot was freed


def test_a_completed_callback_cannot_be_cancelled(cb: CbEnv) -> None:
    queued = cb.queue(cb.add_inbound()).json()
    cb.run_jobs()
    assert cb.post(f"/v1/callbacks/{queued['id']}/cancel").status_code == 409


# ------------------------------------------------------------------ gates run again at dial


def test_dial_path_reevaluates_gates_even_if_queue_time_check_is_skipped(
    cb: CbEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    real = gates.evaluate_gates

    def spy(inputs: Any) -> Any:
        calls["n"] += 1
        return real(inputs)

    monkeypatch.setattr("callshield.callback.dialer_service.evaluate_gates", spy)
    call_id = cb.add_inbound()
    request_id = bypass_queue(cb, call_id)  # no queue-time evaluation at all
    assert dial_directly(cb, request_id).placed
    assert calls["n"] == 2  # once to prepare, once immediately before the ring


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("attestation_b", "ATTESTATION_NOT_A"),
        ("window", "OUTSIDE_WINDOW"),
        ("blocklist_user", "BLOCKED_USER"),
        ("blocklist_everywhere", "BLOCKED_INSTANCE"),
        ("no_timezone", "USER_TIMEZONE_UNKNOWN"),
    ],
)
def test_state_changes_between_queue_and_dial_block_the_call(
    cb: CbEnv, mutation: str, reason: str
) -> None:
    from callshield.callback.blocklist import block_destination
    from callshield.db.models import CallerObservation
    from callshield.db.models.callback_ext import CallbackSettingsRow

    call_id = cb.add_inbound()
    queued = cb.queue(call_id).json()
    with cb.session() as s:
        if mutation == "attestation_b":
            obs = s.execute(select(CallerObservation)).scalar_one()
            obs.attestation_metadata = {"attestation_level": "B"}
        elif mutation == "blocklist_user":
            s.add(
                CallbackBlocklist(
                    tenant_id=cb.user.tenant_id,
                    user_id=cb.user.user_id,
                    destination_key=cb.runtime.keyer.key(CALLER_E164),
                    reason="MANUAL",
                )
            )
        elif mutation == "blocklist_everywhere":
            key = cb.runtime.keyer.key(CALLER_E164)
            block_destination(s, cb.user.tenant_id, cb.user.user_id, key, "+1415***0123", "MANUAL")
        elif mutation == "no_timezone":
            row = s.execute(select(CallbackSettingsRow)).scalar_one()
            row.timezone = None
        s.commit()
    if mutation == "window":
        cb.clock.advance(hours=73)
        # still inside 08:00-21:00 local: 15:00 UTC + 73h = 16:00 UTC
    cb.run_jobs()
    assert status_of(cb, queued["id"]) == "BLOCKED"
    assert reason in reasons_of(cb, queued["id"]) and cb.dialer.calls_placed == 0


def test_last_look_before_ringing_catches_a_blocklist_added_after_preparation(cb: CbEnv) -> None:
    from callshield.callback.blocklist import block_destination

    cb.queue(cb.add_inbound())
    load_handler_modules()
    from callshield.db.session import make_session_factory

    factory = make_session_factory(cb.engine)
    assert run_one(factory, ["callback.dial"])  # prepared + attempt counted, ring not yet placed
    with cb.session() as s:
        key = cb.runtime.keyer.key(CALLER_E164)
        block_destination(s, cb.user.tenant_id, cb.user.user_id, key, "+1415***0123", "STOP")
        s.commit()
    assert run_one(factory, ["callback.place"])
    assert cb.dialer.calls_placed == 0
    with cb.session() as s:
        row = s.execute(select(CallbackRequest)).scalar_one()
        assert row.status == "BLOCKED" and row.placed_at is None
        assert len(s.execute(select(CallbackAttempt)).scalars().all()) == 1  # still counted


# ------------------------------------------------------------------ concurrency / idempotency


def test_two_simultaneous_queue_requests_yield_one_dial(cb: CbEnv) -> None:
    call_id = cb.add_inbound()
    first = cb.queue(call_id, key="req-a")
    second = cb.queue(call_id, key="req-b")  # different key, same call, nothing dialed yet
    assert first.status_code == 202
    assert second.status_code == 409 and second.json()["error"]["code"] == "CALLBACK_ALREADY_QUEUED"
    cb.run_jobs()
    assert cb.dialer.calls_placed == 1


def test_open_slot_unique_constraint_backs_the_one_dial_guarantee(cb: CbEnv) -> None:
    call_id = cb.add_inbound()
    cb.queue(call_id)
    with cb.session() as s:
        template = s.execute(select(CallbackRequest)).scalar_one()
        clone = CallbackRequest(
            tenant_id=template.tenant_id,
            user_id=template.user_id,
            inbound_call_id=template.inbound_call_id,
            line_id=template.line_id,
            authorization_id=template.authorization_id,
            destination_key=template.destination_key,
            destination_masked=template.destination_masked,
            open_slot=template.open_slot,
            idempotency_key="clone",
        )
        s.add(clone)
        with pytest.raises(IntegrityError):
            s.commit()


def test_replaying_the_dial_job_or_running_two_workers_dials_once(cb: CbEnv) -> None:
    queued = cb.queue(cb.add_inbound()).json()
    request_id = uuid.UUID(queued["id"])
    cb.run_jobs()
    assert cb.dialer.calls_placed == 1
    for _ in range(3):
        assert dial_directly(cb, request_id).placed is False
        with cb.session() as s:
            assert (
                dialer_service.place_call(s, cb.runtime, cb.user.tenant_id, request_id).placed
                is False
            )
    with cb.session() as s:  # force the job back to QUEUED, as a crashed worker would
        for job in s.execute(select(Job)).scalars():
            job.state = "QUEUED"
            job.attempts = 0
        s.commit()
    cb.run_jobs()
    assert cb.dialer.calls_placed == 1
    with cb.session() as s:
        assert len(s.execute(select(CallbackAttempt)).scalars().all()) == 1
        assert len(s.execute(select(CallbackGlobalAttempt)).scalars().all()) == 1


def test_place_job_is_single_attempt_so_a_crash_cannot_ring_twice(cb: CbEnv) -> None:
    cb.queue(cb.add_inbound())
    cb.run_jobs()
    with cb.session() as s:
        place = s.execute(select(Job).where(Job.kind == "callback.place")).scalar_one()
        assert place.max_attempts == 1
    assert {"callback.dial", "callback.place", "callback.expire_sweep"} <= set(registered_kinds())


# ------------------------------------------------------------------ caps over time


def test_min_ten_minutes_between_attempts_and_five_per_user_per_day(cb: CbEnv) -> None:
    numbers = [f"+141555501{n:02d}" for n in range(10, 17)]

    def go(number: str) -> str:
        call = cb.add_inbound(from_displayed=number)
        return dial_directly(cb, bypass_queue_for(cb, call, number)).status

    first = go(numbers[0])
    assert first == "COMPLETED"
    cb.clock.advance(minutes=9, seconds=59)
    call = cb.add_inbound(from_displayed=numbers[1])
    spaced = dial_directly(cb, bypass_queue_for(cb, call, numbers[1]))
    assert spaced.status == "BLOCKED" and "CAP_MIN_SPACING" in spaced.reasons
    for number in numbers[1:5]:
        cb.clock.advance(minutes=10)
        assert go(number) == "COMPLETED"
    cb.clock.advance(minutes=10)
    call6 = cb.add_inbound(from_displayed=numbers[5])
    sixth = dial_directly(cb, bypass_queue_for(cb, call6, numbers[5]))
    assert sixth.status == "BLOCKED" and "CAP_PER_USER_DAY" in sixth.reasons
    assert cb.dialer.calls_placed == 5
    cb.clock.advance(hours=24)  # rolling 24h window: allowed again (stay inside calling hours)
    call7 = cb.add_inbound(from_displayed=numbers[6])
    assert dial_directly(cb, bypass_queue_for(cb, call7, numbers[6])).status == "COMPLETED"


def bypass_queue_for(cb: CbEnv, call_id: uuid.UUID, number: str) -> uuid.UUID:
    request_id = bypass_queue(cb, call_id)
    with cb.session() as s:
        row = s.execute(
            select(CallbackRequest).where(CallbackRequest.id == request_id)
        ).scalar_one()
        row.destination_key = cb.runtime.keyer.key(number)
        s.commit()
    return request_id


def test_rolling_30_day_window_expires_old_attempts(cb: CbEnv) -> None:
    c1, c2 = cb.add_inbound(), cb.add_inbound()
    assert dial_directly(cb, bypass_queue(cb, c1)).status == "COMPLETED"
    cb.clock.advance(minutes=11)
    assert dial_directly(cb, bypass_queue(cb, c2)).status == "COMPLETED"
    cb.clock.advance(days=29)
    c3 = cb.add_inbound()
    assert dial_directly(cb, bypass_queue(cb, c3)).status == "BLOCKED"  # inside the window
    cb.clock.advance(days=2)
    c4 = cb.add_inbound()
    assert dial_directly(cb, bypass_queue(cb, c4)).status == "COMPLETED"  # attempts rolled off
    assert cb.dialer.calls_placed == 3


def test_per_user_caps_cannot_be_raised_through_the_api_or_stored_rows(cb: CbEnv) -> None:
    for key, value in (("max_attempts_per_inbound", 3), ("max_attempts_per_user_day", 6)):
        resp = cb.client.patch("/v1/callback/settings", json={key: value}, headers=cb.user.headers)
        assert resp.status_code == 422
    from callshield.db.models.callback_ext import CallbackSettingsRow

    with cb.session() as s:  # a hand-edited row above the ceiling is ignored
        row = s.execute(select(CallbackSettingsRow)).scalar_one()
        row.overrides = {**row.overrides, "max_attempts_per_inbound": 9}
        s.commit()
    body = cb.get("/v1/callback/settings").json()["settings"]
    assert body["max_attempts_per_inbound"] == 2


# ------------------------------------------------------------------ settings API


def test_settings_roles_ranges_and_locks(cb: CbEnv) -> None:
    patch = cb.client.patch
    h, oh = cb.user.headers, cb.operator_headers
    body = cb.get("/v1/callback/settings").json()
    assert body["settings"]["callback_window_hours"] == 72 and body["timezone"] == "UTC-05:00"
    assert body["changeable_by"]["max_call_seconds"] == "operator"
    assert (
        patch("/v1/callback/settings", json={"callback_window_hours": 24}, headers=h).status_code
        == 200
    )
    assert cb.get("/v1/callback/settings").json()["settings"]["callback_window_hours"] == 24
    assert (
        patch("/v1/callback/settings", json={"callback_window_hours": 169}, headers=h).status_code
        == 422
    )
    assert (
        patch("/v1/callback/settings", json={"max_call_seconds": 60}, headers=h).status_code == 403
    )
    assert (
        patch("/v1/callback/settings", json={"remote_ai_processing": True}, headers=h).status_code
        == 422
    )
    assert (
        patch("/v1/callback/settings", json={"allowed_attestation": "B"}, headers=oh).status_code
        == 422
    )
    assert (
        patch("/v1/callback/settings", json={"outbound_recording": True}, headers=h).status_code
        == 422
    )
    assert (
        patch("/v1/callback/settings", json={"timezone": "Mars/Base"}, headers=h).status_code == 422
    )
    ok = patch("/v1/callback/settings", json={"max_call_seconds": 60}, headers=oh)
    assert ok.status_code == 200 and ok.json()["settings"]["max_call_seconds"] == 60
    assert (
        patch("/v1/callback/settings", json={"max_call_seconds": 121}, headers=oh).status_code
        == 422
    )
    assert (
        patch("/v1/callback/settings", json={"callback_window_hours": 5}, headers=oh).status_code
        == 403
    )
    assert cb.get("/v1/callback/settings").json()["settings"]["max_call_seconds"] == 60
    tz = patch("/v1/callback/settings", json={"timezone": "UTC-06:00"}, headers=h)
    assert tz.status_code == 200 and tz.json()["timezone"] == "UTC-06:00"
    with cb.session() as s:
        actions = set(s.execute(select(AuditEvent.action)).scalars())
    assert "callback.settings.updated" in actions


def test_settings_are_per_user_and_per_tenant(cb: CbEnv) -> None:
    other = second_env(cb, cb.spare)
    cb.client.patch(
        "/v1/callback/settings", json={"callback_window_hours": 6}, headers=cb.user.headers
    )
    seen = other.get("/v1/callback/settings").json()["settings"]
    assert seen["callback_window_hours"] == 72 and seen["callback_enabled"] is True


def test_reviewer_style_roles_cannot_change_settings_or_queue(cb: CbEnv) -> None:
    from callshield.callback.service import QueueCommand  # noqa: F401

    assert cb.client.patch("/v1/callback/settings", json={}, headers={}).status_code == 401
    call_id = cb.add_inbound()
    resp = cb.client.post(
        f"/v1/calls/{call_id}/callbacks",
        json={},
        headers={**cb.operator_headers, "Idempotency-Key": "k"},
    )
    assert resp.status_code == 403  # operators run the instance; they do not authorize calls


# ------------------------------------------------------------------ ownership check


def test_outbound_ownership_check_flow_and_required_before_first_callback(cb: CbEnv) -> None:
    from sqlalchemy import delete

    from callshield.db.models.callback_ext import LineOutboundOwnershipCheck

    with cb.session() as s:
        s.execute(delete(LineOutboundOwnershipCheck))
        s.commit()
    call_id = cb.add_inbound()
    assert cb.get(f"/v1/calls/{call_id}/callback-eligibility").json()["caller_id"]["reason"] == (
        "OWNERSHIP_CHECK_MISSING"
    )
    path = f"/v1/lines/{cb.line_id}/outbound-ownership-check"
    assert cb.post(path, {}).json() == {"status": "CODE_SENT"}
    channel = cb.runtime.channel()
    code = channel.last_code(cb.line_id)  # type: ignore[attr-defined]
    assert code and len(code) == 6
    wrong = cb.post(path, {"code": "000000" if code != "000000" else "111111"})
    assert wrong.status_code == 400
    assert cb.post(path, {"code": code}).json() == {"status": "OWNERSHIP_CONFIRMED"}
    assert cb.get(f"/v1/calls/{call_id}/callback-eligibility").json()["allowed"] is True
    with cb.session() as s:  # only a hash is stored
        text = dump_db_text(cb.engine)
        assert code not in text and s.execute(select(ProtectedLine)).first()


def test_ownership_check_codes_expire_and_lock_after_five_wrong_tries(cb: CbEnv) -> None:
    path = f"/v1/lines/{cb.line_id}/outbound-ownership-check"
    cb.post(path, {})
    for _ in range(5):
        assert cb.post(path, {"code": "999999"}).status_code in (400, 429)
    locked = cb.post(path, {"code": "123456"})
    assert locked.status_code == 400 and locked.json()["error"]["code"] in {
        "CHALLENGE_LOCKED",
        "CHALLENGE_FAILED",
    }
    cb.post(path, {})
    code = cb.runtime.channel().last_code(cb.line_id)  # type: ignore[attr-defined]
    cb.clock.advance(minutes=11)
    expired = cb.post(path, {"code": code})
    assert expired.json()["error"]["code"] == "CHALLENGE_EXPIRED"


# ------------------------------------------------------------------ tenancy


def test_cross_tenant_denial_for_every_endpoint(cb: CbEnv) -> None:
    call_id = cb.add_inbound(with_incident=True)
    queued = cb.queue(call_id).json()
    cb.run_jobs()
    stranger = second_env(cb, cb.spare)
    h = {**stranger.user.headers, "Idempotency-Key": "x-1"}
    paths = [
        ("get", f"/v1/calls/{call_id}/callback-eligibility", None),
        ("post", f"/v1/calls/{call_id}/callbacks", {}),
        ("post", f"/v1/callbacks/{queued['id']}/cancel", {}),
        ("get", f"/v1/callbacks/{queued['id']}", None),
        ("post", f"/v1/lines/{cb.line_id}/outbound-ownership-check", {}),
        ("post", f"/v1/lines/{cb.line_id}/outbound-ownership-check", {"code": "123456"}),
    ]
    for method, path, body in paths:
        resp = stranger.client.request(method.upper(), path, json=body, headers=h)
        assert resp.status_code == 404, (method, path, resp.status_code)
        assert str(call_id) not in resp.text.replace(path, "")
    assert stranger.get("/v1/callbacks").json()["items"] == []
    assert stranger.get("/v1/callback/blocklist").json()["items"] == []
    assert cb.get(f"/v1/callbacks/{queued['id']}").status_code == 200  # owner unaffected
    link = stranger.queue(stranger.add_inbound(), incident_id=str(uuid.uuid4()))
    assert link.status_code == 404  # an incident id from nowhere
    foreign_case = stranger.queue(stranger.add_inbound(), key="k-case", case_id=str(uuid.uuid4()))
    assert foreign_case.status_code == 404


def test_other_users_in_the_same_tenant_cannot_see_or_cancel_my_callbacks(cb: CbEnv) -> None:
    queued = cb.queue(cb.add_inbound()).json()
    op = cb.client.get(f"/v1/callbacks/{queued['id']}", headers=cb.operator_headers)
    assert op.status_code == 404
    assert cb.client.get("/v1/callbacks", headers=cb.operator_headers).json()["items"] == []


def test_unauthenticated_requests_are_rejected(cb: CbEnv) -> None:
    for method, path in (
        ("get", "/v1/callbacks"),
        ("get", "/v1/callback/settings"),
        ("get", "/v1/callback/blocklist"),
        ("get", "/v1/callback/capabilities"),
        ("post", f"/v1/calls/{uuid.uuid4()}/callbacks"),
    ):
        assert cb.client.request(method.upper(), path).status_code == 401


# ------------------------------------------------------------------ results, labels, ledger


def test_api_labels_claimed_fields_and_observed_metadata(cb: CbEnv) -> None:
    queued = cb.queue(cb.add_inbound(with_incident=True)).json()
    cb.run_jobs()
    detail = cb.get(f"/v1/callbacks/{queued['id']}").json()
    fields, meta = detail["result"]["fields"], detail["result"]["metadata"]
    assert set(fields) == {
        "rep_name",
        "company_claimed",
        "greeting_type",
        "represents_seller",
        "offer_type",
        "transfer_target",
        "numbers_mentioned",
        "websites_mentioned",
        "hung_up_after_disclosure",
        "wrong_party",
        "stop_requested",
    }
    for name, item in fields.items():
        expected = "OBSERVED" if name == "hung_up_after_disclosure" else "CLAIMED"
        assert item["label"] == expected, name
    assert fields["company_claimed"]["value"] == "Acme Solar"
    assert fields["websites_mentioned"]["value"] == ["acmesolar.com"]
    assert {
        "call_started_at",
        "duration_seconds",
        "attestation_level",
        "line_type",
        "outcome",
    } <= set(meta)
    assert all(item["label"] == "OBSERVED" for item in meta.values())
    assert meta["attestation_level"]["value"] == "A"
    listing = cb.get("/v1/callbacks").json()["items"]
    assert listing[0]["id"] == queued["id"] and listing[0]["result"]["outcome"] == "COMPLETED"


def test_completion_event_for_the_casebuilder_carries_incident_and_case_ids(cb: CbEnv) -> None:
    with cb.session() as s:
        case = Case(tenant_id=cb.user.tenant_id, protected_line_id=cb.line_id, title="Solar calls")
        s.add(case)
        s.commit()
        case_id = case.id
    call_id = cb.add_inbound(with_incident=True)
    queued = cb.queue(call_id, case_id=str(case_id)).json()
    cb.run_jobs()
    with cb.session() as s:
        event = s.execute(
            select(LedgerEvent).where(LedgerEvent.topic == "callback.completed")
        ).scalar_one()
    assert event.payload["case_id"] == str(case_id)
    assert event.payload["incident_id"] == queued["incident_id"] is not None
    assert event.payload["fields"]["company_claimed"]["label"] == "CLAIMED"
    assert result_of(cb, queued["id"]).case_id == case_id


def test_no_raw_phone_numbers_in_logs_ledger_audit_or_callback_tables(
    cb: CbEnv, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    cb.queue(cb.add_inbound(with_incident=True))
    cb.run_jobs()
    digits = CALLER_E164[1:]
    assert digits not in caplog.text and CALLER_E164 not in caplog.text
    with cb.session() as s:
        blobs = [json.dumps(e.payload) for e in s.execute(select(LedgerEvent)).scalars()]
        blobs += [json.dumps(a.data) for a in s.execute(select(AuditEvent)).scalars()]
        blobs += [json.dumps(r.gate_report) for r in s.execute(select(CallbackRequest)).scalars()]
    assert not [b for b in blobs if digits in b]
    from callshield.db.base import Base

    callback_tables = [t.name for t in Base.metadata.sorted_tables if "callback" in t.name]
    assert "callback_global_attempts" in callback_tables
    with cb.engine.connect() as conn:
        for name in callback_tables:
            table = Base.metadata.tables[name]
            assert digits not in repr([tuple(r) for r in conn.execute(select(table))]), name


def test_global_tables_hold_only_hmac_keys_and_times(cb: CbEnv) -> None:
    from callshield.db.models.callback_ext import CallbackGlobalBlocklist

    attempt_cols = {c.name for c in CallbackGlobalAttempt.__table__.columns}
    block_cols = {c.name for c in CallbackGlobalBlocklist.__table__.columns}
    assert attempt_cols == {"id", "destination_key", "attempted_at"}
    assert block_cols == {"destination_key", "reason", "created_at"}
    cb.queue(cb.add_inbound())
    cb.run_jobs()
    with cb.session() as s:
        row = s.execute(select(CallbackGlobalAttempt)).scalar_one()
    assert (
        row.destination_key == cb.runtime.keyer.key(CALLER_E164) and len(row.destination_key) == 64
    )


def test_distress_hard_stop_blocklists_with_its_own_reason(cb: CbEnv) -> None:
    scenario = CallbackScenario(
        opening=[ev(ANSWERED, 0.5), say("Hello?", 2.0, 0.8)],
        on_speak=[[say("I'll call the police if you phone again", 8.0, 2.0)]],
    )
    cb.scenario(scenario)
    cb.queue(cb.add_inbound())
    cb.run_jobs()
    with cb.session() as s:
        reasons = [b.reason for b in s.execute(select(CallbackBlocklist)).scalars()]
    assert reasons == ["THREAT_OR_DISTRESS"]


def test_failed_placement_is_recorded_not_retried(cb: CbEnv) -> None:
    from callshield.callback.dialer_port import DisabledCallbackDialer

    cb.queue(cb.add_inbound())
    cb.runtime.dialer = CallbackDialerStub()
    cb.run_jobs()
    assert get_runtime().dialer is not None
    with cb.session() as s:
        row = s.execute(select(CallbackRequest)).scalar_one()
        assert row.status in {"FAILED", "BLOCKED"}
    assert DisabledCallbackDialer.configured is False


class CallbackDialerStub:
    """Configured dialer whose placement fails (carrier refused)."""

    kind = "stub"
    configured = True

    def place_call(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("legacy dial path must not be used")

    def place_callback(self, *args: Any, **kwargs: Any) -> Any:
        from callshield.core.errors import PolicyBlockedError

        raise PolicyBlockedError("carrier refused", code="CARRIER_REFUSED")


# ------------------------------------------------------------------ retention / legal hold


def media_scenario_run(cb: CbEnv, **body: Any) -> str:
    queued = cb.queue(
        cb.add_inbound(), outbound_recording=True, recording_notice_confirmed=True, **body
    )
    assert queued.status_code == 202, queued.text
    cb.run_jobs()
    return str(queued.json()["id"])


def test_expire_sweep_deletes_media_after_30_days_and_tombstones_fields_after_90(
    cb: CbEnv,
) -> None:
    request_id = media_scenario_run(cb)
    with cb.session() as s:
        keys = [m.storage_key for m in s.execute(select(CallbackMedia)).scalars()]
    assert len(keys) == 2 and all((cb.tmp_path / "media" / k).exists() for k in keys)
    load_handler_modules()
    from callshield.db.session import make_session_factory

    factory = make_session_factory(cb.engine)
    cb.clock.advance(days=29)
    with cb.session() as s:
        enqueue(s, cb.user.tenant_id, "callback.expire_sweep", {}, idempotency_key="sweep-1")
        s.commit()
    assert run_one(factory, ["callback.expire_sweep"])
    assert all((cb.tmp_path / "media" / k).exists() for k in keys)  # not yet
    cb.clock.advance(days=2)  # day 31
    with cb.session() as s:
        enqueue(s, cb.user.tenant_id, "callback.expire_sweep", {}, idempotency_key="sweep-2")
        s.commit()
    assert run_one(factory, ["callback.expire_sweep"])
    assert not any((cb.tmp_path / "media" / k).exists() for k in keys)
    with cb.session() as s:
        assert all(m.deleted_at is not None for m in s.execute(select(CallbackMedia)).scalars())
    assert result_of(cb, request_id).tombstoned_at is None  # fields live to day 90
    assert cb.get(f"/v1/callbacks/{request_id}").json()["result"]["fields"]["company_claimed"]
    cb.clock.advance(days=60)  # day 91
    with cb.session() as s:
        sweep(s, cb.runtime.media_store(), cb.user.tenant_id, cb.clock())
        s.commit()
    done = cb.get(f"/v1/callbacks/{request_id}").json()
    assert done["result"]["tombstoned"] is True and done["result"]["fields"] == {}
    assert done["status"] == "COMPLETED"  # the row itself remains as a tombstone


def test_user_opened_case_is_a_legal_hold_until_closed(cb: CbEnv) -> None:
    with cb.session() as s:
        case = Case(tenant_id=cb.user.tenant_id, protected_line_id=cb.line_id, title="Hold me")
        s.add(case)
        s.commit()
        case_id = case.id
    request_id = media_scenario_run(cb, case_id=str(case_id))
    cb.clock.advance(days=120)
    with cb.session() as s:
        report = sweep(s, cb.runtime.media_store(), cb.user.tenant_id, cb.clock())
        s.commit()
    assert report.media_deleted == 0 and report.results_tombstoned == 0 and report.held == 3
    with cb.session() as s:
        keys = [m.storage_key for m in s.execute(select(CallbackMedia)).scalars()]
    assert all((cb.tmp_path / "media" / k).exists() for k in keys)
    with cb.session() as s:
        s.execute(select(Case)).scalar_one().state = "CLOSED"
        s.commit()
    with cb.session() as s:
        report = sweep(s, cb.runtime.media_store(), cb.user.tenant_id, cb.clock())
        s.commit()
    assert report.media_deleted == 2 and report.results_tombstoned == 1
    assert result_of(cb, request_id).fields == {}


def test_sweep_fails_stale_dialing_requests_and_purges_old_global_counters(cb: CbEnv) -> None:
    request_id = bypass_queue(cb, cb.add_inbound())
    with cb.session() as s:
        row = s.execute(select(CallbackRequest)).scalar_one()
        row.status = "DIALING"
        row.open_slot = str(row.inbound_call_id)
        s.add(CallbackGlobalAttempt(destination_key="k" * 64, attempted_at=cb.clock()))
        s.commit()
    cb.clock.advance(days=31)
    with cb.session() as s:
        report = sweep(s, cb.runtime.media_store(), cb.user.tenant_id, cb.clock())
        s.commit()
    assert report.stale_requests_failed == 1 and report.global_attempts_purged == 1
    assert status_of(cb, request_id) == "FAILED"
    with cb.session() as s:
        assert s.execute(select(CallbackResult)).first() is None
