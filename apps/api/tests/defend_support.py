"""Shared helpers/fixtures for DEFEND tests. Import the fixtures you need into a test module
and add `pytestmark = pytest.mark.usefixtures("defend_reset")`. Fictional numbers only."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, select

from callshield.core.config import Settings
from callshield.db.base import Base
from callshield.db.session import make_session_factory
from callshield.defend.ingest import set_llm_client
from callshield.defend.notify import clear_notification_hooks
from callshield.defend.verification import InMemoryOutboxChannel, set_challenge_channel
from callshield.policy.jurisdiction import (
    JurisdictionEntry,
    JurisdictionPolicy,
    set_policy_override,
)
from callshield.telephony.mock import (
    FICTIONAL_CALLER,
    FICTIONAL_LINE,
    FICTIONAL_USER,
    inbound_call_payload,
    signed_request,
)
from callshield.telephony.registry import reset_adapters
from tests.conftest import TenantUser

__all__ = [
    "FICTIONAL_CALLER",
    "FICTIONAL_LINE",
    "FICTIONAL_USER",
    "LineHandle",
    "defend_reset",
    "enable_live_processing",
    "enroll_line",
    "inbound_call_payload",
    "post_webhook",
    "rows",
    "screening_settings",
]

OTHER_LINE = "+14155550102"
TRUSTED = "+14155550111"
UNWANTED = "+14155550122"


@dataclass
class LineHandle:
    id: uuid.UUID
    e164: str
    line_key: str
    secret: str
    provider: str
    user: TenantUser


def permissive_policy() -> JurisdictionPolicy:
    entry = JurisdictionEntry("US-TEST", "PERMITTED_WITH_CONSENT", False, True)
    all_party = JurisdictionEntry("US-ALLPARTY", "PERMITTED_WITH_CONSENT", True, True)
    return JurisdictionPolicy("test-policy", {"US-TEST": entry, "US-ALLPARTY": all_party})


@pytest.fixture
def defend_reset() -> Iterator[InMemoryOutboxChannel]:
    reset_adapters()
    clear_notification_hooks()
    set_llm_client(None)
    set_policy_override(permissive_policy())
    channel = InMemoryOutboxChannel()
    set_challenge_channel(channel)
    yield channel
    reset_adapters()
    clear_notification_hooks()
    set_llm_client(None)
    set_policy_override(None)
    set_challenge_channel(None)


@pytest.fixture
def screening_settings() -> Settings:
    return Settings(
        db_url="sqlite://", environment="test", allow_bootstrap=True, live_stt_enabled=True
    )


def enroll_line(
    client: TestClient,
    user: TenantUser,
    channel: InMemoryOutboxChannel,
    e164: str = FICTIONAL_LINE,
    provider: str = "mock",
    routing_config: dict[str, Any] | None = None,
    verify: bool = True,
) -> LineHandle:
    config = {"user_number": FICTIONAL_USER, **(routing_config or {})}
    resp = client.post(
        "/v1/lines",
        headers=user.headers,
        json={"e164": e164, "label": "test", "provider": provider, "routing_config": config},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    line_id = uuid.UUID(body["id"])
    if verify:
        assert (
            client.post(f"/v1/lines/{line_id}/verify", headers=user.headers, json={}).status_code
            == 200
        )
        code = channel.last_code(line_id)
        assert code is not None
        done = client.post(f"/v1/lines/{line_id}/verify", headers=user.headers, json={"code": code})
        assert done.status_code == 200, done.text
    return LineHandle(
        id=line_id,
        e164=e164,
        line_key=body["webhook"]["line_key"],
        secret=body["webhook"]["secret"],
        provider=provider,
        user=user,
    )


_nonce_counter = {"n": 0}


def fresh_nonce() -> str:
    _nonce_counter["n"] += 1
    return f"nonce-{uuid.uuid4().hex[:12]}-{_nonce_counter['n']}"


def post_webhook(
    client: TestClient,
    line: LineHandle,
    payload: dict[str, Any] | bytes,
    nonce: str | None = None,
    secret: str | None = None,
    timestamp: int | None = None,
    line_key: str | None = None,
    provider: str | None = None,
) -> Response:
    body, headers = signed_request(
        secret if secret is not None else line.secret, payload, nonce or fresh_nonce(), timestamp
    )
    path = f"/v1/webhooks/{provider or line.provider}/{line_key or line.line_key}"
    resp: Response = client.post(path, content=body, headers=headers)
    return resp


def enable_live_processing(
    client: TestClient,
    line: LineHandle,
    jurisdiction: str = "US-TEST",
    mode: str = "LIVE_STT_TRANSIENT",
    **basis: Any,
) -> None:
    patch = client.patch(
        f"/v1/lines/{line.id}",
        headers=line.user.headers,
        json={"routing_config": {"jurisdiction": jurisdiction}},
    )
    assert patch.status_code == 200, patch.text
    consent = client.post(
        f"/v1/lines/{line.id}/consent",
        headers=line.user.headers,
        json={"processing_mode": mode, "authorized": True, **basis},
    )
    assert consent.status_code == 201, consent.text


def rows(engine: Engine, model: type[Base], **filters: Any) -> list[Any]:
    """Fresh-session query so assertions never see stale identity-map state."""
    with make_session_factory(engine)() as sess:  # type: Session
        stmt = select(model)
        for key, value in filters.items():
            stmt = stmt.where(getattr(model, key) == value)
        return list(sess.execute(stmt).scalars().all())


def dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()


def response_ops(resp: Response) -> list[str]:
    return [i["op"] for i in resp.json()["instructions"]]


def error_code(resp: Response) -> str:
    body = resp.json()
    return str(body.get("error", {}).get("code", body))
