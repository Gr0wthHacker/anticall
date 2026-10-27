"""Fixtures for the callback integration tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from callshield.auth.tokens import Principal, create_access_token
from callshield.callback.runtime import set_runtime
from callshield.core.config import Settings
from callshield.db.models import User
from callshield.db.session import make_session_factory
from callshield.domain.enums import Role

from ..callback_support import CbEnv, build_cb_env
from ..conftest import TenantUserFactory


@pytest.fixture
def cb(
    settings: Settings,
    client: TestClient,
    engine: Engine,
    make_tenant_user: TenantUserFactory,
    tmp_path: Path,
) -> Iterator[CbEnv]:
    """Callback env with a frozen clock. `cb.spare` is a second tenant whose token was minted
    before the clock froze (JWT iat is checked against the real clock)."""
    user, spare = make_tenant_user("Callback Tenant"), make_tenant_user("Spare Tenant")
    with make_session_factory(engine)() as s:  # an instance operator in the same tenant
        operator = User(
            tenant_id=user.tenant_id,
            email=f"operator-{uuid.uuid4().hex[:6]}@example.test",
            role=Role.OPERATOR.value,
        )
        s.add(operator)
        s.commit()
        token, _ = create_access_token(
            settings, Principal(user.tenant_id, operator.id, Role.OPERATOR)
        )
    env = build_cb_env(
        client,
        engine,
        user,
        tmp_path,
        spare=spare,
        operator_headers={"Authorization": f"Bearer {token}"},
    )
    yield env
    set_runtime(None)
