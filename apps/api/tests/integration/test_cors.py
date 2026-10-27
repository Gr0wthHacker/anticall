"""Configurable CORS: off by default, explicit origins only, never wildcard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from callshield.api.app import create_app
from callshield.core.config import Settings

ALLOWED = "https://app.example.test"
OTHER = "https://evil.example.test"


def _client(engine: Engine, origins: list[str]) -> TestClient:
    settings = Settings(db_url="sqlite://", environment="test", cors_allow_origins=origins)
    return TestClient(create_app(settings, engine), raise_server_exceptions=False)


def _preflight(client: TestClient, origin: str) -> dict[str, str]:
    resp = client.options(
        "/v1/meta/capabilities",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    return {k.lower(): v for k, v in resp.headers.items()}


def test_default_is_no_cors(engine: Engine) -> None:
    assert Settings().cors_allow_origins == []
    client = _client(engine, [])
    resp = client.get("/v1/meta/capabilities", headers={"Origin": ALLOWED})
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}
    assert "access-control-allow-origin" not in _preflight(client, ALLOWED)


def test_allowed_origin_gets_headers_without_credentials(engine: Engine) -> None:
    client = _client(engine, [ALLOWED + "/"])  # trailing slash is normalised
    headers = _preflight(client, ALLOWED)
    assert headers["access-control-allow-origin"] == ALLOWED
    assert "access-control-allow-credentials" not in headers
    assert "authorization" in headers["access-control-allow-headers"].lower()
    simple = client.get("/v1/meta/capabilities", headers={"Origin": ALLOWED})
    assert simple.headers["access-control-allow-origin"] == ALLOWED
    assert "x-request-id" in simple.headers["access-control-expose-headers"].lower()


def test_other_origin_is_not_allowed(engine: Engine) -> None:
    client = _client(engine, [ALLOWED])
    assert "access-control-allow-origin" not in _preflight(client, OTHER)
    resp = client.get("/v1/meta/capabilities", headers={"Origin": OTHER})
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_wildcard_is_refused() -> None:
    with pytest.raises(ValueError, match="never"):
        Settings(cors_allow_origins=["*"])
    with pytest.raises(ValueError, match="never"):
        Settings(cors_allow_origins=[ALLOWED, " * "])


def test_env_var_is_a_json_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLSHIELD_CORS_ALLOW_ORIGINS", f'["{ALLOWED}", "http://localhost:3000"]')
    assert Settings().cors_allow_origins == [ALLOWED, "http://localhost:3000"]
