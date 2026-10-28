"""The quickstart helper script works against the real app (in-process, no network)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import TenantUserFactory

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "synthetic_call.py"


def test_synthetic_call_script_ingests_one_call(
    client: TestClient, make_tenant_user: TenantUserFactory
) -> None:
    spec = importlib.util.spec_from_file_location("synthetic_call", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    user = make_tenant_user("Quickstart")
    result = module.run_synthetic_call(client, user.email, user.password, in_process=True)
    assert result["call_id"]
    calls = client.get("/v1/calls", headers=user.headers).json()
    assert calls["total"] == 1
