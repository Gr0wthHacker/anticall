# ruff: noqa: F811
"""Webhook ingestion: auth, replay, tenancy, idempotency, lifecycle, health, log hygiene."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from callshield.core.clock import FrozenClock, set_clock
from callshield.db.models.defend import (
    CallerObservation,
    CallSession,
    ConsentRecord,
    Incident,
    ProtectedLine,
    RoutingRule,
    TrustedContact,
)
from callshield.db.models.defend_ext import CallRouting, TelephonyHealth, WebhookReceipt
from callshield.db.models.platform import AuditEvent, Job, LedgerEvent
from callshield.db.session import make_session_factory
from callshield.defend.notify import Notification, add_notification_hook
from callshield.defend.verification import InMemoryOutboxChannel
from callshield.telephony.mock import (
    FICTIONAL_CALLER,
    MockProvider,
    ari_stasis_start,
    generic_event,
    signed_request,
)
from callshield.telephony.registry import get_adapter
from tests.conftest import TenantUser, TenantUserFactory
from tests.defend_support import (
    FICTIONAL_LINE,
    FICTIONAL_USER,
    LineHandle,
    defend_reset,  # noqa: F401
    enroll_line,
    error_code,
    inbound_call_payload,
    post_webhook,
    response_ops,
    rows,
)

pytestmark = pytest.mark.usefixtures("defend_reset")

TRUSTED = "+14155550111"
UNWANTED = "+14155550122"


@pytest.fixture
def owner(make_tenant_user: TenantUserFactory) -> TenantUser:
    return make_tenant_user("Owner")


@pytest.fixture
def line(client: TestClient, owner: TenantUser, defend_reset: InMemoryOutboxChannel) -> LineHandle:
    return enroll_line(client, owner, defend_reset)


def add_contact(client: TestClient, line: LineHandle, e164: str, kind: str = "TRUSTED") -> None:
    resp = client.post(
        "/v1/contacts",
        headers=line.user.headers,
        json={"line_id": str(line.id), "e164": e164, "kind": kind},
    )
    assert resp.status_code == 201, resp.text


# ------------------------------------------------------------------ routing outcomes


def test_trusted_caller_reaches_user(client: TestClient, line: LineHandle) -> None:
    add_contact(client, line, TRUSTED)
    resp = post_webhook(client, line, inbound_call_payload("t1", caller=TRUSTED))
    assert resp.status_code == 200, resp.text
    instruction = resp.json()["instructions"][0]
    assert instruction["op"] == "transfer" and instruction["target"] == FICTIONAL_USER


def test_unknown_caller_without_screening_goes_to_voicemail(
    client: TestClient, line: LineHandle
) -> None:
    resp = post_webhook(client, line, inbound_call_payload("u1"))
    assert response_ops(resp) == ["voicemail"]


def test_known_unwanted_does_not_reach_user(client: TestClient, line: LineHandle) -> None:
    add_contact(client, line, UNWANTED, "KNOWN_UNWANTED")
    assert response_ops(
        post_webhook(client, line, inbound_call_payload("k1", caller=UNWANTED))
    ) == ["voicemail"]


def test_mock_provider_records_the_routing_action(client: TestClient, line: LineHandle) -> None:
    add_contact(client, line, TRUSTED)
    post_webhook(client, line, inbound_call_payload("m1", caller=TRUSTED))
    mock = get_adapter("mock")
    assert isinstance(mock, MockProvider)
    assert [(a.op, a.call_id) for a in mock.actions] == [("transfer", "m1")]


def test_rule_priority_applies_through_the_api(client: TestClient, line: LineHandle) -> None:
    body = {"line_id": str(line.id), "matcher": {"caller_prefix": ["+1415"]}, "action": "REJECT"}
    assert (
        client.post(
            "/v1/rules", headers=line.user.headers, json={**body, "priority": 50}
        ).status_code
        == 201
    )
    allow = {
        **body,
        "matcher": {"caller_exact": [FICTIONAL_CALLER]},
        "action": "TRANSFER",
        "priority": 1,
    }
    assert client.post("/v1/rules", headers=line.user.headers, json=allow).status_code == 201
    assert response_ops(post_webhook(client, line, inbound_call_payload("r1"))) == ["transfer"]
    other = inbound_call_payload("r2", caller="+14155550166")
    assert response_ops(post_webhook(client, line, other)) == ["hangup"]


def test_disabled_rule_no_longer_applies(client: TestClient, line: LineHandle) -> None:
    created = client.post(
        "/v1/rules",
        headers=line.user.headers,
        json={"line_id": str(line.id), "matcher": {"anonymous": True}, "action": "REJECT"},
    ).json()
    assert response_ops(post_webhook(client, line, inbound_call_payload("d1", caller=None))) == [
        "hangup"
    ]
    assert (
        client.delete(f"/v1/rules/{created['id']}", headers=line.user.headers).json()["enabled"]
        is False
    )
    assert response_ops(post_webhook(client, line, inbound_call_payload("d2", caller=None))) == [
        "voicemail"
    ]


def test_call_loop_is_prevented_via_diversion_header_fields(
    client: TestClient, line: LineHandle
) -> None:
    add_contact(client, line, TRUSTED)
    looped = inbound_call_payload("l1", caller=TRUSTED, diversions=[FICTIONAL_USER])
    assert response_ops(post_webhook(client, line, looped)) == ["voicemail"]
    too_many = inbound_call_payload("l2", caller=TRUSTED, hop_count=3)
    assert response_ops(post_webhook(client, line, too_many)) == ["voicemail"]
    fine = inbound_call_payload("l3", caller=TRUSTED, hop_count=1)
    assert response_ops(post_webhook(client, line, fine)) == ["transfer"]


# ------------------------------------------------------------------ persistence + idempotency


def test_first_call_persists_session_observation_incident_ledger_and_job(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    payload = inbound_call_payload(
        "p1", attestation={"attest": "A", "verstat": "TN-Validation-Passed"}
    )
    resp = post_webhook(client, line, payload)
    body = resp.json()
    call = rows(engine, CallSession, provider_call_id="p1")[0]
    assert str(call.id) == body["call_id"] and call.direction == "INBOUND"
    assert call.tenant_id == line.user.tenant_id and call.line_id == line.id
    obs = rows(engine, CallerObservation, session_id=call.id)[0]
    assert obs.displayed_caller_id == FICTIONAL_CALLER and obs.label == "OBSERVED"
    meta = obs.attestation_metadata
    assert meta["network_attestation"]["attest"] == "A" and meta["is_verdict"] is False
    assert meta["attestation_level"] == "A"
    routing = rows(engine, CallRouting)[0]
    assert routing.to_e164 == FICTIONAL_LINE and routing.from_displayed == FICTIONAL_CALLER
    assert call.started_at is not None
    incident = rows(engine, Incident, call_session_id=call.id)[0]
    assert str(incident.id) == body["incident_id"] and incident.status == "CANDIDATE"
    assert incident.evidence_label == "OBSERVED"
    events = rows(engine, LedgerEvent, topic="call.received")
    assert len(events) == 1 and events[0].payload["incident_id"] == str(incident.id)
    assert "caller" not in json.dumps(events[0].payload).replace("caller_masked", "")
    jobs = rows(engine, Job, kind="incident.created")
    assert len(jobs) == 1
    assert jobs[0].payload == {"incident_id": str(incident.id)}
    assert jobs[0].idempotency_key == f"incident:{incident.id}"
    assert jobs[0].state == "QUEUED"


def test_duplicate_delivery_is_idempotent(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    payload = inbound_call_payload("dup1")
    first = post_webhook(client, line, payload)
    second = post_webhook(client, line, payload)  # fresh nonce: provider retry
    third = post_webhook(client, line, {**payload, "event_id": "other-event-id"})
    assert [r.status_code for r in (first, second, third)] == [200, 200, 200]
    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == third.json()["status"] == "duplicate"
    assert second.json()["call_id"] == first.json()["call_id"]
    assert second.json()["incident_id"] == first.json()["incident_id"]
    assert second.json()["instructions"] == first.json()["instructions"]
    assert len(rows(engine, CallSession)) == 1
    assert len(rows(engine, Incident)) == 1
    assert len(rows(engine, CallerObservation)) == 1
    assert len(rows(engine, CallRouting)) == 1
    assert len(rows(engine, LedgerEvent, topic="call.received")) == 1
    assert len(rows(engine, LedgerEvent, topic="call.routed")) == 1
    assert len(rows(engine, Job, kind="incident.created")) == 1


def test_same_call_id_in_two_tenants_creates_two_sessions(
    client: TestClient,
    two_tenants: tuple[TenantUser, TenantUser],
    engine: Engine,
    defend_reset: InMemoryOutboxChannel,
) -> None:
    a, b = two_tenants
    line_a = enroll_line(client, a, defend_reset, "+14155550100")
    line_b = enroll_line(client, b, defend_reset, "+14155550102")
    assert (
        post_webhook(client, line_a, inbound_call_payload("same", to=line_a.e164)).status_code
        == 200
    )
    assert (
        post_webhook(client, line_b, inbound_call_payload("same", to=line_b.e164)).status_code
        == 200
    )
    sessions = rows(engine, CallSession, provider_call_id="same")
    assert {s.tenant_id for s in sessions} == {a.tenant_id, b.tenant_id}


def test_ari_events_flow_end_to_end(
    client: TestClient, owner: TenantUser, engine: Engine, defend_reset: InMemoryOutboxChannel
) -> None:
    ari_line = enroll_line(client, owner, defend_reset, provider="ari")
    payload = ari_stasis_start("ari-chan-1", ari_line.e164, FICTIONAL_CALLER, args=["attest=A"])
    resp = post_webhook(client, ari_line, payload)
    assert resp.status_code == 200, resp.text
    assert response_ops(resp) == ["voicemail"]
    call = rows(engine, CallSession, provider="ari")[0]
    assert call.provider_call_id == "ari-chan-1"
    again = post_webhook(client, ari_line, payload)
    assert again.json()["status"] == "duplicate" and len(rows(engine, CallSession)) == 1


def test_anonymous_caller_is_recorded_and_routed(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    resp = post_webhook(client, line, inbound_call_payload("anon", caller=None))
    assert resp.status_code == 200
    assert rows(engine, CallerObservation)[0].displayed_caller_id is None


# ------------------------------------------------------------------ authentication


def test_forged_signature_rejected_and_nothing_persisted(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    resp = post_webhook(client, line, inbound_call_payload("f1"), secret="attacker-guess")
    assert resp.status_code == 401 and error_code(resp) == "WEBHOOK_REJECTED"
    assert not rows(engine, CallSession) and not rows(engine, Incident)
    assert not rows(engine, WebhookReceipt)


def test_unsigned_request_rejected(client: TestClient, line: LineHandle) -> None:
    body = json.dumps(inbound_call_payload("n1")).encode()
    resp = client.post(f"/v1/webhooks/mock/{line.line_key}", content=body)
    assert resp.status_code == 401


def test_jwt_is_not_a_substitute_for_a_signature(client: TestClient, line: LineHandle) -> None:
    body = json.dumps(inbound_call_payload("j1")).encode()
    resp = client.post(
        f"/v1/webhooks/mock/{line.line_key}", content=body, headers=line.user.headers
    )
    assert resp.status_code == 401


def test_tampered_body_rejected(client: TestClient, line: LineHandle) -> None:
    body, headers = signed_request(line.secret, inbound_call_payload("tb"), "nonce-tamper-1")
    resp = client.post(
        f"/v1/webhooks/mock/{line.line_key}", content=body.replace(b"tb", b"xx"), headers=headers
    )
    assert resp.status_code == 401


def test_stale_and_future_timestamps_rejected(client: TestClient, line: LineHandle) -> None:
    clock = FrozenClock(datetime(2026, 3, 1, 12, 0, tzinfo=UTC))
    set_clock(clock)
    now = int(clock().timestamp())
    for offset in (-3600, 3600):
        resp = post_webhook(client, line, inbound_call_payload("ts"), timestamp=now + offset)
        assert resp.status_code == 401


def test_replayed_request_rejected(client: TestClient, line: LineHandle, engine: Engine) -> None:
    payload = inbound_call_payload("rp1")
    body, headers = signed_request(line.secret, payload, "nonce-replay-0001")
    url = f"/v1/webhooks/mock/{line.line_key}"
    assert client.post(url, content=body, headers=headers).status_code == 200
    replay = client.post(url, content=body, headers=headers)
    assert replay.status_code == 409 and error_code(replay) == "WEBHOOK_REPLAY"
    assert len(rows(engine, CallSession)) == 1
    assert len(rows(engine, WebhookReceipt)) == 1


def test_unknown_line_key_and_provider(client: TestClient, line: LineHandle) -> None:
    assert post_webhook(client, line, inbound_call_payload("x"), line_key="nope").status_code == 401
    assert (
        post_webhook(client, line, inbound_call_payload("x"), provider="mockery").status_code == 404
    )
    # right key, wrong provider path: not a match for this endpoint
    assert (
        post_webhook(client, line, inbound_call_payload("x"), provider="generic").status_code == 401
    )


def test_oversize_body_rejected(client: TestClient, line: LineHandle) -> None:
    big = json.dumps(inbound_call_payload("big", detail="x" * 70_000)).encode()
    assert post_webhook(client, line, big).status_code == 413


@pytest.mark.parametrize(
    "payload",
    [b"not json at all", b"[1,2,3]", b'{"type":"call.received","event_id":"e"}'],
)
def test_malformed_signed_webhook_is_400_and_creates_nothing(
    client: TestClient, line: LineHandle, engine: Engine, payload: bytes
) -> None:
    resp = post_webhook(client, line, payload)
    assert resp.status_code == 400 and error_code(resp) == "MALFORMED_EVENT"
    assert not rows(engine, CallSession) and not rows(engine, Incident) and not rows(engine, Job)


def test_event_without_line_identity_is_rejected(client: TestClient, line: LineHandle) -> None:
    payload = generic_event("call.received", "e-noto", call_id="c", **{"from": FICTIONAL_CALLER})
    assert error_code(post_webhook(client, line, payload)) == "MALFORMED_EVENT"


# ------------------------------------------------------------------ tenant isolation


def test_cross_tenant_webhook_cannot_route(
    client: TestClient,
    two_tenants: tuple[TenantUser, TenantUser],
    engine: Engine,
    defend_reset: InMemoryOutboxChannel,
) -> None:
    """B holds a valid key+secret for B's line but addresses A's number: rejected, A untouched."""
    a, b = two_tenants
    line_a = enroll_line(client, a, defend_reset, "+14155550100")
    line_b = enroll_line(client, b, defend_reset, "+14155550102")
    resp = post_webhook(client, line_b, inbound_call_payload("x1", to=line_a.e164))
    assert resp.status_code == 400 and error_code(resp) == "LINE_MISMATCH"
    assert not rows(engine, CallSession) and not rows(engine, Incident)
    assert not rows(engine, LedgerEvent, topic="call.received")


def test_tenant_comes_from_the_line_not_the_payload(
    client: TestClient,
    two_tenants: tuple[TenantUser, TenantUser],
    engine: Engine,
    defend_reset: InMemoryOutboxChannel,
) -> None:
    a, b = two_tenants
    line_a = enroll_line(client, a, defend_reset, "+14155550100")
    enroll_line(client, b, defend_reset, "+14155550102")
    payload = inbound_call_payload("t-inj", to=line_a.e164, tenant_id=str(b.tenant_id))
    resp = post_webhook(client, line_a, payload)
    assert resp.status_code == 200  # the field is ignored, never trusted
    assert {c.tenant_id for c in rows(engine, CallSession)} == {a.tenant_id}
    assert not rows(engine, CallSession, tenant_id=b.tenant_id)


def test_signature_for_one_line_is_invalid_for_another(
    client: TestClient,
    two_tenants: tuple[TenantUser, TenantUser],
    defend_reset: InMemoryOutboxChannel,
) -> None:
    a, b = two_tenants
    line_a = enroll_line(client, a, defend_reset, "+14155550100")
    line_b = enroll_line(client, b, defend_reset, "+14155550102")
    resp = post_webhook(
        client, line_a, inbound_call_payload("c", to=line_a.e164), secret=line_b.secret
    )
    assert resp.status_code == 401


def test_endpoint_id_mismatch_is_rejected(
    client: TestClient, owner: TenantUser, defend_reset: InMemoryOutboxChannel, engine: Engine
) -> None:
    resp = client.post(
        "/v1/lines",
        headers=owner.headers,
        json={
            "e164": FICTIONAL_LINE,
            "provider": "mock",
            "endpoint_id": "trunk-A",
            "routing_config": {"user_number": FICTIONAL_USER},
        },
    )
    created = resp.json()
    with make_session_factory(engine)() as s:
        ln = s.get(ProtectedLine, uuid.UUID(created["id"]))
        assert ln is not None
        ln.verification_status = "VERIFIED"
        s.commit()
    handle = LineHandle(
        id=ln.id,
        e164=FICTIONAL_LINE,
        line_key=created["webhook"]["line_key"],
        secret=created["webhook"]["secret"],
        provider="mock",
        user=owner,
    )
    bad = inbound_call_payload("e1", endpoint_id="trunk-B")
    assert error_code(post_webhook(client, handle, bad)) == "LINE_MISMATCH"
    good = inbound_call_payload("e2", endpoint_id="trunk-A")
    assert post_webhook(client, handle, good).status_code == 200


def test_unverified_line_is_unroutable(
    client: TestClient, owner: TenantUser, defend_reset: InMemoryOutboxChannel, engine: Engine
) -> None:
    pending = enroll_line(client, owner, defend_reset, verify=False)
    resp = post_webhook(client, pending, inbound_call_payload("uv"))
    assert resp.status_code == 403 and error_code(resp) == "LINE_NOT_VERIFIED"
    assert not rows(engine, CallSession)


# ------------------------------------------------------------------ lifecycle transitions


def test_answered_transferred_ended_emit_call_topics(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    add_contact(client, line, TRUSTED)
    post_webhook(client, line, inbound_call_payload("lc1", caller=TRUSTED))
    for etype, eid in (("call.answered", "a"), ("call.transferred", "t"), ("call.ended", "e")):
        extra = {"duration_seconds": 42} if etype == "call.ended" else {}
        resp = post_webhook(
            client, line, generic_event(etype, f"{eid}-lc1", call_id="lc1", **extra)
        )
        assert resp.status_code == 200 and resp.json()["status"] == "accepted", resp.text
    for topic in ("call.answered", "call.transferred", "call.ended"):
        assert len(rows(engine, LedgerEvent, topic=topic)) == 1
    call = rows(engine, CallSession, provider_call_id="lc1")[0]
    assert call.disposition == "TRANSFERRED" and call.duration_seconds == 42 and call.ended_at


def test_transition_events_are_idempotent(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    post_webhook(client, line, inbound_call_payload("lc2"))
    end = generic_event("call.ended", "end-lc2", call_id="lc2")
    assert post_webhook(client, line, end).json()["status"] == "accepted"
    assert post_webhook(client, line, end).json()["status"] == "duplicate"
    assert len(rows(engine, LedgerEvent, topic="call.ended")) == 1


def test_unanswered_ring_ends_as_no_answer_and_voicemail_stays(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    add_contact(client, line, TRUSTED)
    post_webhook(client, line, inbound_call_payload("ring", caller=TRUSTED))
    post_webhook(client, line, inbound_call_payload("vm"))
    for cid in ("ring", "vm"):
        post_webhook(client, line, generic_event("call.ended", f"end-{cid}", call_id=cid))
    by_id = {c.provider_call_id: c.disposition for c in rows(engine, CallSession)}
    assert by_id == {"ring": "NO_ANSWER", "vm": "VOICEMAIL"}


def test_event_for_unknown_call_is_404(client: TestClient, line: LineHandle) -> None:
    resp = post_webhook(client, line, generic_event("call.ended", "e", call_id="ghost"))
    assert resp.status_code == 404


def test_transition_cannot_touch_another_tenants_call(
    client: TestClient,
    two_tenants: tuple[TenantUser, TenantUser],
    engine: Engine,
    defend_reset: InMemoryOutboxChannel,
) -> None:
    a, b = two_tenants
    line_a = enroll_line(client, a, defend_reset, "+14155550100")
    line_b = enroll_line(client, b, defend_reset, "+14155550102")
    post_webhook(client, line_a, inbound_call_payload("shared", to=line_a.e164))
    resp = post_webhook(client, line_b, generic_event("call.ended", "x", call_id="shared"))
    assert resp.status_code == 404
    assert rows(engine, CallSession)[0].ended_at is None


# ------------------------------------------------------------------ SIP registration + health


def test_registration_failure_records_health_notifies_and_restores_fallback(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    seen: list[Notification] = []
    add_notification_hook(seen.append)
    event = {"event_id": "reg1", "type": "registration.failed", "detail": "SIP 503 from registrar"}
    resp = post_webhook(client, line, event)
    assert resp.status_code == 200 and resp.json()["detail"] == "registration_failed"
    assert [n.kind for n in seen] == ["telephony.registration_failed"]
    assert seen[0].tenant_id == line.user.tenant_id
    mock = get_adapter("mock")
    assert isinstance(mock, MockProvider) and mock.fallbacks_restored == [None]
    assert len(rows(engine, LedgerEvent, topic="call.failed")) == 1
    health = rows(engine, TelephonyHealth)[0]
    assert health.status == "DEGRADED" and "503" in health.detail
    audits = [a.action for a in rows(engine, AuditEvent)]
    assert "telephony.registration_failed" in audits
    # duplicate failure report does not notify twice
    post_webhook(client, line, event)
    assert len(seen) == 1


def test_health_endpoint_reports_degraded_then_recovered(
    client: TestClient, line: LineHandle
) -> None:
    health = client.get("/v1/health/telephony", headers=line.user.headers).json()
    assert health["overall"] == "UNKNOWN" and {a["provider"] for a in health["adapters"]} >= {
        "mock"
    }
    post_webhook(client, line, {"event_id": "r1", "type": "registration.failed", "detail": "503"})
    health = client.get("/v1/health/telephony", headers=line.user.headers).json()
    assert health["overall"] == "DEGRADED" and health["providers"][0]["status"] == "DEGRADED"
    post_webhook(client, line, {"event_id": "r2", "type": "registration.ok"})
    health = client.get("/v1/health/telephony", headers=line.user.headers).json()
    assert health["overall"] == "OK"


def test_health_is_tenant_scoped(
    client: TestClient,
    two_tenants: tuple[TenantUser, TenantUser],
    defend_reset: InMemoryOutboxChannel,
) -> None:
    a, b = two_tenants
    line_a = enroll_line(client, a, defend_reset)
    post_webhook(client, line_a, {"event_id": "r", "type": "registration.failed", "detail": "x"})
    assert client.get("/v1/health/telephony", headers=a.headers).json()["overall"] == "DEGRADED"
    other = client.get("/v1/health/telephony", headers=b.headers).json()
    assert other["overall"] == "UNKNOWN" and other["providers"] == []


def test_health_requires_auth(client: TestClient) -> None:
    assert client.get("/v1/health/telephony").status_code == 401


def test_failing_notification_hook_does_not_break_ingestion(
    client: TestClient, line: LineHandle
) -> None:
    def broken(_: Notification) -> None:
        raise RuntimeError("pager down")

    add_notification_hook(broken)
    resp = post_webhook(client, line, {"event_id": "rb", "type": "registration.failed"})
    assert resp.status_code == 200


# ------------------------------------------------------------------ audit / consent rows / logs


def test_every_call_writes_a_processing_decision_consent_row_and_audit(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    post_webhook(client, line, inbound_call_payload("cd1"))
    call = rows(engine, CallSession)[0]
    decision = rows(engine, ConsentRecord, session_id=call.id)[0]
    assert decision.processing_mode == "METADATA_ONLY" and decision.authorized is False
    assert decision.legal_basis_config["kind"] == "DECISION"
    assert "JURISDICTION_UNKNOWN" in decision.legal_basis_config["reasons"]
    actions = [a.action for a in rows(engine, AuditEvent)]
    assert "processing.decision" in actions and "call.routed" in actions


def test_no_raw_phone_numbers_in_logs(
    client: TestClient, line: LineHandle, caplog: pytest.LogCaptureFixture
) -> None:
    add_contact(client, line, TRUSTED)
    with caplog.at_level(logging.DEBUG):
        post_webhook(client, line, inbound_call_payload("log1", caller=TRUSTED))
        post_webhook(client, line, inbound_call_payload("log2"), secret="wrong")
        post_webhook(client, line, b"garbage")
        post_webhook(client, line, {"event_id": "r", "type": "registration.failed"})
    text = " | ".join(r.getMessage() for r in caplog.records if r.name.startswith("callshield"))
    assert "call received" in text, "expected app log output to assert against"
    assert "+1415***0111" in text
    assert not re.search(r"\+\d{8,}", text)
    for raw in (TRUSTED, FICTIONAL_CALLER, FICTIONAL_LINE, FICTIONAL_USER):
        assert raw not in text
    assert line.secret not in text and line.line_key not in text


def test_secrets_are_never_returned_after_creation(client: TestClient, line: LineHandle) -> None:
    for path in ("/v1/lines", f"/v1/lines/{line.id}"):
        text = client.get(path, headers=line.user.headers).text
        assert line.secret not in text and line.line_key not in text


def test_routing_rules_and_contacts_tables_untouched_by_webhooks(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    post_webhook(client, line, inbound_call_payload("ro1"))
    assert not rows(engine, RoutingRule) and not rows(engine, TrustedContact)


def test_missing_attestation_is_stored_as_none(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    post_webhook(client, line, inbound_call_payload("na1"))
    meta = rows(engine, CallerObservation)[0].attestation_metadata
    assert meta["attestation_level"] == "NONE" and meta["is_verdict"] is False
