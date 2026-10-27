from __future__ import annotations

import uuid
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from callshield.api.app import create_app
from callshield.auth.deps import TenantContext, tenant_context
from callshield.auth.passwords import hash_password, verify_password
from callshield.auth.scoping import TenantScopedRepo, scoped_get, scoped_select
from callshield.auth.tokens import Principal, create_access_token, decode_access_token
from callshield.core.config import Settings
from callshield.core.errors import NotFoundError, UnauthorizedError
from callshield.db.models import ProtectedLine, Tenant
from callshield.db.session import make_session_factory
from callshield.domain.enums import Role

from ..conftest import TEST_PASSWORD, TenantUser, TenantUserFactory


def test_password_hashing() -> None:
    hashed = hash_password("a-long-password")
    assert hashed != "a-long-password"
    assert verify_password(hashed, "a-long-password")
    assert not verify_password(hashed, "wrong")
    assert not verify_password(None, "anything")


def test_token_roundtrip_and_tamper(settings: Settings) -> None:
    principal = Principal(uuid.uuid4(), uuid.uuid4(), Role.OPERATOR)
    token, _ = create_access_token(settings, principal)
    assert decode_access_token(settings, token) == principal
    with pytest.raises(UnauthorizedError):
        decode_access_token(settings, token + "x")


def test_bootstrap_login_me(client: TestClient, make_tenant_user: TenantUserFactory) -> None:
    user = make_tenant_user()
    login = client.post("/v1/auth/login", json={"email": user.email, "password": TEST_PASSWORD})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = client.get("/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["tenant_id"] == str(user.tenant_id)
    assert me.json()["role"] == "HOUSEHOLD_ADMIN"


def test_bad_login_and_missing_token(
    client: TestClient, make_tenant_user: TenantUserFactory
) -> None:
    user = make_tenant_user()
    bad = client.post("/v1/auth/login", json={"email": user.email, "password": "nope-nope-nope"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert client.get("/v1/auth/me").status_code == 401
    forged = client.get("/v1/auth/me", headers={"Authorization": "Bearer garbage"})
    assert forged.json()["error"]["code"] == "INVALID_TOKEN"


def test_bootstrap_closed_after_first_tenant(settings: Settings, engine) -> None:  # type: ignore[no-untyped-def]

    closed = settings.model_copy(update={"allow_bootstrap": False})
    body = {"tenant_name": "T", "admin_email": "a@example.test", "admin_password": TEST_PASSWORD}
    with TestClient(create_app(closed, engine)) as c:
        assert c.post("/v1/tenants", json=body).status_code == 201
        body["admin_email"] = "b@example.test"
        denied = c.post("/v1/tenants", json=body)
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "BOOTSTRAP_DISABLED"


def test_admin_creates_member_and_member_cannot(
    client: TestClient, make_tenant_user: TenantUserFactory
) -> None:
    admin = make_tenant_user()
    payload = {
        "email": "member@example.test",
        "password": TEST_PASSWORD,
        "role": "CONSUMER_SUBSCRIBER",
    }
    created = client.post("/v1/users", json=payload, headers=admin.headers)
    assert created.status_code == 201
    login = client.post(
        "/v1/auth/login", json={"email": "member@example.test", "password": TEST_PASSWORD}
    )
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    payload["email"] = "other@example.test"
    denied = client.post("/v1/users", json=payload, headers=member_headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "INSUFFICIENT_ROLE"
    dup = client.post(
        "/v1/users", json=payload | {"email": "member@example.test"}, headers=admin.headers
    )
    assert dup.status_code == 409


def test_new_user_lands_in_admins_tenant(
    client: TestClient, two_tenants: tuple[TenantUser, TenantUser]
) -> None:
    a, b = two_tenants
    payload = {"email": "m@example.test", "password": TEST_PASSWORD}
    assert client.post("/v1/users", json=payload, headers=a.headers).status_code == 201
    login = client.post(
        "/v1/auth/login", json={"email": "m@example.test", "password": TEST_PASSWORD}
    )
    token = decode_access_token(client.app.state.settings, login.json()["access_token"])  # type: ignore[attr-defined]
    assert token.tenant_id == a.tenant_id != b.tenant_id


def test_scoped_get_denies_cross_tenant(session: Session) -> None:
    a, b = Tenant(name="a"), Tenant(name="b")
    session.add_all([a, b])
    session.flush()
    line = ProtectedLine(tenant_id=a.id, e164="+14155550123")
    session.add(line)
    session.commit()
    assert scoped_get(session, ProtectedLine, line.id, a.id) is line
    assert scoped_get(session, ProtectedLine, line.id, b.id) is None
    repo_b = TenantScopedRepo(session, ProtectedLine, b.id)
    assert repo_b.get(line.id) is None
    assert repo_b.list() == []
    created = repo_b.add(e164="+14155550124", tenant_id=a.id)  # tenant override ignored
    assert created.tenant_id == b.id


def test_scoped_select_rejects_non_tenant_model() -> None:
    from callshield.db.models import DestinationRegistry

    with pytest.raises(TypeError):
        scoped_select(DestinationRegistry, uuid.uuid4())


def test_token_for_deleted_tenant_user_is_rejected(
    client: TestClient, settings: Settings, two_tenants: tuple[TenantUser, TenantUser]
) -> None:
    a, b = two_tenants
    # Valid signature, but user_id belongs to tenant B while claiming tenant A.
    forged, _ = create_access_token(
        settings, Principal(a.tenant_id, b.user_id, Role.HOUSEHOLD_ADMIN)
    )
    resp = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401


def test_tenant_context_dependency_isolates_rows(
    settings: Settings, engine: Engine, make_tenant_user: TenantUserFactory
) -> None:
    from callshield.api.app import create_app

    app = create_app(settings, engine)

    @app.get("/t/lines/{line_id}")
    def get_line(
        line_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(tenant_context)]
    ) -> dict[str, str]:
        line = ctx.repo(ProtectedLine).get(line_id)
        if line is None:
            raise NotFoundError("line not found")
        return {"id": str(line.id)}

    with TestClient(app) as c:
        # Reuse the shared in-memory engine: the fixture client has its own app, so bootstrap here.
        def boot(email: str) -> TenantUser:
            r = c.post(
                "/v1/tenants",
                json={"tenant_name": email, "admin_email": email, "admin_password": TEST_PASSWORD},
            ).json()
            tok = r["token"]["access_token"]
            return TenantUser(
                uuid.UUID(r["tenant_id"]), uuid.UUID(r["user_id"]), email, TEST_PASSWORD, tok,
                {"Authorization": f"Bearer {tok}"},
            )  # fmt: skip

        a, b = boot("a@example.test"), boot("b@example.test")
        with make_session_factory(engine)() as s:
            line = ProtectedLine(tenant_id=a.tenant_id, e164="+14155550123")
            s.add(line)
            s.commit()
            line_id = line.id
        assert c.get(f"/t/lines/{line_id}", headers=a.headers).status_code == 200
        denied = c.get(f"/t/lines/{line_id}", headers=b.headers)
        assert denied.status_code == 404
        assert c.get(f"/t/lines/{line_id}").status_code == 401
