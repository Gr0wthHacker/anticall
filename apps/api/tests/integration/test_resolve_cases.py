"""RESOLVE cases: creation, corrections, manual attempts (R-012), recontact, tenant isolation."""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from callshield.core.config import Settings
from callshield.db.models import CallSession, Incident, LedgerEvent
from callshield.db.session import make_session_factory
from callshield.resolve.next_steps import propose_recontact

from ..conftest import TenantUserFactory
from ..resolve_support import (
    BASE,
    Env,
    authorize,
    authorize_and_start,
    build_env,
    create_case,
    enable_ai,
    ready,
    start,
    verified_destination,
)


def _env(
    client: TestClient,
    engine: Engine,
    settings: Settings,
    make_tenant_user: TenantUserFactory,
    scenario: str = "ivr_agent_ack",
) -> Env:
    return build_env(client, engine, settings, make_tenant_user(), scenario)


def test_create_case_requires_a_source_and_owned_line(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    empty = env.post(BASE, {"line_id": str(env.line_id)})
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "CASE_NEEDS_SOURCE"
    foreign = env.post(BASE, {"line_id": str(uuid.uuid4()), "summary": "x"})
    assert foreign.status_code == 404
    case = env.post(BASE, {"line_id": str(env.line_id), "summary": "spam", "urgency": "HIGH"})
    assert case.status_code == 201
    body = case.json()
    assert body["status"] == "CREATED" and body["owner_user_id"] == str(env.user.user_id)
    assert body["claimed_entity_status"] == "UNVERIFIED"


def test_create_case_from_call_records(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    with make_session_factory(engine)() as s:
        call = CallSession(
            tenant_id=env.user.tenant_id,
            line_id=env.line_id,
            provider="test",
            provider_call_id="c-1",
            direction="INBOUND",
        )
        s.add(call)
        s.commit()
        call_id = str(call.id)
    resp = env.post(BASE, {"line_id": str(env.line_id), "call_session_ids": [call_id]})
    assert resp.status_code == 201
    timeline = env.get(f"{BASE}/{resp.json()['id']}/timeline").json()
    assert timeline[0]["kind"] == "case.created"
    assert timeline[0]["data"]["call_session_ids"] == [call_id]
    bad = env.post(BASE, {"line_id": str(env.line_id), "call_session_ids": [str(uuid.uuid4())]})
    assert bad.status_code == 404


def test_unverified_alleged_company_correction_is_recorded(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id = create_case(env, claimed_entity="Acme Lending")
    fix = env.post(
        f"{BASE}/{case_id}/corrections",
        {"field": "claimed_entity", "new_value": "Acme Loans LLC", "reason": "site shows LLC"},
    )
    assert fix.status_code == 200
    assert fix.json()["claimed_entity"] == "Acme Loans LLC"
    assert fix.json()["claimed_entity_status"] == "UNVERIFIED"
    entry = next(
        e
        for e in env.get(f"{BASE}/{case_id}/timeline").json()
        if e["kind"] == "correction.recorded"
    )
    assert entry["data"]["old"] == "Acme Lending" and entry["data"]["new"] == "Acme Loans LLC"
    bad = env.post(f"{BASE}/{case_id}/corrections", {"field": "owner_user_id", "new_value": "x"})
    assert bad.status_code == 422
    verified_destination(env, case_id)
    detail = env.get(f"{BASE}/{case_id}").json()
    assert detail["claimed_entity_status"] == "SUPPORTED_BY_USER_ATTESTED_EVIDENCE"


def test_eleven_manual_attempts_are_representable_without_cutoff(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id = create_case(env)
    dest = env.post(f"{BASE}/{case_id}/destinations", {"phone": "+14155550142"}).json()
    ids = []
    for i in range(11):
        when = (env.clock() - timedelta(days=30 - i)).isoformat()
        resp = env.post(
            f"{BASE}/{case_id}/attempts/manual",
            {
                "destination_id": dest["id"],
                "outcome": "IVR_FAILED" if i % 2 else "UNRESOLVED",
                "occurred_at": when,
                "duration_seconds": 120,
                "notes": f"attempt {i}",
                "historic": True,
            },
            f"manual-{i}",
        )
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["id"])
    assert len(set(ids)) == 11
    case = env.get(f"{BASE}/{case_id}").json()
    assert len(case["attempts"]) == 11
    for att in case["attempts"]:
        assert att["evidence_label"] == "USER_ATTESTED"
        assert att["source"] == "USER_HISTORIC"
        assert att["authorization_id"] is None
        assert att["agent_claims"][0]["label"] == "USER_ATTESTED"
    detail = env.get(f"{BASE}/{case_id}/attempts/{ids[0]}").json()
    assert detail["transcript"] == []  # no fabricated recording or transcript
    assert env.dialer.calls_placed == 0


def test_manual_attempts_do_not_consume_system_caps_or_cooldown(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env)
    for i in range(12):
        resp = env.post(
            f"{BASE}/{case_id}/attempts/manual",
            {"destination_id": did, "outcome": "NO_ANSWER"},
            f"m-{i}",
        )
        assert resp.status_code == 201
    assert authorize_and_start(env, case_id, did).status_code == 200  # system attempt unaffected


def test_manual_attempt_validation_and_idempotency(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id = create_case(env)
    dest = env.post(f"{BASE}/{case_id}/destinations", {"phone": "+14155550142"}).json()
    body = {"destination_id": dest["id"], "outcome": "REFUSED", "notes": "said no"}
    a = env.post(f"{BASE}/{case_id}/attempts/manual", body, "same")
    b = env.post(f"{BASE}/{case_id}/attempts/manual", body, "same")
    assert a.json()["id"] == b.json()["id"]
    assert len(env.get(f"{BASE}/{case_id}").json()["attempts"]) == 1
    assert env.post(f"{BASE}/{case_id}/attempts/manual", body).status_code == 422  # no key
    future = (env.clock() + timedelta(days=2)).isoformat()
    bad_time = env.post(
        f"{BASE}/{case_id}/attempts/manual", {**body, "occurred_at": future}, "future"
    )
    assert bad_time.status_code == 422
    resolved = env.post(
        f"{BASE}/{case_id}/attempts/manual", {**body, "outcome": "VERIFIED_RESOLVED"}, "res"
    )
    assert resolved.status_code == 422
    other = env.post(
        f"{BASE}/{case_id}/attempts/manual", {**body, "destination_id": str(uuid.uuid4())}, "x"
    )
    assert other.status_code == 404


def test_review_attempt_appends_without_overwriting_outcome(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user, "agent_silent_after_request")
    enable_ai(env)
    case_id, did = ready(env)
    attempt_id = authorize_and_start(env, case_id, did).json()["attempt_id"]
    bad = env.post(f"{BASE}/{case_id}/attempts/{attempt_id}/review", {"review_state": "CORRECTED"})
    assert bad.status_code == 409
    ok = env.post(
        f"{BASE}/{case_id}/attempts/{attempt_id}/review",
        {"review_state": "CORRECTED", "corrected_outcome": "DROPPED", "notes": "line was bad"},
    )
    assert ok.status_code == 200
    assert ok.json()["outcome"] == "REQUEST_DELIVERED"  # original kept
    assert ok.json()["review_state"] == "CORRECTED"
    kinds = [e["kind"] for e in env.get(f"{BASE}/{case_id}/timeline").json()]
    assert "attempt.reviewed" in kinds


def test_monitoring_recontact_requires_attribution_review_no_auto_reopen(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    enable_ai(env)
    case_id, did = ready(env)
    assert authorize_and_start(env, case_id, did).json()["case_state"] == "ACKNOWLEDGED"
    mv = env.post(f"{BASE}/{case_id}/state", {"target": "MONITORING"})
    assert mv.status_code == 200 and mv.json()["status"] == "MONITORING"
    assert env.post(f"{BASE}/{case_id}/state", {"target": "REOPENED"}).status_code == 422
    with make_session_factory(engine)() as s:
        inc = Incident(tenant_id=env.user.tenant_id, line_id=env.line_id)
        s.add(inc)
        s.commit()
        incident_id = str(inc.id)
    prop = env.post(f"{BASE}/{case_id}/recontacts", {"incident_id": incident_id})
    assert prop.status_code == 201
    assert env.get(f"{BASE}/{case_id}").json()["status"] == "ATTRIBUTION_REVIEW"
    again = env.post(f"{BASE}/{case_id}/recontacts", {"incident_id": incident_id})
    assert again.json()["id"] == prop.json()["id"]  # idempotent
    decision = env.post(
        f"{BASE}/{case_id}/recontacts/{prop.json()['id']}/decision", {"related": True}
    )
    assert decision.status_code == 200 and decision.json()["case_state"] == "REOPENED"
    with make_session_factory(engine)() as s:
        topics = list(
            s.execute(
                select(LedgerEvent.topic).where(LedgerEvent.tenant_id == env.user.tenant_id)
            ).scalars()
        )
    assert "case.reopened" in topics
    dup = env.post(f"{BASE}/{case_id}/recontacts/{prop.json()['id']}/decision", {"related": False})
    assert dup.status_code == 409


def test_recontact_not_related_and_unknown_incident(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    enable_ai(env)
    case_id, did = ready(env)
    authorize_and_start(env, case_id, did)
    env.post(f"{BASE}/{case_id}/state", {"target": "MONITORING"})
    assert (
        env.post(f"{BASE}/{case_id}/recontacts", {"incident_id": str(uuid.uuid4())}).status_code
        == 404
    )
    with make_session_factory(engine)() as s:
        inc = Incident(tenant_id=env.user.tenant_id, line_id=env.line_id)
        s.add(inc)
        s.commit()
        incident_id = inc.id
        session: Session = s
        proposal = propose_recontact(session, env.user.tenant_id, uuid.UUID(case_id), incident_id)
        s.commit()
        pid = str(proposal.id)
    res = env.post(f"{BASE}/{case_id}/recontacts/{pid}/decision", {"related": False})
    assert res.json()["case_state"] == "NOT_RELATED"


def test_cross_tenant_denial_for_every_endpoint(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    enable_ai(env)
    case_id, did = ready(env)
    attempt_id = authorize_and_start(env, case_id, did).json()["attempt_id"]
    intruder = make_tenant_user("Intruder")
    h = intruder.headers
    base = f"{BASE}/{case_id}"
    fake = str(uuid.uuid4())
    calls = [
        ("get", base, None),
        ("get", f"{base}/timeline", None),
        ("get", f"{base}/next-steps", None),
        ("get", f"{base}/attempts/{attempt_id}", None),
        ("get", f"{base}/destinations/{did}/ivr-paths", None),
        ("post", f"{base}/destinations", {"phone": "+14155550142"}),
        (
            "post",
            f"{base}/destinations/{did}/verify",
            {"evidence_kind": "OFFICIAL_WEBSITE", "reference": "x"},
        ),
        ("post", f"{base}/destinations/{did}/ivr-paths", {"steps": [{"digit": "2"}]}),
        ("post", f"{base}/contact-authorizations", {"destination_id": did}),
        ("post", f"{base}/start-contact", {"destination_id": did}),
        ("post", f"{base}/attempts/manual", {"destination_id": did, "outcome": "NO_ANSWER"}),
        ("post", f"{base}/attempts/{attempt_id}/review", {"review_state": "CONFIRMED"}),
        ("post", f"{base}/stop", {}),
        ("post", f"{base}/corrections", {"field": "summary", "new_value": "x"}),
        ("post", f"{base}/state", {"target": "CLOSED"}),
        ("post", f"{base}/recontacts", {"incident_id": fake}),
        ("post", f"{base}/recontacts/{fake}/decision", {"related": True}),
    ]
    for method, path, body in calls:
        headers = {**h, "Idempotency-Key": "k-1"}
        resp = (
            client.get(path, headers=headers)
            if method == "get"
            else client.post(path, json=body, headers=headers)
        )
        assert resp.status_code == 404, (method, path, resp.status_code, resp.text)
        assert case_id not in resp.text.replace(path, "")
    listing = client.get(BASE, headers=h).json()
    assert listing == []
    # other tenant's line / destination ids supplied by the intruder
    b_env = Env(client, engine, settings, intruder, env.dialer, uuid.uuid4(), env.clock)
    assert b_env.post(BASE, {"line_id": str(env.line_id), "summary": "x"}).status_code == 404
    # the original owner's data is untouched
    assert env.get(f"{BASE}/{case_id}").status_code == 200


def test_cross_tenant_destination_in_body_is_unusable(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env_a = _env(client, engine, settings, make_tenant_user)
    _, did_a = ready(env_a)
    env_b = _env(client, engine, settings, make_tenant_user)
    case_b = create_case(env_b)
    manual = env_b.post(
        f"{BASE}/{case_b}/attempts/manual", {"destination_id": did_a, "outcome": "NO_ANSWER"}, "k"
    )
    assert manual.status_code == 404
    auth = authorize(env_b, case_b, did_a)
    assert auth.status_code == 403
    assert start(env_b, case_b, did_a, None).status_code == 403
    assert env_b.dialer.calls_placed == 0


def test_unauthenticated_requests_rejected(client: TestClient) -> None:
    assert client.get(BASE).status_code == 401
    assert client.post(f"{BASE}/{uuid.uuid4()}/start-contact", json={}).status_code == 401
