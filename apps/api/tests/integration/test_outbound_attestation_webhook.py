# ruff: noqa: F811
"""Provider-authenticated outbound attestation feed: signature, replay, tenancy, blocking."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, select

from callshield.callback.lines import caller_id_problem
from callshield.db.models.callback_ext import LineOutboundAttestation
from callshield.db.models.defend import ProtectedLine
from callshield.db.session import make_session_factory
from callshield.defend.verification import InMemoryOutboxChannel
from callshield.telephony.mock import signed_request
from tests.conftest import TenantUser, TenantUserFactory
from tests.defend_support import (
    FICTIONAL_LINE,
    LineHandle,
    defend_reset,  # noqa: F401
    enroll_line,
    fresh_nonce,
)

pytestmark = pytest.mark.usefixtures("defend_reset")

OTHER_LINE = "+14155550102"


@pytest.fixture
def owner(make_tenant_user: TenantUserFactory) -> TenantUser:
    return make_tenant_user("Owner")


@pytest.fixture
def line(client: TestClient, owner: TenantUser, defend_reset: InMemoryOutboxChannel) -> LineHandle:
    return enroll_line(client, owner, defend_reset)


def report(
    client: TestClient,
    line: LineHandle,
    payload: dict[str, Any] | bytes,
    *,
    nonce: str | None = None,
    secret: str | None = None,
    timestamp: int | None = None,
    line_key: str | None = None,
) -> Response:
    body, headers = signed_request(
        secret if secret is not None else line.secret, payload, nonce or fresh_nonce(), timestamp
    )
    path = f"/v1/webhooks/{line.provider}/{line_key or line.line_key}/outbound-attestation"
    resp: Response = client.post(path, content=body, headers=headers)
    return resp


def stored_levels(engine: Engine) -> list[tuple[str, str]]:
    with make_session_factory(engine)() as s:
        rows = s.execute(select(LineOutboundAttestation)).scalars().all()
        return [(str(r.tenant_id), r.level) for r in rows]


def test_signed_report_records_level_for_the_verified_line(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    resp = report(client, line, {"level": "A"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "recorded", "level": "A"}
    assert stored_levels(engine) == [(str(line.user.tenant_id), "A")]
    with make_session_factory(engine)() as s:
        row = s.execute(select(LineOutboundAttestation)).scalar_one()
        assert row.source == "provider:mock" and row.line_id == line.id


@pytest.mark.parametrize(
    ("reported", "stored"),
    [("B", "B"), ("c", "C"), ("UNKNOWN", "UNKNOWN"), ("NONE", "UNKNOWN"), ("a+", "UNKNOWN")],
)
def test_non_a_levels_stay_blocking(
    client: TestClient, line: LineHandle, engine: Engine, reported: str, stored: str
) -> None:
    assert report(client, line, {"level": reported}).status_code == 200
    assert stored_levels(engine) == [(str(line.user.tenant_id), stored)]
    with make_session_factory(engine)() as s:
        row = s.get(ProtectedLine, line.id)
        assert row is not None
        assert caller_id_problem(s, line.user.tenant_id, row) in {
            "OWNERSHIP_CHECK_MISSING",
            "OUTBOUND_ATTESTATION_NOT_A",
        }


def test_later_report_can_downgrade_a(client: TestClient, line: LineHandle, engine: Engine) -> None:
    assert report(client, line, {"level": "A"}).status_code == 200
    assert report(client, line, {"level": "B"}).status_code == 200
    assert stored_levels(engine) == [(str(line.user.tenant_id), "B")]


def test_forged_signature_is_rejected_and_stores_nothing(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    resp = report(client, line, {"level": "A"}, secret="not-the-line-secret")
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "WEBHOOK_REJECTED"
    body, headers = signed_request(line.secret, {"level": "A"}, fresh_nonce())
    tampered = body.replace(b'"A"', b'"B"')
    path = f"/v1/webhooks/{line.provider}/{line.line_key}/outbound-attestation"
    assert client.post(path, content=tampered, headers=headers).status_code == 401
    unsigned = client.post(path, content=body)
    assert unsigned.status_code == 401
    assert stored_levels(engine) == []


def test_stale_timestamp_is_rejected(client: TestClient, line: LineHandle, engine: Engine) -> None:
    from callshield.core.clock import utcnow

    old = int(utcnow().timestamp()) - 3600
    assert report(client, line, {"level": "A"}, timestamp=old).status_code == 401
    assert stored_levels(engine) == []


def test_replayed_nonce_is_rejected(client: TestClient, line: LineHandle, engine: Engine) -> None:
    assert report(client, line, {"level": "B"}, nonce="replay-nonce-0001").status_code == 200
    again = report(client, line, {"level": "A"}, nonce="replay-nonce-0001")
    assert again.status_code == 409 and again.json()["error"]["code"] == "WEBHOOK_REPLAY"
    assert stored_levels(engine) == [(str(line.user.tenant_id), "B")]


def test_nonce_is_shared_with_the_call_event_webhook(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    from tests.defend_support import post_webhook

    first = post_webhook(client, line, {"event": "x"}, nonce="shared-nonce-0001")
    assert first.status_code != 409
    again = report(client, line, {"level": "A"}, nonce="shared-nonce-0001")
    assert again.status_code == 409
    assert stored_levels(engine) == []


def test_tenant_comes_only_from_the_verified_line(
    client: TestClient,
    line: LineHandle,
    engine: Engine,
    make_tenant_user: TenantUserFactory,
    defend_reset: InMemoryOutboxChannel,
) -> None:
    other = enroll_line(client, make_tenant_user("Other"), defend_reset, e164=OTHER_LINE)
    # A's secret against B's line key (and vice versa) never crosses tenants.
    assert report(client, line, {"level": "A"}, line_key=other.line_key).status_code == 401
    assert report(client, other, {"level": "A"}, line_key=line.line_key).status_code == 401
    assert stored_levels(engine) == []
    # A payload cannot name a tenant or line.
    for extra in ({"tenant_id": str(other.user.tenant_id)}, {"line_id": str(other.id)}):
        resp = report(client, line, {"level": "A", **extra})
        assert resp.status_code == 400 and resp.json()["error"]["code"] == "MALFORMED_EVENT"
    assert report(client, line, {"level": "A"}).status_code == 200
    assert stored_levels(engine) == [(str(line.user.tenant_id), "A")]


def test_malformed_and_unknown_provider_and_unknown_key(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    assert report(client, line, b"not json").status_code == 400
    assert report(client, line, {"level": ""}).status_code == 400
    assert report(client, line, {}).status_code == 400
    assert report(client, line, {"level": "A"}, line_key="nope-nope").status_code == 401
    body, headers = signed_request(line.secret, {"level": "A"}, fresh_nonce())
    bad = client.post(
        f"/v1/webhooks/no-such-provider/{line.line_key}/outbound-attestation",
        content=body,
        headers=headers,
    )
    assert bad.status_code == 404
    assert stored_levels(engine) == []


def test_unverified_line_cannot_report(
    client: TestClient,
    owner: TenantUser,
    engine: Engine,
    defend_reset: InMemoryOutboxChannel,
) -> None:
    pending = enroll_line(client, owner, defend_reset, e164=FICTIONAL_LINE, verify=False)
    resp = report(client, pending, {"level": "A"})
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "LINE_NOT_VERIFIED"
    assert stored_levels(engine) == []


def test_users_cannot_post_here_with_a_bearer_token(
    client: TestClient, line: LineHandle, engine: Engine
) -> None:
    path = f"/v1/webhooks/{line.provider}/{line.line_key}/outbound-attestation"
    resp = client.post(path, json={"level": "A"}, headers=line.user.headers)
    assert resp.status_code == 401
    assert stored_levels(engine) == []
