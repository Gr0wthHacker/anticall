"""Shared helpers for RESOLVE tests (not a test module)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine

from callshield.core.clock import FrozenClock, set_clock
from callshield.core.config import Settings
from callshield.db.models import ConsentRecord, ProtectedLine
from callshield.db.session import make_session_factory
from callshield.resolve.deps import get_dialer
from callshield.resolve.dialer import MockDialer

from .conftest import TenantUser

COMPANY_PHONE = "+14155550142"
BASE = "/v1/resolution-cases"


@dataclass
class Env:
    client: TestClient
    engine: Engine
    settings: Settings
    user: TenantUser
    dialer: MockDialer
    line_id: uuid.UUID
    clock: FrozenClock
    counter: int = field(default=0)

    @property
    def h(self) -> dict[str, str]:
        return self.user.headers

    def key(self, label: str = "k") -> str:
        self.counter += 1
        return f"{label}-{self.counter}-{uuid.uuid4().hex[:6]}"

    def post(self, path: str, body: dict[str, Any], key: str | None = None) -> Any:
        if path.endswith(("/start-contact", "/contact-authorizations")):
            # Retired HTTP routes (410): drive the reusable libraries directly instead.
            from .resolve_legacy import legacy_authorize, legacy_start

            case_id = path.rsplit("/", 2)[-2]
            handler = legacy_start if path.endswith("/start-contact") else legacy_authorize
            return handler(self, case_id, body, key)
        headers = dict(self.h)
        if key:
            headers["Idempotency-Key"] = key
        resp: Response = self.client.post(path, json=body, headers=headers)
        return resp

    def get(self, path: str) -> Response:
        resp: Response = self.client.get(path, headers=self.h)
        return resp


def add_line(engine: Engine, tenant_id: uuid.UUID, *, verified: bool = True) -> uuid.UUID:
    with make_session_factory(engine)() as s:
        line = ProtectedLine(
            tenant_id=tenant_id,
            e164="+14155550123",
            verification_status="VERIFIED" if verified else "UNVERIFIED",
        )
        s.add(line)
        s.commit()
        return line.id


def build_env(
    client: TestClient,
    engine: Engine,
    settings: Settings,
    user: TenantUser,
    scenario: str = "ivr_agent_ack",
    *,
    outbound: bool = True,
) -> Env:
    settings.outbound_enabled = outbound
    clock = FrozenClock(datetime.now(UTC))
    set_clock(clock)
    dialer = MockDialer(scenario)
    app = client.app
    assert isinstance(app, FastAPI)
    app.dependency_overrides[get_dialer] = lambda: dialer
    return Env(client, engine, settings, user, dialer, add_line(engine, user.tenant_id), clock)


def create_case(env: Env, **overrides: Any) -> str:
    body: dict[str, Any] = {
        "line_id": str(env.line_id),
        "claimed_entity": "Acme Lending (as stated by caller)",
        "objective": "stop solicitation calls",
        "summary": "Repeated loan solicitation calls",
        "policy_region": "US-CA",
    }
    body.update(overrides)
    resp = env.post(BASE, body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def verified_destination(env: Env, case_id: str, phone: str = COMPANY_PHONE) -> str:
    """Add an unverified destination, then verify it with evidence. Returns the VERIFIED row id."""
    resp = env.post(f"{BASE}/{case_id}/destinations", {"phone": phone})
    assert resp.status_code == 201, resp.text
    did = resp.json()["id"]
    ver = env.post(
        f"{BASE}/{case_id}/destinations/{did}/verify",
        {
            "evidence_kind": "OFFICIAL_WEBSITE",
            "reference": "https://acme-lending.example/compliance",
            "confirms_entity": True,
        },
    )
    assert ver.status_code == 200, ver.text
    return str(ver.json()["id"])


def approve_path(env: Env, case_id: str, did: str, digit: str = "2") -> None:
    resp = env.post(
        f"{BASE}/{case_id}/destinations/{did}/ivr-paths",
        {"steps": [{"digit": digit, "expect_keywords": ["agent"]}], "timeout_seconds": 60},
    )
    assert resp.status_code == 201, resp.text


def authorize(env: Env, case_id: str, did: str, key: str | None = None, **extra: Any) -> Any:
    body = {"destination_id": did, **extra}
    return env.post(f"{BASE}/{case_id}/contact-authorizations", body, key or env.key("auth"))


def start(
    env: Env,
    case_id: str,
    did: str,
    auth_id: str | None,
    key: str | None = None,
    mode: str = "AI",
) -> Any:
    body: dict[str, Any] = {"destination_id": did, "mode": mode}
    if auth_id:
        body["authorization_id"] = auth_id
    return env.post(f"{BASE}/{case_id}/start-contact", body, key or env.key("start"))


def ready(env: Env, **case_overrides: Any) -> tuple[str, str]:
    """Case + verified destination + approved IVR path."""
    case_id = create_case(env, **case_overrides)
    did = verified_destination(env, case_id)
    approve_path(env, case_id, did)
    return case_id, did


def authorize_and_start(env: Env, case_id: str, did: str, mode: str = "AI") -> Any:
    auth = authorize(env, case_id, did)
    assert auth.status_code == 201, auth.text
    return start(env, case_id, did, auth.json()["id"], mode=mode)


def enable_ai(env: Env, *, consent: bool = True, mode: str = "LIVE_STT_TRANSIENT") -> None:
    """Turn the AI gate on: settings flag plus the line's processing consent."""
    env.settings.ai_outbound_enabled = True
    if consent:
        with make_session_factory(env.engine)() as s:
            s.add(
                ConsentRecord(
                    tenant_id=env.user.tenant_id,
                    user_id=env.user.user_id,
                    processing_mode=mode,
                    legal_basis_config={"line_id": str(env.line_id)},
                    authorized=True,
                    authorized_at=datetime.now(UTC),
                )
            )
            s.commit()
