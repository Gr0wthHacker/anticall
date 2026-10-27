"""PRD v3 opt-out outbound path is retired (ADR 0006): 410 on HTTP, rest of Resolve intact."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from callshield.agent.tools import TOOL_NAMES
from callshield.core.config import Settings

from ..conftest import TenantUserFactory
from ..resolve_support import BASE, build_env, create_case, verified_destination


def test_start_contact_and_opt_out_authorizations_return_410_with_pointers(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = build_env(client, engine, settings, make_tenant_user())
    case_id = create_case(env)
    did = verified_destination(env, case_id)
    for path in ("start-contact", "contact-authorizations"):
        resp = client.post(
            f"{BASE}/{case_id}/{path}",
            json={"destination_id": did},
            headers={**env.h, "Idempotency-Key": "k-410"},
        )
        assert resp.status_code == 410, (path, resp.text)
        error = resp.json()["error"]
        assert error["code"] == "OUTBOUND_OPT_OUT_REMOVED"
        assert "callback" in error["message"].lower() and "complaint" in error["message"].lower()
        assert error["details"]["callback_api"] == "/v1/calls/{id}/callbacks"
        assert error["details"]["complaint_flow"] == "/v1/cases"
    assert env.dialer.calls_placed == 0


def test_410_never_leaks_other_tenants_cases_and_still_requires_auth(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = build_env(client, engine, settings, make_tenant_user())
    case_id = create_case(env)
    intruder = make_tenant_user("Intruder")
    for path in ("start-contact", "contact-authorizations"):
        denied = client.post(f"{BASE}/{case_id}/{path}", json={}, headers=intruder.headers)
        assert denied.status_code == 404
        assert client.post(f"{BASE}/{case_id}/{path}", json={}).status_code == 401
        missing = client.post(f"{BASE}/{uuid.uuid4()}/{path}", json={}, headers=env.h)
        assert missing.status_code == 404


def test_the_rest_of_resolve_keeps_working(
    client: TestClient, engine: Engine, settings: Settings, make_tenant_user: TenantUserFactory
) -> None:
    env = build_env(client, engine, settings, make_tenant_user())
    case_id = create_case(env)
    did = verified_destination(env, case_id)
    assert env.get(BASE).status_code == 200
    assert env.get(f"{BASE}/{case_id}").status_code == 200
    assert env.get(f"{BASE}/{case_id}/timeline").status_code == 200
    assert env.get(f"{BASE}/{case_id}/next-steps").status_code == 200
    manual = env.post(
        f"{BASE}/{case_id}/attempts/manual",
        {"destination_id": did, "outcome": "NO_ANSWER"},
        "manual-1",
    )
    assert manual.status_code == 201, manual.text


def test_ai_tool_registry_is_still_exactly_eight_tools_without_dial() -> None:
    assert len(TOOL_NAMES) == 8
    assert not [name for name in TOOL_NAMES if any(w in name for w in ("dial", "call", "schedule"))]
    from callshield.callback import dialogue

    assert not hasattr(dialogue, "ToolCall") and not hasattr(dialogue, "AgentToolRegistry")
