"""RESOLVE safety gates: start-contact refusals, cooldown/caps, single-use, idempotency."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from callshield.core.config import Settings
from callshield.db.models import AuditEvent, ContactAttempt, LedgerEvent, User
from callshield.db.models.resolve_ext import ContactAttemptDetail
from callshield.db.session import make_session_factory
from callshield.domain.enums import Role

from ..conftest import TenantUserFactory
from ..resolve_legacy import legacy_start
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
    *,
    outbound: bool = True,
) -> Env:
    return build_env(client, engine, settings, make_tenant_user(), scenario, outbound=outbound)


def _codes(resp) -> set[str]:  # type: ignore[no-untyped-def]
    return {r["code"] for r in resp.json()["error"]["details"]["reasons"]}


def _count(env: Env, model: type) -> int:
    with make_session_factory(env.engine)() as s:
        return len(list(s.execute(select(model).where(model.tenant_id == env.user.tenant_id))))  # type: ignore[attr-defined]


def test_outbound_disabled_by_default_blocks_everything(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    assert Settings().outbound_enabled is False and Settings().ai_outbound_enabled is False
    env = _env(client, engine, settings, make_tenant_user, outbound=False)
    case_id, did = ready(env)
    resp = authorize(env, case_id, did)
    assert resp.status_code == 403 and "OUTBOUND_DISABLED" in _codes(resp)
    resp2 = start(env, case_id, did, None)
    assert resp2.status_code == 403
    assert {"OUTBOUND_DISABLED", "AUTHORIZATION_MISSING"} <= _codes(resp2)
    assert env.dialer.calls_placed == 0


def test_default_dialer_is_disabled_even_when_outbound_on(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    from fastapi import FastAPI

    from callshield.resolve.deps import get_dialer

    env = _env(client, engine, settings, make_tenant_user)
    app = client.app
    assert isinstance(app, FastAPI)
    del app.dependency_overrides[get_dialer]  # back to the real default
    case_id, did = ready(env)
    resp = authorize_and_start(env, case_id, did)
    assert resp.status_code == 403
    assert "DIALER_NOT_CONFIGURED" in _codes(resp)


def test_caller_id_only_destination_can_never_be_authorized_or_dialed(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id = create_case(env)
    dest = env.post(
        f"{BASE}/{case_id}/destinations",
        {"phone": "+14155550177", "provenance": "CALLER_ID_ONLY"},
    ).json()
    assert dest["dialable"] is False
    resp = authorize(env, case_id, dest["id"])
    assert resp.status_code == 403 and "DESTINATION_CALLER_ID_ONLY" in _codes(resp)
    resp2 = start(env, case_id, dest["id"], str(uuid.uuid4()))
    assert resp2.status_code == 403
    assert {"DESTINATION_CALLER_ID_ONLY", "AUTHORIZATION_MISSING"} <= _codes(resp2)
    assert env.dialer.calls_placed == 0


def test_unverified_destination_blocked_and_verified_requires_evidence(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id = create_case(env)
    dest = env.post(f"{BASE}/{case_id}/destinations", {"phone": "+14155550142"}).json()
    resp = authorize(env, case_id, dest["id"])
    assert resp.status_code == 403 and "DESTINATION_UNVERIFIED" in _codes(resp)
    no_evidence = env.post(
        f"{BASE}/{case_id}/destinations/{dest['id']}/verify",
        {"evidence_kind": "OFFICIAL_WEBSITE", "reference": "  "},
    )
    assert no_evidence.status_code == 422
    forged = env.post(
        f"{BASE}/{case_id}/destinations",
        {"phone": "+14155550143", "provenance": "INDEPENDENTLY_VERIFIED"},
    )
    assert forged.status_code == 422  # cannot be created as verified without evidence


def test_verify_appends_new_row_and_old_one_stays_undialable(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id = create_case(env)
    verified = verified_destination(env, case_id)
    dests = env.get(f"{BASE}/{case_id}").json()["destinations"]
    assert len(dests) == 2
    old = next(d for d in dests if d["id"] != verified)
    assert old["verification_status"] == "UNVERIFIED" and old["is_current"] is False
    assert authorize(env, case_id, old["id"]).status_code == 403


def test_bare_number_and_missing_authorization_refused(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env)
    resp = env.post(
        f"{BASE}/{case_id}/start-contact",
        {"destination_id": did, "phone": "+14155550142", "retries": 5},
        env.key(),
    )
    assert resp.status_code == 403 and "AUTHORIZATION_MISSING" in _codes(resp)
    assert env.dialer.calls_placed == 0


def test_missing_idempotency_key_refused(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env)
    auth = authorize(env, case_id, did).json()
    resp = env.post(
        f"{BASE}/{case_id}/start-contact", {"destination_id": did, "authorization_id": auth["id"]}
    )
    assert resp.status_code == 403 and "IDEMPOTENCY_KEY_MISSING" in _codes(resp)
    in_body = env.post(
        f"{BASE}/{case_id}/start-contact",
        {"destination_id": did, "authorization_id": auth["id"], "idempotency_key": "body-key-1"},
    )
    assert in_body.status_code == 200  # body key accepted


def test_expired_authorization_rejected(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env)
    auth = authorize(env, case_id, did, ttl_seconds=60).json()
    env.clock.advance(seconds=61)
    resp = start(env, case_id, did, auth["id"])
    assert resp.status_code == 403 and "AUTHORIZATION_EXPIRED" in _codes(resp)
    assert env.dialer.calls_placed == 0


def test_authorization_ttl_bounds(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env)
    assert authorize(env, case_id, did, ttl_seconds=999999).status_code == 422
    assert authorize(env, case_id, did, ttl_seconds=1).status_code == 422


def test_authorization_is_single_use(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env)
    auth = authorize(env, case_id, did).json()
    first = start(env, case_id, did, auth["id"])
    assert first.status_code == 200
    second = start(env, case_id, did, auth["id"])  # different idempotency key
    assert second.status_code == 403
    assert "AUTHORIZATION_CONSUMED" in _codes(second)
    assert env.dialer.calls_placed == 1


def test_same_idempotency_key_replays_without_second_call(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env)
    auth = authorize(env, case_id, did).json()
    a = start(env, case_id, did, auth["id"], key="same-key")
    b = start(env, case_id, did, auth["id"], key="same-key")
    assert a.status_code == b.status_code == 200
    assert a.json()["attempt_id"] == b.json()["attempt_id"]
    assert b.json()["replayed"] is True and a.json()["replayed"] is False
    assert env.dialer.calls_placed == 1
    assert _count(env, ContactAttempt) == 1


def test_authorization_creation_is_idempotent_and_single_open(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env)
    a = authorize(env, case_id, did, key="auth-key")
    b = authorize(env, case_id, did, key="auth-key")
    assert a.json()["id"] == b.json()["id"] and b.json()["created"] is False
    other = authorize(env, case_id, did, key="another-key")
    assert other.status_code == 403 and "OPEN_AUTHORIZATION_EXISTS" in _codes(other)


def test_unsupervised_rapid_redial_blocked(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user, "no_answer")
    case_id, did = ready(env)
    results = []
    for _ in range(6):
        auth = authorize(env, case_id, did)
        if auth.status_code != 201:
            results.append(auth.status_code)
            continue
        results.append(start(env, case_id, did, auth.json()["id"]).status_code)
    assert env.dialer.calls_placed == 1
    assert results[0] == 200 and all(r == 403 or r == 409 for r in results[1:])
    refused = start(env, case_id, did, authorize(env, case_id, did).json().get("id"))
    assert "COOLDOWN_ACTIVE" in _codes(refused)


def test_cooldown_elapses_then_next_manual_attempt_allowed(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user, "no_answer")
    case_id, did = ready(env)
    assert authorize_and_start(env, case_id, did).status_code == 200
    auth = authorize(env, case_id, did).json()
    assert start(env, case_id, did, auth["id"]).status_code == 403  # cooling down
    env.clock.advance(seconds=settings.outbound_cooldown_seconds + 120)
    again = authorize(env, case_id, did, ttl_seconds=600).json()
    assert start(env, case_id, did, again["id"]).status_code == 200
    assert env.dialer.calls_placed == 2


def test_per_case_daily_cap_and_rolling_window(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user, "no_answer")
    case_id, did = ready(env)
    for _ in range(settings.max_attempts_per_case_per_day):
        env.clock.advance(seconds=settings.outbound_cooldown_seconds + 1)
        assert authorize_and_start(env, case_id, did).status_code == 200
    env.clock.advance(seconds=settings.outbound_cooldown_seconds + 1)
    capped = authorize_and_start(env, case_id, did)
    assert capped.status_code == 403 and "CASE_DAILY_CAP" in _codes(capped)
    env.clock.advance(hours=25)
    assert authorize_and_start(env, case_id, did).status_code == 200


def test_tenant_daily_cap(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    settings.max_attempts_per_tenant_per_day = 2
    env = _env(client, engine, settings, make_tenant_user, "no_answer")
    for _ in range(2):
        case_id, did = ready(env)
        assert authorize_and_start(env, case_id, did).status_code == 200
    case_id, did = ready(env)
    capped = authorize_and_start(env, case_id, did)
    assert capped.status_code == 403 and "TENANT_DAILY_CAP" in _codes(capped)


def test_active_attempt_blocks_concurrent_start(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env)
    auth = authorize(env, case_id, did).json()
    with make_session_factory(engine)() as s:
        from callshield.db.models import ContactAttempt as AttemptRow
        from callshield.domain.enums import EvidenceLabel

        attempt = AttemptRow(
            tenant_id=env.user.tenant_id,
            case_id=uuid.UUID(case_id),
            destination_id=uuid.UUID(did),
            started_at=env.clock(),
            idempotency_key="seed-active",
        )
        s.add(attempt)
        s.flush()
        s.add(
            ContactAttemptDetail(
                tenant_id=env.user.tenant_id,
                attempt_id=attempt.id,
                case_id=uuid.UUID(case_id),
                source="SYSTEM",
                evidence_label=EvidenceLabel.OBSERVED.value,
            )
        )
        s.commit()
    resp = start(env, case_id, did, auth["id"])
    assert resp.status_code == 403
    assert {"ACTIVE_ATTEMPT_ON_CASE", "ACTIVE_ATTEMPT_ON_DESTINATION"} <= _codes(resp)
    assert env.dialer.calls_placed == 0


def test_authorization_schema_enforces_single_use(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    from sqlalchemy.exc import IntegrityError

    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env)
    auth = authorize(env, case_id, did).json()
    body = start(env, case_id, did, auth["id"]).json()
    with make_session_factory(engine)() as s, pytest.raises(IntegrityError):
        s.add(
            ContactAttemptDetail(
                tenant_id=env.user.tenant_id,
                attempt_id=uuid.UUID(body["attempt_id"]),
                case_id=uuid.UUID(case_id),
                source="SYSTEM",
                evidence_label="OBSERVED",
                authorization_id=uuid.UUID(auth["id"]),
            )
        )
        s.commit()


def test_non_owner_cannot_start_and_refusal_is_audited(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env)
    auth = authorize(env, case_id, did).json()
    with make_session_factory(engine)() as s:
        other = User(
            tenant_id=env.user.tenant_id,
            email=f"o-{uuid.uuid4().hex[:6]}@example.test",
            role=Role.CONSUMER_SUBSCRIBER.value,
        )
        s.add(other)
        s.commit()
    # the retired HTTP route is gone; drive the reusable service as the other user
    resp = legacy_start(
        env,
        case_id,
        {"destination_id": did, "authorization_id": auth["id"]},
        "x-1",
        user_id=other.id,
        role=Role.CONSUMER_SUBSCRIBER,
    )
    assert resp.status_code == 403
    assert {"NOT_CASE_OWNER", "AUTHORIZATION_MISMATCH"} <= _codes(resp)
    assert env.dialer.calls_placed == 0
    with make_session_factory(engine)() as s:
        actions = list(
            s.execute(
                select(AuditEvent.action).where(AuditEvent.tenant_id == env.user.tenant_id)
            ).scalars()
        )
        topics = list(
            s.execute(
                select(LedgerEvent.topic).where(LedgerEvent.tenant_id == env.user.tenant_id)
            ).scalars()
        )
    assert "resolve.start_contact.refused" in actions
    assert "contact.blocked" in topics


def test_every_refusal_is_audited(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env)
    for _ in range(3):
        assert start(env, case_id, did, None).status_code == 403
    with make_session_factory(engine)() as s:
        n = len(
            list(
                s.execute(
                    select(AuditEvent).where(
                        AuditEvent.tenant_id == env.user.tenant_id,
                        AuditEvent.action == "resolve.start_contact.refused",
                    )
                ).scalars()
            )
        )
    assert n == 3


def test_policy_region_and_line_verification_required(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env, policy_region="")
    auth = authorize(env, case_id, did).json()
    resp = start(env, case_id, did, auth["id"])
    assert resp.status_code == 403 and "POLICY_REGION_MISSING" in _codes(resp)
    fix = env.post(
        f"{BASE}/{case_id}/corrections",
        {"field": "policy_region", "new_value": "US-NY", "reason": "set region"},
    )
    assert fix.status_code == 200
    assert start(env, case_id, did, auth["id"]).status_code == 200


def test_max_call_duration_enforced(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    settings.max_call_seconds = 5  # the mock agent connects at t=12
    env = _env(client, engine, settings, make_tenant_user)
    case_id, did = ready(env)
    body = authorize_and_start(env, case_id, did).json()
    att = env.get(f"{BASE}/{case_id}/attempts/{body['attempt_id']}").json()["attempt"]
    assert att["policy_state"]["end_reason"] == "MAX_DURATION"
    assert env.dialer.handles[0].hung_up


def test_additional_call_needs_new_authorization_and_reroutes_state(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = _env(client, engine, settings, make_tenant_user, "agent_silent_after_request")
    enable_ai(env)
    case_id, did = ready(env)
    first = authorize_and_start(env, case_id, did)
    assert first.json()["case_state"] == "UNCONFIRMED"
    steps = env.get(f"{BASE}/{case_id}/next-steps").json()
    assert any(s["kind"] == "SUPERVISED_ADDITIONAL_CALL" for s in steps)
    env.clock.advance(hours=1)
    bare = start(env, case_id, did, None)
    assert bare.status_code == 403 and "AUTHORIZATION_MISSING" in _codes(bare)
    assert env.dialer.calls_placed == 1
    again = authorize(env, case_id, did)
    assert again.status_code == 201
    assert env.get(f"{BASE}/{case_id}").json()["status"] == "USER_APPROVED_CONTACT"
