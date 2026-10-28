# ruff: noqa: F811
"""ARI events through the shared authenticated ingest tail: tenancy, idempotency, health."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from callshield.core.config import Settings
from callshield.db.models.defend import CallerObservation, CallSession, Incident
from callshield.db.models.defend_ext import LineEndpoint, TelephonyHealth
from callshield.db.models.platform import Job, LedgerEvent
from callshield.db.session import make_session_factory
from callshield.defend.verification import InMemoryOutboxChannel
from callshield.telephony.asterisk.ari_client import STATUS_DEGRADED, STATUS_OK, AriHealth
from callshield.telephony.asterisk.events import (
    AriEventIngestor,
    IngestResult,
    build_attestation,
    map_ari_event,
)
from callshield.telephony.asterisk.health import write_link_health
from tests.asterisk_support import CALLER_E164, ari_start
from tests.conftest import TenantUser, TenantUserFactory
from tests.defend_support import (
    defend_reset,  # noqa: F401
    enroll_line,
    rows,
)

pytestmark = pytest.mark.usefixtures("defend_reset")
OTHER_NUMBER = "+14155550102"


@pytest.fixture
def ingestor(engine: Engine, settings: Settings) -> AriEventIngestor:
    return AriEventIngestor(make_session_factory(engine), settings)


def send(ingestor: AriEventIngestor, data: dict[str, Any], **attest: Any) -> IngestResult:
    event = map_ari_event(data, attest if attest else build_attestation(*([None] * 4)))
    assert event is not None
    return ingestor.ingest(event)


def set_endpoint(engine: Engine, line_id: Any, endpoint: str) -> None:
    factory = make_session_factory(engine)
    with factory() as session:
        row = session.execute(
            select(LineEndpoint).where(LineEndpoint.line_id == line_id)
        ).scalar_one()
        row.endpoint_id = endpoint
        session.commit()


@pytest.fixture
def two_lines(
    client: TestClient, make_tenant_user: TenantUserFactory, defend_reset: InMemoryOutboxChannel
) -> tuple[Any, Any]:
    a: TenantUser = make_tenant_user("A")
    b: TenantUser = make_tenant_user("B")
    return (
        enroll_line(client, a, defend_reset, provider="ari"),
        enroll_line(client, b, defend_reset, e164=OTHER_NUMBER, provider="ari"),
    )


def test_inbound_call_creates_incident_ledger_and_job_once(
    ingestor: AriEventIngestor, two_lines: tuple[Any, Any], engine: Engine
) -> None:
    line_a, _ = two_lines
    data = ari_start("chan-1", to=line_a.e164)
    first = send(ingestor, data)
    assert first.status == "accepted" and first.outcome is not None
    assert [i.op for i in first.outcome.instructions] == ["voicemail"]  # no consent: no screening
    again = send(ingestor, data)  # redelivered by ARI / a reconnect
    assert again.status == "duplicate"
    assert len(rows(engine, CallSession, provider="ari")) == 1
    assert len(rows(engine, Incident)) == 1
    assert len(rows(engine, LedgerEvent, topic="call.received")) == 1
    assert len([j for j in rows(engine, Job) if j.kind == "incident.created"]) == 1


def test_tenant_comes_only_from_the_matched_line(
    ingestor: AriEventIngestor, two_lines: tuple[Any, Any], engine: Engine
) -> None:
    line_a, line_b = two_lines
    data = ari_start("chan-b", to=line_b.e164)
    data["tenant_id"] = str(line_a.user.tenant_id)  # payload claims tenant A: ignored
    data["channel"]["tenant_id"] = str(line_a.user.tenant_id)
    result = send(ingestor, data)
    assert result.tenant_id == line_b.user.tenant_id and result.line_id == line_b.id
    (call,) = rows(engine, CallSession, provider="ari")
    assert call.tenant_id == line_b.user.tenant_id and call.line_id == line_b.id
    assert rows(engine, Incident)[0].tenant_id == line_b.user.tenant_id


def test_unknown_number_matches_nothing_and_persists_nothing(
    ingestor: AriEventIngestor, two_lines: tuple[Any, Any], engine: Engine
) -> None:
    result = send(ingestor, ari_start("chan-x", to="+14155550177"))
    assert (result.status, result.reason) == ("ignored", "NO_UNIQUE_LINE")
    assert not rows(engine, CallSession) and not rows(engine, Incident)


def test_later_events_resolve_through_the_stored_call_only(
    ingestor: AriEventIngestor, two_lines: tuple[Any, Any], engine: Engine
) -> None:
    line_a, line_b = two_lines
    send(ingestor, ari_start("chan-a", to=line_a.e164))
    send(ingestor, ari_start("chan-b", to=line_b.e164))
    end = {"type": "ChannelDestroyed", "timestamp": "2026-10-27T12:01:00.000+0000"}
    end["channel"] = {"id": "chan-a"}  # type: ignore[assignment]
    result = send(ingestor, end)
    assert result.status == "accepted" and result.tenant_id == line_a.user.tenant_id
    calls = {c.provider_call_id: c for c in rows(engine, CallSession)}
    assert calls["chan-a"].ended_at is not None and calls["chan-b"].ended_at is None
    ghost = {"type": "ChannelDestroyed", "channel": {"id": "never-seen"}}
    assert send(ingestor, ghost).status in ("ignored", "rejected")


def test_endpoint_mismatch_is_rejected_not_rerouted(
    ingestor: AriEventIngestor, two_lines: tuple[Any, Any], engine: Engine
) -> None:
    line_a, _ = two_lines
    set_endpoint(engine, line_a.id, "trunk")
    bad = send(ingestor, ari_start("chan-e", to=line_a.e164, trunk="evil-trunk"))
    assert (bad.status, bad.reason) == ("rejected", "LINE_MISMATCH")
    assert not rows(engine, CallSession)
    assert send(ingestor, ari_start("chan-e", to=line_a.e164, trunk="trunk")).status == "accepted"


def test_unverified_line_is_rejected(
    client: TestClient,
    make_tenant_user: TenantUserFactory,
    defend_reset: InMemoryOutboxChannel,
    ingestor: AriEventIngestor,
    engine: Engine,
) -> None:
    pending = enroll_line(client, make_tenant_user("P"), defend_reset, provider="ari", verify=False)
    result = send(ingestor, ari_start("chan-p", to=pending.e164))
    assert (result.status, result.reason) == ("rejected", "LINE_NOT_VERIFIED")
    assert not rows(engine, CallSession)


def test_attestation_level_is_stored_only_when_verified(
    ingestor: AriEventIngestor, two_lines: tuple[Any, Any], engine: Engine
) -> None:
    line_a, line_b = two_lines
    verified = build_attestation(None, "A", None, "TN-Validation-Passed-A")
    forged = build_attestation(None, "A", None, None)
    send(ingestor, ari_start("chan-v", to=line_a.e164), **verified)
    send(ingestor, ari_start("chan-f", to=line_b.e164), **forged)
    levels = {o.attestation_metadata["attestation_level"] for o in rows(engine, CallerObservation)}
    assert levels == {"A", "NONE"}
    by_caller = {o.displayed_caller_id for o in rows(engine, CallerObservation)}
    assert by_caller == {CALLER_E164}


def test_registration_events_degrade_only_the_matching_tenants(
    ingestor: AriEventIngestor, two_lines: tuple[Any, Any], engine: Engine
) -> None:
    line_a, line_b = two_lines
    set_endpoint(engine, line_a.id, "trunk")
    set_endpoint(engine, line_b.id, "other-trunk")
    down = {
        "type": "PeerStatusChange",
        "timestamp": "2026-10-27T12:00:01.000+0000",
        "endpoint": {"resource": "trunk"},
        "peer": {"peer_status": "Unreachable"},
    }
    assert send(ingestor, down).status == "accepted"
    health = {h.tenant_id: h.status for h in rows(engine, TelephonyHealth, provider="ari")}
    assert health == {line_a.user.tenant_id: "DEGRADED"}
    up = {
        "type": "PeerStatusChange",
        "timestamp": "2026-10-27T12:00:02.000+0000",
        "endpoint": {"resource": "trunk"},
        "peer": {"peer_status": "Reachable"},
    }
    send(ingestor, up)
    assert rows(engine, TelephonyHealth, provider="ari")[0].status == "OK"
    unmatched = {
        "type": "PeerStatusChange",
        "endpoint": {"resource": "nobody"},
        "peer": {"peer_status": "Unreachable"},
    }
    assert send(ingestor, unmatched).reason == "NO_LINE_FOR_ENDPOINT"


def test_link_health_is_published_per_ari_tenant_and_keeps_registration_failures(
    ingestor: AriEventIngestor,
    two_lines: tuple[Any, Any],
    engine: Engine,
    client: TestClient,
) -> None:
    line_a, line_b = two_lines
    factory = make_session_factory(engine)
    assert write_link_health(factory, AriHealth(STATUS_DEGRADED, "ari_unreachable")) == 2
    states = {(h.status, h.detail) for h in rows(engine, TelephonyHealth, provider="ari")}
    assert states == {("DEGRADED", "ari_unreachable")}
    assert write_link_health(factory, AriHealth(STATUS_OK, "connected", True)) == 2
    assert {h.status for h in rows(engine, TelephonyHealth, provider="ari")} == {"OK"}
    set_endpoint(engine, line_a.id, "trunk")
    down = {
        "type": "PeerStatusChange",
        "timestamp": "2026-10-27T12:00:09.000+0000",
        "endpoint": {"resource": "trunk"},
        "peer": {"peer_status": "Unreachable"},
    }
    send(ingestor, down)
    assert write_link_health(factory, AriHealth(STATUS_OK, "connected", True)) == 1  # skips A
    resp = client.get("/v1/health/telephony", headers=line_a.user.headers)
    assert resp.status_code == 200 and resp.json()["overall"] == "DEGRADED"
    other = client.get("/v1/health/telephony", headers=line_b.user.headers)
    assert other.json()["overall"] == "OK"
