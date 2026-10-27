"""Acknowledgment gate: served text, all-items-required acceptance, tenancy, callback flag."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.acknowledgment.content import ACKNOWLEDGMENT_V1, ACKNOWLEDGMENT_VERSION
from callshield.acknowledgment.service import current_text_sha256
from callshield.db.models.platform import AuditEvent
from callshield.db.models.platform_ext import Acknowledgment
from tests.conftest import TEST_PASSWORD, TenantUser, TenantUserFactory

REPO_ROOT = Path(__file__).resolve().parents[4]
ITEM_IDS = [item["id"] for item in ACKNOWLEDGMENT_V1["items"]]


def _all_checked(**overrides: bool) -> dict[str, Any]:
    items = {i: True for i in ITEM_IDS} | overrides
    return {"text_sha256": current_text_sha256(), "items": items}


def _accept(client: TestClient, user: TenantUser, body: dict[str, Any]) -> Any:
    return client.post("/v1/acknowledgments", json=body, headers=user.headers)


def test_embedded_copy_matches_legal_json() -> None:
    legal = json.loads((REPO_ROOT / "docs/legal/acknowledgment-v1.json").read_text("utf-8"))
    assert legal == ACKNOWLEDGMENT_V1
    assert f"{legal['id']}-{legal['version']}" == ACKNOWLEDGMENT_VERSION


def test_current_serves_versioned_unchecked_items(
    client: TestClient, make_tenant_user: TenantUserFactory
) -> None:
    user = make_tenant_user("Owner")
    body = client.get("/v1/acknowledgments/current", headers=user.headers).json()
    assert body["acknowledgment_version"] == ACKNOWLEDGMENT_VERSION
    assert body["text_sha256"] == current_text_sha256()
    assert body["default_checked"] is False and body["all_required"] is True
    assert [i["id"] for i in body["items"]] == ITEM_IDS
    assert body["status"] == "DRAFT_NOT_COUNSEL_REVIEWED"
    assert client.get("/v1/acknowledgments/current").status_code == 401


def test_accept_records_exact_version_user_and_operator(
    client: TestClient, session: Session, make_tenant_user: TenantUserFactory
) -> None:
    user = make_tenant_user("Owner")
    resp = _accept(client, user, _all_checked())
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["user_id"] == str(user.user_id) and out["created"] is True
    assert out["acknowledgment_version"] == ACKNOWLEDGMENT_VERSION
    assert out["text_sha256"] == current_text_sha256() and out["item_ids"] == ITEM_IDS
    assert out["operator_id"] == str(user.tenant_id)
    row = session.execute(select(Acknowledgment)).scalar_one()
    assert row.user_id == user.user_id and row.tenant_id == user.tenant_id
    actions = session.execute(select(AuditEvent.action)).scalars().all()
    assert "acknowledgment.accepted" in actions


def test_accept_is_idempotent(client: TestClient, session: Session, make_tenant_user: Any) -> None:
    user = make_tenant_user("Owner")
    first = _accept(client, user, _all_checked())
    again = _accept(client, user, _all_checked())
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["acknowledgment_id"] == first.json()["acknowledgment_id"]
    assert len(session.execute(select(Acknowledgment)).scalars().all()) == 1


def test_every_item_must_be_true(client: TestClient, make_tenant_user: TenantUserFactory) -> None:
    user = make_tenant_user("Owner")
    unchecked = _accept(client, user, _all_checked(**{ITEM_IDS[0]: False}))
    assert unchecked.status_code == 422
    err = unchecked.json()["error"]
    assert err["code"] == "ACKNOWLEDGMENT_INCOMPLETE"
    assert err["details"]["unchecked"] == [ITEM_IDS[0]]
    missing = _all_checked()
    del missing["items"][ITEM_IDS[1]]
    assert _accept(client, user, missing).status_code == 422
    extra = _accept(client, user, _all_checked(made_up_item=True))
    assert extra.status_code == 422 and extra.json()["error"]["details"]["unknown"] == [
        "made_up_item"
    ]
    assert (
        _accept(client, user, {"text_sha256": current_text_sha256(), "items": {}}).status_code
        == 422
    )
    mine = client.get("/v1/acknowledgments/me", headers=user.headers).json()
    assert mine["acknowledged"] is False and mine["history"] == []


def test_stale_digest_is_refused(client: TestClient, make_tenant_user: TenantUserFactory) -> None:
    user = make_tenant_user("Owner")
    body = _all_checked() | {"text_sha256": "0" * 64}
    resp = _accept(client, user, body)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "ACKNOWLEDGMENT_STALE"
    assert _accept(client, user, _all_checked() | {"surprise": 1}).status_code == 422


def test_me_reflects_only_my_acknowledgment(
    client: TestClient, make_tenant_user: TenantUserFactory
) -> None:
    mine, other = make_tenant_user("Mine"), make_tenant_user("Other")
    before = client.get("/v1/acknowledgments/me", headers=mine.headers).json()
    assert before["acknowledged"] is False
    assert _accept(client, mine, _all_checked()).status_code == 201
    after = client.get("/v1/acknowledgments/me", headers=mine.headers).json()
    assert after["acknowledged"] is True and len(after["history"]) == 1
    assert after["current_text_sha256"] == current_text_sha256()
    theirs = client.get("/v1/acknowledgments/me", headers=other.headers).json()
    assert theirs["acknowledged"] is False and theirs["history"] == []


def test_cross_tenant_isolation_and_same_user_flow(
    client: TestClient, session: Session, two_tenants: tuple[TenantUser, TenantUser]
) -> None:
    a, b = two_tenants
    assert _accept(client, a, _all_checked()).status_code == 201
    assert _accept(client, b, _all_checked()).status_code == 201  # B is not blocked by A's row
    rows = session.execute(select(Acknowledgment)).scalars().all()
    assert {(r.tenant_id, r.user_id) for r in rows} == {
        (a.tenant_id, a.user_id),
        (b.tenant_id, b.user_id),
    }
    for user in (a, b):
        history = client.get("/v1/acknowledgments/me", headers=user.headers).json()["history"]
        assert [h["user_id"] for h in history] == [str(user.user_id)]
        assert history[0]["operator_id"] == str(user.tenant_id)


def test_reviewer_role_cannot_accept(client: TestClient, make_tenant_user: Any) -> None:
    admin = make_tenant_user("Owner")
    made = client.post(
        "/v1/users",
        json={"email": "rev@example.test", "password": TEST_PASSWORD, "role": "REVIEWER"},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    login = client.post(
        "/v1/auth/login", json={"email": "rev@example.test", "password": TEST_PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    denied = client.post("/v1/acknowledgments", json=_all_checked(), headers=headers)
    assert denied.status_code == 403
    assert client.get("/v1/acknowledgments/current", headers=headers).status_code == 200


def test_callback_capabilities_report_acknowledged(
    client: TestClient, make_tenant_user: TenantUserFactory
) -> None:
    user = make_tenant_user("Owner")
    caps = client.get("/v1/callback/capabilities", headers=user.headers).json()
    assert caps["acknowledged"] is False and caps["callback_enabled"] is False
    assert _accept(client, user, _all_checked()).status_code == 201
    after = client.get("/v1/callback/capabilities", headers=user.headers).json()
    assert after["acknowledged"] is True
    other = make_tenant_user("Other")
    assert (
        client.get("/v1/callback/capabilities", headers=other.headers).json()["acknowledged"]
        is False
    )
    assert "acknowledged" not in client.get("/v1/meta/capabilities").text
