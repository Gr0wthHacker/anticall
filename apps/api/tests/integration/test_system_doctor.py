"""GET /v1/system/doctor: admin-only, secrets redacted, reuses the CLI check engine."""

from __future__ import annotations

from fastapi.testclient import TestClient

from callshield.api.routers.system_doctor import get_doctor_deps
from callshield.doctor.model import DbStatus
from tests.conftest import TenantUserFactory
from tests.doctor_support import fake_deps


def test_requires_authentication(client: TestClient) -> None:
    assert client.get("/v1/system/doctor").status_code == 401


def test_admin_gets_report_with_gated_features(
    client: TestClient, make_tenant_user: TenantUserFactory
) -> None:
    admin = make_tenant_user("Doctor Tenant")
    client.app.dependency_overrides[get_doctor_deps] = lambda: fake_deps()  # type: ignore[attr-defined]
    resp = client.get("/v1/system/doctor", headers=admin.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {"ok", "counts", "checks", "gated_features", "docs"} <= set(body)
    assert all(not f["enabled"] for f in body["gated_features"])
    assert {c["id"] for c in body["checks"]} >= {"database", "secrets", "risky"}
    text = resp.text
    assert "dev-insecure" not in text


def test_failing_database_check_is_reported(
    client: TestClient, make_tenant_user: TenantUserFactory
) -> None:
    admin = make_tenant_user("Doctor Tenant 2")
    down = fake_deps(db=DbStatus(False, None, "OperationalError"))
    client.app.dependency_overrides[get_doctor_deps] = lambda: down  # type: ignore[attr-defined]
    body = client.get("/v1/system/doctor", headers=admin.headers).json()
    assert body["ok"] is False
    assert any(c["id"] == "database" and c["status"] == "FAIL" for c in body["checks"])


def test_non_admin_is_forbidden(client: TestClient, make_tenant_user: TenantUserFactory) -> None:
    admin = make_tenant_user("Doctor Tenant 3")
    created = client.post(
        "/v1/users",
        headers=admin.headers,
        json={
            "email": "member@example.test",
            "password": "member-password-123",
            "role": "CONSUMER_SUBSCRIBER",
        },
    )
    assert created.status_code == 201
    login = client.post(
        "/v1/auth/login",
        json={"email": "member@example.test", "password": "member-password-123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/v1/system/doctor", headers=headers).status_code == 403


def test_real_engine_check_runs_without_overrides(
    client: TestClient, make_tenant_user: TenantUserFactory
) -> None:
    admin = make_tenant_user("Doctor Tenant 4")
    body = client.get("/v1/system/doctor", headers=admin.headers).json()
    db = next(c for c in body["checks"] if c["id"] in ("database", "migrations"))
    assert db["status"] in ("PASS", "WARN", "FAIL")
