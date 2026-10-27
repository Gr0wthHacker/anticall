# ruff: noqa: F811
"""DEFEND REST API: lines, verification, calls, corrections, rules, contacts, consent, audit,
roles and cross-tenant denial for every endpoint."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from callshield.core.clock import FrozenClock, set_clock
from callshield.db.models.defend import Classification, ProtectedLine
from callshield.db.models.defend_ext import LineVerificationChallenge
from callshield.db.models.platform import LedgerEvent
from callshield.defend.verification import InMemoryOutboxChannel, set_challenge_channel
from callshield.telephony.mock import generic_event
from tests.conftest import TEST_PASSWORD, TenantUser, TenantUserFactory
from tests.defend_support import (
    FICTIONAL_LINE,
    FICTIONAL_USER,
    LineHandle,
    defend_reset,  # noqa: F401
    enable_live_processing,
    enroll_line,
    error_code,
    inbound_call_payload,
    post_webhook,
    rows,
)

pytestmark = pytest.mark.usefixtures("defend_reset")

CONFIG = {"user_number": FICTIONAL_USER}


def create_line(
    client: TestClient, user: TenantUser, e164: str = FICTIONAL_LINE, **extra: Any
) -> Any:
    return client.post(
        "/v1/lines",
        headers=user.headers,
        json={"e164": e164, "label": "Main", "provider": "mock", "routing_config": CONFIG, **extra},
    )


# ------------------------------------------------------------------ lines


def test_create_line_is_unverified_unroutable_and_returns_credentials_once(
    client: TestClient, make_tenant_user: TenantUserFactory
) -> None:
    user = make_tenant_user()
    resp = create_line(client, user, "(415) 555-0100")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["e164"] == FICTIONAL_LINE
    assert body["verification_status"] == "UNVERIFIED" and body["routable"] is False
    assert body["screening_enabled"] is False
    creds = body["webhook"]
    assert (
        creds["url_path"] == f"/v1/webhooks/mock/{creds['line_key']}" and len(creds["secret"]) >= 32
    )
    assert "secret" not in client.get(f"/v1/lines/{body['id']}", headers=user.headers).json()


def test_screening_cannot_be_enabled_at_creation(
    client: TestClient, make_tenant_user: TenantUserFactory
) -> None:
    user = make_tenant_user()
    assert create_line(client, user, screening_enabled=True).status_code == 422


def test_duplicate_line_conflicts_and_bad_input_is_rejected(
    client: TestClient, make_tenant_user: TenantUserFactory
) -> None:
    user = make_tenant_user()
    assert create_line(client, user).status_code == 201
    assert create_line(client, user).status_code == 409
    assert create_line(client, user, "12345").status_code == 422
    other = client.post(
        "/v1/lines",
        headers=user.headers,
        json={"e164": "+14155550102", "provider": "carrier-pigeon"},
    )
    assert other.status_code == 422 and other.json()["error"]["code"] == "UNSUPPORTED_PROVIDER"


def test_user_number_equal_to_line_is_rejected_as_loop(
    client: TestClient, make_tenant_user: TenantUserFactory
) -> None:
    user = make_tenant_user()
    resp = client.post(
        "/v1/lines",
        headers=user.headers,
        json={"e164": FICTIONAL_LINE, "routing_config": {"user_number": FICTIONAL_LINE}},
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "ROUTING_LOOP"


def test_patch_routing_config_merges_validates_and_audits(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset)
    resp = client.patch(
        f"/v1/lines/{line.id}",
        headers=user.headers,
        json={"label": "Renamed", "routing_config": {"max_hops": 5, "unknown_action": "VOICEMAIL"}},
    )
    assert resp.status_code == 200
    cfg = resp.json()["routing_config"]
    assert (
        cfg["max_hops"] == 5
        and cfg["user_number"] == FICTIONAL_USER
        and resp.json()["label"] == "Renamed"
    )
    for bad in (
        {"max_hops": 99},
        {"unknown_action": "EXPLODE"},
        {"nope": 1},
        {"user_number": FICTIONAL_LINE},
    ):
        r = client.patch(f"/v1/lines/{line.id}", headers=user.headers, json={"routing_config": bad})
        assert r.status_code == 422, bad
    audit = client.get("/v1/audit-events?action=line.updated", headers=user.headers).json()
    assert audit["total"] == 1


def test_list_lines_paginates(client: TestClient, make_tenant_user: TenantUserFactory) -> None:
    user = make_tenant_user()
    for n in range(3):
        assert create_line(client, user, f"+1415555010{n + 2}").status_code == 201
    page = client.get("/v1/lines?limit=2&offset=0", headers=user.headers).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert len(client.get("/v1/lines?limit=2&offset=2", headers=user.headers).json()["items"]) == 1
    assert client.get("/v1/lines?limit=0", headers=user.headers).status_code == 422


def test_rotating_webhook_secret_invalidates_the_old_one(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset)
    assert post_webhook(client, line, inbound_call_payload("a")).status_code == 200
    new = client.post(f"/v1/lines/{line.id}/webhook-secret:rotate", headers=user.headers).json()
    assert new["secret"] != line.secret and new["line_key"] == line.line_key
    assert post_webhook(client, line, inbound_call_payload("b")).status_code == 401
    assert (
        post_webhook(client, line, inbound_call_payload("b"), secret=new["secret"]).status_code
        == 200
    )


# ------------------------------------------------------------------ ownership verification (D-001)


def test_verification_flow_happy_path(
    client: TestClient,
    make_tenant_user: TenantUserFactory,
    defend_reset: InMemoryOutboxChannel,
    engine: Engine,
) -> None:
    user = make_tenant_user()
    line_id = create_line(client, user).json()["id"]
    sent = client.post(f"/v1/lines/{line_id}/verify", headers=user.headers, json={})
    assert sent.status_code == 200 and sent.json()["status"] == "PENDING"
    code = defend_reset.last_code(uuid.UUID(line_id))
    assert code and len(code) == 6
    challenge = rows(engine, LineVerificationChallenge)[0]
    assert code not in challenge.code_hash and challenge.code_hash != code  # only a hash is stored
    done = client.post(f"/v1/lines/{line_id}/verify", headers=user.headers, json={"code": code})
    assert done.status_code == 200 and done.json()["status"] == "VERIFIED"
    line = client.get(f"/v1/lines/{line_id}", headers=user.headers).json()
    assert line["routable"] is True and line["verified_at"]
    assert len(rows(engine, LedgerEvent, topic="line.verified")) == 1


def test_wrong_code_fails_and_locks_after_five_attempts(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    user = make_tenant_user()
    line_id = create_line(client, user).json()["id"]
    client.post(f"/v1/lines/{line_id}/verify", headers=user.headers, json={})
    real = defend_reset.last_code(uuid.UUID(line_id)) or ""
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        r = client.post(f"/v1/lines/{line_id}/verify", headers=user.headers, json={"code": wrong})
        assert r.status_code == 400 and error_code(r) == "CHALLENGE_FAILED"
    locked = client.post(f"/v1/lines/{line_id}/verify", headers=user.headers, json={"code": real})
    assert locked.status_code == 400 and error_code(locked) == "CHALLENGE_LOCKED"
    assert client.get(f"/v1/lines/{line_id}", headers=user.headers).json()["routable"] is False
    # a fresh challenge works
    client.post(f"/v1/lines/{line_id}/verify", headers=user.headers, json={})
    fresh = defend_reset.last_code(uuid.UUID(line_id)) or ""
    assert (
        client.post(
            f"/v1/lines/{line_id}/verify", headers=user.headers, json={"code": fresh}
        ).json()["status"]
        == "VERIFIED"
    )


def test_code_expires(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    user = make_tenant_user()
    clock = FrozenClock(datetime.now(UTC))
    set_clock(clock)
    line_id = create_line(client, user).json()["id"]
    client.post(f"/v1/lines/{line_id}/verify", headers=user.headers, json={})
    code = defend_reset.last_code(uuid.UUID(line_id)) or ""
    clock.advance(minutes=11)
    resp = client.post(f"/v1/lines/{line_id}/verify", headers=user.headers, json={"code": code})
    assert resp.status_code == 400 and error_code(resp) == "CHALLENGE_EXPIRED"


def test_confirm_without_challenge_and_already_verified(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    user = make_tenant_user()
    line_id = create_line(client, user).json()["id"]
    r = client.post(f"/v1/lines/{line_id}/verify", headers=user.headers, json={"code": "123456"})
    assert r.status_code == 400 and error_code(r) == "CHALLENGE_EXPIRED"
    line = enroll_line(client, user, defend_reset, "+14155550102")
    again = client.post(f"/v1/lines/{line.id}/verify", headers=user.headers, json={})
    assert again.status_code == 409


def test_verification_requires_configured_channel_outside_dev(
    client: TestClient, make_tenant_user: TenantUserFactory
) -> None:
    set_challenge_channel(None)
    client.app.state.settings.environment = "prod"  # type: ignore[attr-defined]
    user = make_tenant_user()
    line_id = create_line(client, user).json()["id"]
    resp = client.post(f"/v1/lines/{line_id}/verify", headers=user.headers, json={})
    assert resp.status_code == 403 and error_code(resp) == "VERIFICATION_CHANNEL_MISSING"
    client.app.state.settings.environment = "test"  # type: ignore[attr-defined]


def test_unverified_line_cannot_be_routed_even_after_failed_verify(
    client: TestClient,
    make_tenant_user: TenantUserFactory,
    defend_reset: InMemoryOutboxChannel,
    engine: Engine,
) -> None:
    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset, verify=False)
    assert post_webhook(client, line, inbound_call_payload("x")).status_code == 403
    assert rows(engine, ProtectedLine)[0].verification_status == "UNVERIFIED"


# ------------------------------------------------------------------ processing consent endpoints


def test_consent_view_starts_denied_and_follows_grants(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset)
    view = client.get(f"/v1/lines/{line.id}/consent", headers=user.headers).json()
    assert view["records"] == [] and view["decision"]["allow_live_stt"] is False
    assert "LIVE_STT_DISABLED_BY_DEPLOYMENT" in view["decision"]["reasons"]
    grant = client.post(
        f"/v1/lines/{line.id}/consent",
        headers=user.headers,
        json={"processing_mode": "TRANSCRIPT_RETAINED", "authorized": True},
    )
    assert grant.status_code == 201 and len(grant.json()["records"]) == 1
    # the deployment flag is off in this app, so consent alone never enables processing
    assert grant.json()["decision"]["allow_live_stt"] is False
    assert (
        client.get(f"/v1/lines/{line.id}/consent/decision", headers=user.headers).status_code == 200
    )


def test_consent_preview_has_no_side_effects(
    client: TestClient,
    make_tenant_user: TenantUserFactory,
    defend_reset: InMemoryOutboxChannel,
    engine: Engine,
) -> None:
    from callshield.db.models.defend import ConsentRecord

    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset)
    client.get(f"/v1/lines/{line.id}/consent/decision", headers=user.headers)
    assert not rows(engine, ConsentRecord)


def test_consent_is_appended_never_edited(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset)
    for authorized in (True, False, True):
        client.post(
            f"/v1/lines/{line.id}/consent",
            headers=user.headers,
            json={"processing_mode": "LIVE_STT_TRANSIENT", "authorized": authorized},
        )
    records = client.get(f"/v1/lines/{line.id}/consent", headers=user.headers).json()["records"]
    assert [r["authorized"] for r in records] == [True, False, True]
    assert records[1]["revoked_at"] is not None


def test_consent_rejects_unknown_mode(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset)
    r = client.post(
        f"/v1/lines/{line.id}/consent",
        headers=user.headers,
        json={"processing_mode": "EVERYTHING", "authorized": True},
    )
    assert r.status_code == 422


# ------------------------------------------------------------------ calls, corrections


@pytest.fixture
def populated(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> LineHandle:
    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset)
    client.post(
        "/v1/contacts",
        headers=user.headers,
        json={"line_id": str(line.id), "e164": "+14155550111", "kind": "TRUSTED"},
    )
    post_webhook(client, line, inbound_call_payload("c-trusted", caller="+14155550111"))
    post_webhook(client, line, inbound_call_payload("c-unknown", caller="+14155550166"))
    post_webhook(client, line, generic_event("call.ended", "e1", call_id="c-unknown"))
    return line


def test_list_calls_and_filters(client: TestClient, populated: LineHandle) -> None:
    h = populated.user.headers
    allc = client.get("/v1/calls", headers=h).json()
    assert allc["total"] == 2 and {c["direction"] for c in allc["items"]} == {"INBOUND"}
    assert client.get("/v1/calls?direction=inbound", headers=h).json()["total"] == 2
    assert client.get("/v1/calls?direction=OUTBOUND", headers=h).json()["total"] == 0
    assert client.get("/v1/calls?disposition=VOICEMAIL", headers=h).json()["total"] == 1
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert client.get("/v1/calls", headers=h, params={"date_from": future}).json()["total"] == 0
    assert client.get("/v1/calls", headers=h, params={"date_from": past}).json()["total"] == 2
    assert client.get("/v1/calls", headers=h, params={"date_to": past}).json()["total"] == 0
    assert len(client.get("/v1/calls?limit=1", headers=h).json()["items"]) == 1


def test_list_calls_filters_by_class_and_claimed_identity(
    client: TestClient, populated: LineHandle
) -> None:
    h = populated.user.headers
    first = client.get("/v1/calls", headers=h).json()["items"][0]["id"]
    corr = client.post(
        f"/v1/calls/{first}/corrections", headers=h, json={"label": "SCAM_SUSPECTED"}
    )
    assert corr.status_code == 201
    scam = client.get("/v1/calls", headers=h, params={"class": "SCAM_SUSPECTED"}).json()
    assert scam["total"] == 1 and scam["items"][0]["category"] == "scam-likely"
    assert client.get("/v1/calls", headers=h, params={"class": "SURVEY"}).json()["total"] == 0
    assert client.get("/v1/calls", headers=h, params={"claimed": "acme%"}).json()["total"] == 0


def test_claimed_filter_matches_claims_and_escapes_wildcards(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    from callshield.core.config import Settings  # noqa: F401

    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset)
    post_webhook(client, line, inbound_call_payload("one"))
    assert (
        client.get("/v1/calls", headers=user.headers, params={"claimed": "%"}).json()["total"] == 0
    )
    assert (
        client.get("/v1/calls", headers=user.headers, params={"claimed": "_"}).json()["total"] == 0
    )


def test_call_detail_separates_facts_claims_and_model_conclusions(
    client: TestClient, populated: LineHandle
) -> None:
    h = populated.user.headers
    cid = next(
        c["id"]
        for c in client.get("/v1/calls", headers=h).json()["items"]
        if c["displayed_caller_id"] == "+14155550166"
    )
    detail = client.get(f"/v1/calls/{cid}", headers=h).json()
    facts = detail["facts"]
    assert facts["evidence_label"] == "OBSERVED" and facts["displayed_caller_id"] == "+14155550166"
    assert facts["network_attestation"]["is_verdict"] is False
    assert "not a verified calling party" in facts["note"]
    assert detail["claims"] == [] and detail["model_conclusions"] == []
    assert detail["effective_classification"] is None
    assert detail["routing"]["action"] == "VOICEMAIL" and detail["incident_id"]
    assert set(detail) >= {"facts", "claims", "model_conclusions", "routing"}


def test_corrections_append_and_never_mutate_the_original(
    client: TestClient, populated: LineHandle, engine: Engine
) -> None:
    h = populated.user.headers
    cid = client.get("/v1/calls", headers=h).json()["items"][0]["id"]
    first = client.post(
        f"/v1/calls/{cid}/corrections",
        headers=h,
        json={"label": "UNSOLICITED_SALES", "note": "sounded like sales"},
    ).json()
    second = client.post(
        f"/v1/calls/{cid}/corrections", headers=h, json={"label": "INFORMATIONAL"}
    ).json()
    assert first["supersedes_id"] is None and second["supersedes_id"] == first["id"]
    assert first["evidence_label"] == "USER_ATTESTED"
    stored = {str(c.id): c for c in rows(engine, Classification)}
    assert len(stored) == 2 and stored[first["id"]].label == "UNSOLICITED_SALES"
    assert stored[first["id"]].reviewed_status == "CORRECTED"
    detail = client.get(f"/v1/calls/{cid}", headers=h).json()
    assert len(detail["model_conclusions"]) == 2
    assert detail["effective_classification"]["label"] == "INFORMATIONAL"
    assert len(rows(engine, LedgerEvent, topic="correction.recorded")) == 2


def test_correction_rejects_invalid_label(client: TestClient, populated: LineHandle) -> None:
    h = populated.user.headers
    cid = client.get("/v1/calls", headers=h).json()["items"][0]["id"]
    assert (
        client.post(f"/v1/calls/{cid}/corrections", headers=h, json={"label": "NOPE"}).status_code
        == 422
    )


# ------------------------------------------------------------------ rules


def test_rule_crud_and_audit(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset)
    h = user.headers
    created = client.post(
        "/v1/rules",
        headers=h,
        json={
            "line_id": str(line.id),
            "priority": 10,
            "matcher": {"caller_prefix": ["+1800"]},
            "action": "REJECT",
        },
    )
    assert created.status_code == 201
    rid = created.json()["id"]
    put = client.put(
        f"/v1/rules/{rid}",
        headers=h,
        json={
            "priority": 5,
            "matcher": {"anonymous": True},
            "action": "VOICEMAIL",
            "enabled": True,
        },
    )
    assert (
        put.status_code == 200
        and put.json()["priority"] == 5
        and put.json()["matcher"] == {"anonymous": True}
    )
    assert client.get(f"/v1/rules/{rid}", headers=h).json()["action"] == "VOICEMAIL"
    assert len(client.get(f"/v1/rules?line_id={line.id}", headers=h).json()) == 1
    gone = client.delete(f"/v1/rules/{rid}", headers=h)
    assert gone.status_code == 200 and gone.json()["enabled"] is False
    assert client.get(f"/v1/rules/{rid}", headers=h).status_code == 200  # disabled, not deleted
    assert client.get("/v1/rules?include_disabled=false", headers=h).json() == []
    actions = {a["action"] for a in client.get("/v1/audit-events", headers=h).json()["items"]}
    assert {"rule.created", "rule.updated", "rule.disabled"} <= actions


@pytest.mark.parametrize(
    "body",
    [
        {"matcher": {}, "action": "REJECT"},
        {"matcher": {"anonymous": True}, "action": "FALLBACK"},
        {"matcher": {"anonymous": True}, "action": "DIAL"},
        {"matcher": {"caller_prefix": ["not-a-prefix"]}, "action": "REJECT"},
        {"matcher": {"anonymous": True, "sql": "1=1"}, "action": "REJECT"},
        {"matcher": {"anonymous": True}, "action": "REJECT", "priority": -1},
    ],
)
def test_invalid_rules_rejected(
    client: TestClient,
    make_tenant_user: TenantUserFactory,
    defend_reset: InMemoryOutboxChannel,
    body: dict[str, Any],
) -> None:
    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset)
    assert (
        client.post(
            "/v1/rules", headers=user.headers, json={"line_id": str(line.id), **body}
        ).status_code
        == 422
    )


# ------------------------------------------------------------------ contacts


def test_contact_crud(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset)
    h = user.headers
    body = {
        "line_id": str(line.id),
        "e164": "(415) 555-0111",
        "kind": "PERMITTED",
        "name": "Pharmacy",
    }
    created = client.post("/v1/contacts", headers=h, json=body)
    assert created.status_code == 201 and created.json()["e164"] == "+14155550111"
    cid = created.json()["id"]
    assert client.post("/v1/contacts", headers=h, json=body).status_code == 409
    patched = client.patch(f"/v1/contacts/{cid}", headers=h, json={"kind": "TRUSTED", "name": "Rx"})
    assert patched.json()["kind"] == "TRUSTED" and patched.json()["name"] == "Rx"
    assert len(client.get(f"/v1/contacts?line_id={line.id}", headers=h).json()) == 1
    assert client.delete(f"/v1/contacts/{cid}", headers=h).status_code == 204
    assert client.get(f"/v1/contacts/{cid}", headers=h).status_code == 404
    actions = {a["action"] for a in client.get("/v1/audit-events", headers=h).json()["items"]}
    assert {"contact.created", "contact.updated", "contact.deleted"} <= actions


def test_contact_validation(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset)
    for bad in ({"e164": "abc"}, {"kind": "BEST_FRIEND"}):
        body = {"line_id": str(line.id), "e164": "+14155550111", **bad}
        assert client.post("/v1/contacts", headers=user.headers, json=body).status_code == 422


def test_contact_changes_take_effect_on_next_call(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset)
    contact = client.post(
        "/v1/contacts",
        headers=user.headers,
        json={"line_id": str(line.id), "e164": "+14155550111", "kind": "KNOWN_UNWANTED"},
    ).json()
    assert (
        post_webhook(client, line, inbound_call_payload("a", caller="+14155550111")).json()[
            "instructions"
        ][0]["op"]
        == "voicemail"
    )
    client.patch(f"/v1/contacts/{contact['id']}", headers=user.headers, json={"kind": "TRUSTED"})
    assert (
        post_webhook(client, line, inbound_call_payload("b", caller="+14155550111")).json()[
            "instructions"
        ][0]["op"]
        == "transfer"
    )


# ------------------------------------------------------------------ audit events


def test_audit_events_are_paginated_and_tenant_scoped(
    client: TestClient,
    two_tenants: tuple[TenantUser, TenantUser],
    defend_reset: InMemoryOutboxChannel,
) -> None:
    a, b = two_tenants
    for n in range(3):
        create_line(client, a, f"+1415555010{n + 2}")
    page = client.get("/v1/audit-events?limit=2", headers=a.headers).json()
    assert page["total"] >= 3 and len(page["items"]) == 2
    assert all(i["immutable_ref"] for i in page["items"])
    mine = {
        i["id"] for i in client.get("/v1/audit-events?limit=200", headers=a.headers).json()["items"]
    }
    theirs = client.get("/v1/audit-events?limit=200", headers=b.headers).json()["items"]
    assert not mine & {i["id"] for i in theirs}
    assert all(i["action"] != "line.enrolled" for i in theirs)
    assert client.get("/v1/audit-events").status_code == 401


# ------------------------------------------------------------------ cross-tenant denial + roles


@pytest.fixture
def victim(
    client: TestClient,
    two_tenants: tuple[TenantUser, TenantUser],
    defend_reset: InMemoryOutboxChannel,
) -> dict[str, Any]:
    a, b = two_tenants
    line = enroll_line(client, a, defend_reset)
    rule = client.post(
        "/v1/rules",
        headers=a.headers,
        json={"line_id": str(line.id), "matcher": {"anonymous": True}, "action": "VOICEMAIL"},
    ).json()
    contact = client.post(
        "/v1/contacts", headers=a.headers, json={"line_id": str(line.id), "e164": "+14155550111"}
    ).json()
    post_webhook(client, line, inbound_call_payload("x1"))
    call_id = client.get("/v1/calls", headers=a.headers).json()["items"][0]["id"]
    return {
        "line": line,
        "rule": rule["id"],
        "contact": contact["id"],
        "call": call_id,
        "attacker": b,
    }


def test_every_endpoint_denies_cross_tenant_access_with_404(
    client: TestClient, victim: dict[str, Any]
) -> None:
    h = victim["attacker"].headers
    line_id, rule, contact, call = (
        victim["line"].id,
        victim["rule"],
        victim["contact"],
        victim["call"],
    )
    attempts = [
        ("GET", f"/v1/lines/{line_id}", None),
        ("PATCH", f"/v1/lines/{line_id}", {"label": "pwn"}),
        ("POST", f"/v1/lines/{line_id}/verify", {}),
        ("POST", f"/v1/lines/{line_id}/screening:disable", None),
        ("POST", f"/v1/lines/{line_id}/screening:enable", None),
        ("POST", f"/v1/lines/{line_id}/webhook-secret:rotate", None),
        ("GET", f"/v1/lines/{line_id}/consent", None),
        (
            "POST",
            f"/v1/lines/{line_id}/consent",
            {"processing_mode": "LIVE_STT_TRANSIENT", "authorized": True},
        ),
        ("GET", f"/v1/lines/{line_id}/consent/decision", None),
        ("GET", f"/v1/calls/{call}", None),
        ("POST", f"/v1/calls/{call}/corrections", {"label": "SURVEY"}),
        ("GET", f"/v1/rules/{rule}", None),
        (
            "PUT",
            f"/v1/rules/{rule}",
            {"priority": 1, "matcher": {"anonymous": True}, "action": "REJECT"},
        ),
        ("DELETE", f"/v1/rules/{rule}", None),
        (
            "POST",
            "/v1/rules",
            {"line_id": str(line_id), "matcher": {"anonymous": True}, "action": "REJECT"},
        ),
        ("GET", f"/v1/contacts/{contact}", None),
        ("PATCH", f"/v1/contacts/{contact}", {"name": "x"}),
        ("DELETE", f"/v1/contacts/{contact}", None),
        ("POST", "/v1/contacts", {"line_id": str(line_id), "e164": "+14155550199"}),
    ]
    for method, path, body in attempts:
        resp = client.request(method, path, headers=h, json=body)
        assert resp.status_code == 404, (method, path, resp.status_code, resp.text)
        assert str(line_id) not in resp.text.replace(path, "")


def test_list_endpoints_never_leak_other_tenants_rows(
    client: TestClient, victim: dict[str, Any]
) -> None:
    h = victim["attacker"].headers
    assert client.get("/v1/lines", headers=h).json()["items"] == []
    assert client.get("/v1/calls", headers=h).json() == {
        "items": [],
        "total": 0,
        "limit": 50,
        "offset": 0,
    }
    assert client.get("/v1/rules", headers=h).json() == []
    assert client.get("/v1/contacts", headers=h).json() == []
    assert client.get(f"/v1/rules?line_id={victim['line'].id}", headers=h).json() == []
    assert client.get(f"/v1/contacts?line_id={victim['line'].id}", headers=h).json() == []


def test_victim_data_unchanged_after_attacks(client: TestClient, victim: dict[str, Any]) -> None:
    h, owner = victim["attacker"].headers, victim["line"].user.headers
    client.patch(f"/v1/lines/{victim['line'].id}", headers=h, json={"label": "pwn"})
    client.delete(f"/v1/rules/{victim['rule']}", headers=h)
    assert client.get(f"/v1/lines/{victim['line'].id}", headers=owner).json()["label"] == "test"
    assert client.get(f"/v1/rules/{victim['rule']}", headers=owner).json()["enabled"] is True


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/v1/lines"),
        ("POST", "/v1/lines"),
        ("GET", "/v1/calls"),
        ("GET", "/v1/rules"),
        ("POST", "/v1/rules"),
        ("GET", "/v1/contacts"),
        ("POST", "/v1/contacts"),
        ("GET", "/v1/audit-events"),
        ("GET", f"/v1/lines/{uuid.uuid4()}/consent"),
        ("POST", f"/v1/lines/{uuid.uuid4()}/screening:disable"),
    ],
)
def test_endpoints_require_authentication(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path).status_code == 401


def test_reviewer_role_can_read_but_not_change_protection(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    admin = make_tenant_user()
    line = enroll_line(client, admin, defend_reset)
    client.post(
        "/v1/users",
        headers=admin.headers,
        json={"email": "reviewer@example.test", "password": TEST_PASSWORD, "role": "REVIEWER"},
    )
    token = client.post(
        "/v1/auth/login", json={"email": "reviewer@example.test", "password": TEST_PASSWORD}
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/v1/lines", headers=h).status_code == 200
    assert client.get(f"/v1/lines/{line.id}", headers=h).status_code == 200
    assert client.post(f"/v1/lines/{line.id}/screening:disable", headers=h).status_code == 403
    assert (
        client.post(
            "/v1/contacts", headers=h, json={"line_id": str(line.id), "e164": "+14155550111"}
        ).status_code
        == 403
    )
    assert client.patch(f"/v1/lines/{line.id}", headers=h, json={"label": "x"}).status_code == 403
    assert (
        client.post(
            f"/v1/lines/{line.id}/consent",
            headers=h,
            json={"processing_mode": "LIVE_STT_TRANSIENT", "authorized": True},
        ).status_code
        == 403
    )


# ------------------------------------------------------------------ deployment gate


def test_deployment_flag_off_blocks_screening_even_with_consent_and_permitted_jurisdiction(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> None:
    """Default Settings keep live_stt_enabled off; nothing the user does can override it."""
    user = make_tenant_user()
    line = enroll_line(client, user, defend_reset)
    enable_live_processing(client, line)
    enabled = client.post(f"/v1/lines/{line.id}/screening:enable", headers=user.headers).json()
    assert enabled["screening_enabled"] is True and enabled["processing_allows_screening"] is False
    resp = post_webhook(client, line, inbound_call_payload("gate"))
    assert [i["op"] for i in resp.json()["instructions"]] == ["voicemail"]
