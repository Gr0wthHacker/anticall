"""RESOLVE acceptance tests (PRD 5.2, 15.1): controlled IVR flow end to end."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from callshield.core.config import Settings
from callshield.db.models import LedgerEvent
from callshield.db.session import make_session_factory

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


def _topics(env: Env) -> list[str]:
    with make_session_factory(env.engine)() as s:
        rows = s.execute(
            select(LedgerEvent.topic).where(LedgerEvent.tenant_id == env.user.tenant_id)
        ).scalars()
        return list(rows)


def _run(env: Env, mode: str = "AI") -> tuple[str, str, dict]:  # type: ignore[type-arg]
    case_id, did = ready(env)
    resp = authorize_and_start(env, case_id, did, mode=mode)
    assert resp.status_code == 200, resp.text
    return case_id, did, resp.json()


def test_ivr_option_2_reaches_agent_request_delivered_and_claimed_ack(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    enable_ai(env)
    case_id, _, body = _run(env)
    assert body["outcome"] == "ACKNOWLEDGED"
    assert body["mode"] == "AI"
    assert body["case_state"] == "ACKNOWLEDGED"
    handle = env.dialer.handles[0]
    assert handle.dtmf_sent == ["2"]
    assert "automated assistant" in handle.spoken[0]
    detail = env.get(f"{BASE}/{case_id}/attempts/{body['attempt_id']}").json()
    texts = [(t["speaker"], t["text"]) for t in detail["transcript"]]
    assert ("COUNTERPARTY", "I've recorded your request.") in texts
    assert ("COUNTERPARTY", "Compliance desk, how can I help you?") in texts
    assert detail["attempt"]["ivr_route"][0]["digit"] == "2"
    claims = detail["attempt"]["agent_claims"]
    assert any(c["type"] == "ACKNOWLEDGEMENT_CLAIM" and c["label"] == "CLAIMED" for c in claims)
    topics = _topics(env)
    for expected in (
        "contact.authorized",
        "contact.started",
        "ivr.option.selected",
        "request.delivered",
        "request.acknowledged",
        "contact.ended",
        "case.created",
        "destination.verified",
    ):
        assert expected in topics, expected


def test_attempt_records_timeline_fields(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    enable_ai(env)
    case_id, _, body = _run(env)
    att = env.get(f"{BASE}/{case_id}/attempts/{body['attempt_id']}").json()["attempt"]
    assert att["authorization_id"]
    assert att["started_at"] and att["ended_at"]
    assert att["dialed_phone_masked"] == "+1415***0142"
    assert "+14155550142" not in str(att)
    assert att["policy_state"]["policy_version"] and att["policy_state"]["template_version"]
    assert att["evidence_label"] == "OBSERVED" and att["source"] == "SYSTEM"
    kinds = [e["kind"] for e in env.get(f"{BASE}/{case_id}/timeline").json()]
    for kind in ("case.created", "destination.verified", "contact.authorized", "contact.started"):
        assert kind in kinds


def test_acknowledged_is_never_suppression(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    enable_ai(env)
    case_id, _, _ = _run(env)
    case = env.get(f"{BASE}/{case_id}").json()
    statuses = [(o["status"], o["evidence_label"]) for o in case["opt_out_events"]]
    assert ("ACKNOWLEDGED", "CLAIMED") in statuses
    assert all(s != "CESSATION_OBSERVED" for s, _ in statuses)
    assert "SUPPRESSED" not in str(case["status"])
    assert {s["kind"] for s in case["next_steps"]} == {"KEEP_MONITORING"}


def test_no_confirmation_stays_unconfirmed(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user, "agent_silent_after_request")
    enable_ai(env)
    case_id, _, body = _run(env)
    assert body["outcome"] == "REQUEST_DELIVERED"
    assert body["case_state"] == "UNCONFIRMED"
    case = env.get(f"{BASE}/{case_id}").json()
    assert case["opt_out_events"][-1]["status"] == "UNCONFIRMED"
    kinds = {s["kind"] for s in case["next_steps"]}
    assert "VERIFIED_COMPLIANCE_EMAIL" in kinds
    call_steps = [s for s in case["next_steps"] if s["involves_call"]]
    assert call_steps and all(s["requires_new_authorization"] for s in call_steps)


def test_refusal_marks_refused(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user, "agent_refuses")
    enable_ai(env)
    _, _, body = _run(env)
    assert (body["outcome"], body["case_state"]) == ("REFUSED", "REFUSED")


def test_call_back_and_channel_referral(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user, "agent_call_back")
    enable_ai(env)
    _, _, body = _run(env)
    assert body["outcome"] == "UNRESOLVED"
    assert body["case_state"] == "UNCONFIRMED"
    assert "compliance channel" in env.dialer.handles[0].spoken[1]
    env2 = _env(client, engine, settings, make_tenant_user, "agent_channel_referral")
    enable_ai(env2)
    assert _run(env2)[2]["outcome"] == "REFERRED_TO_CHANNEL"


def test_hangup_and_drop_never_infer_success(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user, "call_drops")
    enable_ai(env)
    _, _, body = _run(env)
    assert body["outcome"] == "DROPPED"
    assert body["case_state"] == "INTERRUPTED"
    env2 = _env(client, engine, settings, make_tenant_user, "agent_hangs_up_mid_request")
    enable_ai(env2)
    c2, _, b2 = _run(env2)
    assert b2["outcome"] == "DROPPED"
    assert env2.get(f"{BASE}/{c2}").json()["opt_out_events"][-1]["status"] == "DRAFTED"
    env3 = _env(client, engine, settings, make_tenant_user, "agent_hangs_up")
    enable_ai(env3)
    assert _run(env3)[2]["case_state"] == "UNCONFIRMED"


def test_no_answer_busy_ivr_drift_loop_and_stream_loss(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    expected = {
        "no_answer": "NO_ANSWER",
        "busy": "NO_ANSWER",
        "ivr_drift": "IVR_FAILED",
        "ivr_loop": "IVR_FAILED",
        "stream_loss_before_agent": "DROPPED",
    }
    for scenario, outcome in expected.items():
        env = _env(client, engine, settings, make_tenant_user, scenario)
        enable_ai(env)
        _, _, body = _run(env)
        assert body["outcome"] == outcome, scenario
        assert body["case_state"] == "INTERRUPTED", scenario


def test_ivr_loop_sends_digit_once_only(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user, "ivr_loop")
    _run(env)
    assert env.dialer.handles[0].dtmf_sent == ["2"]  # no second attempt at the same menu


def test_ivr_drift_pauses_and_parks_for_user(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user, "ivr_drift")
    case_id, _, body = _run(env)
    assert env.dialer.handles[0].dtmf_sent == []
    att = env.get(f"{BASE}/{case_id}/attempts/{body['attempt_id']}").json()["attempt"]
    assert att["parked_for_review"] is True
    assert att["policy_state"]["ivr_failure"] == "MENU_DRIFT"


def test_no_approved_path_only_proposes_never_sends_dtmf(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id = create_case(env)
    did = verified_destination(env, case_id)
    body = authorize_and_start(env, case_id, did).json()
    assert env.dialer.handles[0].dtmf_sent == []
    assert body["outcome"] == "IVR_FAILED"
    att = env.get(f"{BASE}/{case_id}/attempts/{body['attempt_id']}").json()["attempt"]
    assert att["policy_state"]["ivr_proposal"]  # accessibility text for the user to approve


def test_ai_off_falls_back_to_dtmf_and_human_handoff(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)  # ai_outbound_enabled stays False
    _, _, body = _run(env)
    handle = env.dialer.handles[0]
    assert handle.dtmf_sent == ["2"]
    assert handle.spoken == []  # AI never spoke
    assert body["mode"] == "HUMAN"
    assert body["outcome"] == "AGENT_CONNECTED"


def test_ai_flag_on_but_no_consent_falls_back(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    enable_ai(env, consent=False)
    case_id, _, body = _run(env)
    assert env.dialer.handles[0].spoken == []
    assert body["mode"] == "HUMAN"
    att = env.get(f"{BASE}/{case_id}/attempts/{body['attempt_id']}").json()["attempt"]
    assert att["policy_state"]["ai_gate"] == "NO_PROCESSING_CONSENT"


def test_consent_mode_metadata_only_disallows_ai(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    enable_ai(env, mode="METADATA_ONLY")
    _, _, body = _run(env)
    assert body["mode"] == "HUMAN"
    assert env.dialer.handles[0].spoken == []


def test_human_mode_requested_never_speaks_even_if_ai_enabled(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    enable_ai(env)
    _, _, body = _run(env, mode="HUMAN")
    assert body["mode"] == "HUMAN"
    assert env.dialer.handles[0].spoken == []


def test_stop_conditions_end_session(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    # scenario -> (stop condition, number of agent utterances: opt-out [+ goodbye])
    expected = {
        "agent_refuses_bot": ("COUNTERPARTY_REFUSES_BOT", 2),
        "agent_asks_terminate": ("COUNTERPARTY_TERMINATE", 2),
        "agent_asks_sensitive": ("SENSITIVE_INFO", 1),
        "agent_conflicting": ("CONFLICTING_INSTRUCTIONS", 1),
        "prompt_injection": ("POLICY_UNCERTAINTY", 1),
    }
    for scenario, (stop_cond, spoken_count) in expected.items():
        env = _env(client, engine, settings, make_tenant_user, scenario)
        enable_ai(env)
        case_id, _, body = _run(env)
        att = env.get(f"{BASE}/{case_id}/attempts/{body['attempt_id']}").json()["attempt"]
        assert att["stop_condition"] == stop_cond, scenario
        assert body["case_state"] == "INTERRUPTED", scenario
        assert env.dialer.calls_placed == 1
        assert len(env.dialer.handles[0].spoken) == spoken_count, scenario
        assert att["parked_for_review"] is (scenario in ("agent_conflicting", "prompt_injection"))
        assert env.dialer.handles[0].hung_up


def test_prompt_injection_does_not_dial_or_impersonate(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user, "prompt_injection")
    enable_ai(env)
    _run(env)
    assert env.dialer.calls_placed == 1
    assert env.dialer.dialed == ["+14155550142"]
    spoken = " ".join(env.dialer.handles[0].spoken).lower()
    assert "attorney" not in spoken and "lawyer" not in spoken
    assert "automated assistant" in spoken


def test_user_stop_revokes_authorization_and_offers_alternatives(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env)
    auth = authorize(env, case_id, did).json()
    resp = env.post(f"{BASE}/{case_id}/stop", {"reason": "changed my mind"})
    assert resp.status_code == 200
    assert resp.json()["revoked_authorizations"] == 1
    assert resp.json()["case_state"] == "VERIFIED_DESTINATION"
    assert "VERIFIED_COMPLIANCE_EMAIL" in resp.json()["non_calling_alternatives"]
    refused = start(env, case_id, did, auth["id"])
    assert refused.status_code == 403
    codes = {r["code"] for r in refused.json()["error"]["details"]["reasons"]}
    assert "AUTHORIZATION_EXPIRED" in codes
    assert env.dialer.calls_placed == 0
