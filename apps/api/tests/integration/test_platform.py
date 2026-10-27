"""Jobs, error envelope, router discovery, capabilities, schema/migration checks."""

from __future__ import annotations

import sys
import textwrap
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, Table, inspect
from sqlalchemy.orm import Session

import callshield.api.routers as routers_pkg
from callshield.api.app import create_app
from callshield.core.clock import FrozenClock, set_clock
from callshield.core.config import Settings
from callshield.db.base import Base, tenant_models
from callshield.db.models import Tenant
from callshield.db.session import make_engine, make_session_factory
from callshield.domain.enums import JobState
from callshield.worker.queue import (
    backoff_seconds,
    claim_next,
    complete,
    enqueue,
    fail,
    job_handler,
)
from callshield.worker.runner import run_one

API_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- jobs


def test_enqueue_is_idempotent(session: Session, tenant_row: Tenant) -> None:
    job, created = enqueue(session, tenant_row.id, "k", {"x": 1}, idempotency_key="same")
    again, created2 = enqueue(session, tenant_row.id, "k", {"x": 2}, idempotency_key="same")
    assert created and not created2
    assert again.id == job.id


def test_claim_complete(session: Session, tenant_row: Tenant) -> None:
    job, _ = enqueue(session, tenant_row.id, "k", {})
    session.commit()
    claimed = claim_next(session)
    assert claimed is not None and claimed.id == job.id
    assert claimed.state == JobState.RUNNING and claimed.attempts == 1
    assert claim_next(session) is None  # already running
    complete(session, claimed)
    assert claimed.state == JobState.SUCCEEDED


def test_future_job_not_claimed_and_backoff_then_dead(session: Session, tenant_row: Tenant) -> None:
    clock = FrozenClock()
    set_clock(clock)
    enqueue(session, tenant_row.id, "k", {}, run_after=clock() + timedelta(hours=1), max_attempts=2)
    session.commit()
    assert claim_next(session) is None
    clock.advance(hours=2)
    job = claim_next(session)
    assert job is not None
    fail(session, job, "boom")
    session.commit()
    assert job.state == JobState.QUEUED
    assert job.run_after == clock() + timedelta(seconds=backoff_seconds(1))
    assert claim_next(session) is None
    clock.advance(hours=1)
    job = claim_next(session)
    assert job is not None and job.attempts == 2
    fail(session, job, "boom again")
    assert job.state == JobState.DEAD


def test_backoff_is_capped() -> None:
    assert backoff_seconds(1) == 30
    assert backoff_seconds(2) == 60
    assert backoff_seconds(50) == 3600


def test_handler_registry_and_run_one(engine) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine)
    seen: list[dict[str, int]] = []

    @job_handler("test.runone")
    def handle(session: Session, job) -> None:  # type: ignore[no-untyped-def]
        seen.append(job.payload)

    with factory() as s:
        tenant = Tenant(name="w")
        s.add(tenant)
        s.flush()
        enqueue(s, tenant.id, "test.runone", {"n": 1})
        enqueue(s, tenant.id, "test.unknown", {})
        s.commit()
    assert run_one(factory) is True
    assert run_one(factory) is True
    assert run_one(factory) is False  # unknown-kind job went back to backoff
    assert seen == [{"n": 1}]


# ---------------------------------------------------------------- HTTP


def test_error_envelope_shapes(client: TestClient) -> None:
    notfound = client.get("/v1/does-not-exist")
    body = notfound.json()
    assert notfound.status_code == 404
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == "NOT_FOUND"
    invalid = client.post("/v1/auth/login", json={"email": "bad"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "X-Request-ID" in invalid.headers
    assert invalid.json()["error"]["details"]["request_id"] == invalid.headers["X-Request-ID"]


def test_unhandled_exception_is_enveloped(settings: Settings, engine) -> None:  # type: ignore[no-untyped-def]
    app = create_app(settings, engine)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internals +14155550123")

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/boom")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in resp.text


def test_request_id_is_echoed(client: TestClient) -> None:
    assert (
        client.get("/healthz", headers={"X-Request-ID": "abc123"}).headers["X-Request-ID"]
        == "abc123"
    )


def test_health_and_ready(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200


def test_capabilities_all_off(client: TestClient) -> None:
    body = client.get("/v1/meta/capabilities").json()
    assert body["capabilities"] and not any(body["capabilities"].values())


def test_router_auto_discovery(
    settings: Settings,
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "zz_discovered.py").write_text(
        textwrap.dedent(
            """
            from fastapi import APIRouter
            router = APIRouter()

            @router.get("/v1/discovered-ping")
            def ping() -> dict[str, bool]:
                return {"pong": True}
            """
        )
    )
    (tmp_path / "no_router.py").write_text("X = 1\n")
    monkeypatch.setattr(routers_pkg, "__path__", [*routers_pkg.__path__, str(tmp_path)])
    sys.modules.pop("callshield.api.routers.zz_discovered", None)
    app = create_app(settings, engine)
    assert "callshield.api.routers.zz_discovered" in app.state.mounted_routers
    assert "callshield.api.routers.no_router" not in app.state.mounted_routers
    with TestClient(app) as c:
        assert c.get("/v1/discovered-ping").json() == {"pong": True}


# ---------------------------------------------------------------- schema


def test_every_tenant_table_has_not_null_indexed_fk() -> None:
    models = tenant_models()
    assert len(models) >= 28
    for model in models:
        table = cast(Table, model.__table__)
        col = table.c.tenant_id
        assert not col.nullable, table.name
        assert any(fk.column.table.name == "tenants" for fk in col.foreign_keys), table.name
        indexed = col.index or any(next(iter(ix.columns)) is col for ix in table.indexes)
        assert indexed, table.name


def test_all_prd_tables_exist() -> None:
    expected = {
        "tenants", "users", "protected_lines", "call_sessions", "caller_observations",
        "organization_claims", "resolution_cases", "contact_destinations", "contact_attempts",
        "ivr_paths", "opt_out_events", "classifications", "evidence_artifacts", "consent_records",
        "audit_events", "complaint_drafts", "candidate_campaigns", "campaign_links", "cases",
        "case_incidents", "case_assessments", "filing_drafts", "submission_approvals",
        "filing_submissions", "case_audit_events", "ledger_events", "incidents",
        "contact_authorizations", "routing_rules", "trusted_contacts", "destination_registry",
        "jobs",
    }  # fmt: skip
    assert expected <= set(Base.metadata.tables)


def test_migration_matches_models(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = make_engine(url)
    migrated = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert migrated == set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        cols = {c["name"] for c in inspect(engine).get_columns(name)}
        assert cols == {c.name for c in table.columns}, name
    command.downgrade(cfg, "base")
    engine.dispose()
