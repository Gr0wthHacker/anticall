"""Shared fixtures: in-memory SQLite engine, session, API client, two tenants."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from callshield.api.app import create_app
from callshield.core.clock import set_clock
from callshield.core.config import Settings
from callshield.db.base import Base
from callshield.db.models import Tenant
from callshield.db.session import make_engine, make_session_factory

TEST_PASSWORD = "correct-horse-battery"


@dataclass
class TenantUser:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    password: str
    token: str
    headers: dict[str, str] = field(default_factory=dict)


TenantUserFactory = Callable[..., TenantUser]


@pytest.fixture
def settings() -> Settings:
    return Settings(db_url="sqlite://", environment="test", allow_bootstrap=True)


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = make_engine("sqlite://")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    sess = make_session_factory(engine)()
    yield sess
    sess.rollback()
    sess.close()


@pytest.fixture
def client(settings: Settings, engine: Engine) -> Iterator[TestClient]:
    app = create_app(settings, engine)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _reset_clock() -> Iterator[None]:
    yield
    set_clock(None)


@pytest.fixture
def make_tenant_user(client: TestClient) -> TenantUserFactory:
    """Factory: bootstrap a new tenant + admin, return TenantUser with auth headers."""
    counter = {"n": 0}

    def factory(name: str | None = None) -> TenantUser:
        counter["n"] += 1
        n = counter["n"]
        email = f"admin{n}-{uuid.uuid4().hex[:6]}@example.test"
        resp = client.post(
            "/v1/tenants",
            json={
                "tenant_name": name or f"Tenant {n}",
                "admin_email": email,
                "admin_password": TEST_PASSWORD,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        token = body["token"]["access_token"]
        return TenantUser(
            tenant_id=uuid.UUID(body["tenant_id"]),
            user_id=uuid.UUID(body["user_id"]),
            email=email,
            password=TEST_PASSWORD,
            token=token,
            headers={"Authorization": f"Bearer {token}"},
        )

    return factory


@pytest.fixture
def two_tenants(make_tenant_user: TenantUserFactory) -> tuple[TenantUser, TenantUser]:
    """Two fully separate tenants (A, B), each with an admin."""
    return make_tenant_user("Tenant A"), make_tenant_user("Tenant B")


@pytest.fixture
def tenant_row(session: Session) -> Tenant:
    tenant = Tenant(name="Direct DB Tenant")
    session.add(tenant)
    session.commit()
    return tenant
