"""Shared helpers for CALLBACK tests (not a test module). Fictional 555-01xx numbers only."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from callshield.callback.config import CallbackSettings
from callshield.callback.dialer_service import DialResult, run_dial
from callshield.callback.keys import DestinationKeyer
from callshield.callback.lines import record_outbound_attestation
from callshield.callback.mock_scenarios import CallbackMockDialer, CallbackScenario
from callshield.callback.runtime import CallbackRuntime, set_runtime
from callshield.callback.storage import LocalFsStore
from callshield.core.clock import FrozenClock, set_clock
from callshield.db.base import Base
from callshield.db.models import CallerObservation, CallSession, Incident, ProtectedLine, User
from callshield.db.models.callback_ext import (
    CallbackAuthorization,
    CallbackRequest,
    CallbackResult,
    CallbackSettingsRow,
    LineOutboundOwnershipCheck,
)
from callshield.db.models.defend_ext import CallRouting
from callshield.db.session import make_session_factory
from callshield.worker.runner import load_handler_modules, run_one

from .conftest import TenantUser

LINE_E164 = "+14155550100"
CALLER_E164 = "+14155550123"
NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)  # 10:00 at UTC-05:00
TEST_TZ = "UTC-05:00"


@dataclass
class CbEnv:
    client: TestClient
    engine: Engine
    user: TenantUser
    runtime: CallbackRuntime
    clock: FrozenClock
    line_id: uuid.UUID
    tmp_path: Path
    spare: TenantUser | None = None  # a second tenant created BEFORE the clock froze
    operator_headers: dict[str, str] = field(default_factory=dict)
    counter: int = field(default=0)

    @property
    def dialer(self) -> CallbackMockDialer:
        assert isinstance(self.runtime.dialer, CallbackMockDialer)
        return self.runtime.dialer

    def key(self, label: str = "k") -> str:
        self.counter += 1
        return f"{label}-{self.counter}-{uuid.uuid4().hex[:6]}"

    def session(self) -> Session:
        return make_session_factory(self.engine)()

    def add_inbound(
        self,
        *,
        attestation: dict[str, Any] | None = None,
        age: timedelta = timedelta(hours=1),
        from_displayed: str | None = CALLER_E164,
        with_incident: bool = False,
    ) -> uuid.UUID:
        meta = {"attestation_level": "A"} if attestation is None else attestation
        with self.session() as s:
            call = CallSession(
                tenant_id=self.user.tenant_id,
                line_id=self.line_id,
                provider_call_id=f"prov-{uuid.uuid4().hex[:8]}",
                direction="INBOUND",
                started_at=self.clock() - age,
            )
            s.add(call)
            s.flush()
            s.add(
                CallRouting(
                    tenant_id=self.user.tenant_id,
                    session_id=call.id,
                    action="RING",
                    reason="test",
                    to_e164=LINE_E164,
                    from_displayed=from_displayed,
                )
            )
            s.add(
                CallerObservation(
                    tenant_id=self.user.tenant_id,
                    session_id=call.id,
                    displayed_caller_id=from_displayed,
                    attestation_metadata=meta,
                )
            )
            if with_incident:
                s.add(
                    Incident(
                        tenant_id=self.user.tenant_id,
                        call_session_id=call.id,
                        line_id=self.line_id,
                        displayed_caller_id=from_displayed,
                    )
                )
            s.commit()
            return call.id

    def post(
        self, path: str, body: dict[str, Any] | None = None, key: str | None = None
    ) -> Response:
        headers = dict(self.user.headers)
        if key:
            headers["Idempotency-Key"] = key
        resp: Response = self.client.post(path, json=body or {}, headers=headers)
        return resp

    def get(self, path: str) -> Response:
        resp: Response = self.client.get(path, headers=self.user.headers)
        return resp

    def queue(self, call_id: uuid.UUID, key: str | None = None, **body: Any) -> Response:
        return self.post(f"/v1/calls/{call_id}/callbacks", body, key or self.key("cb"))

    def run_jobs(self, max_jobs: int = 10) -> int:
        load_handler_modules()
        factory = make_session_factory(self.engine)
        ran = 0
        while ran < max_jobs and run_one(factory, ["callback.dial", "callback.place"]):
            ran += 1
        return ran

    def scenario(self, scenario: str | CallbackScenario) -> CallbackMockDialer:
        dialer = CallbackMockDialer(scenario)
        self.runtime.dialer = dialer
        return dialer


def build_cb_env(
    client: TestClient,
    engine: Engine,
    user: TenantUser,
    tmp_path: Path,
    *,
    scenario: str | CallbackScenario = "business_intro",
    enabled: bool = True,
    user_enabled: bool = True,
    settings_kwargs: dict[str, Any] | None = None,
    spare: TenantUser | None = None,
    operator_headers: dict[str, str] | None = None,
) -> CbEnv:
    clock = FrozenClock(NOW)
    set_clock(clock)
    settings = CallbackSettings(
        callback_enabled=enabled,
        storage_root=str(tmp_path / "media"),
        **(settings_kwargs or {}),
    )
    runtime = CallbackRuntime(
        settings=settings,
        keyer=DestinationKeyer(settings.hmac_secret.get_secret_value(), allow_dev_default=True),
        dialer=CallbackMockDialer(scenario),
        store=LocalFsStore(tmp_path / "media"),
        environment="test",
    )
    set_runtime(runtime)
    line_id = _add_line(engine, user, user_enabled)
    return CbEnv(
        client, engine, user, runtime, clock, line_id, tmp_path, spare, operator_headers or {}
    )


def _add_line(engine: Engine, user: TenantUser, user_enabled: bool) -> uuid.UUID:
    with make_session_factory(engine)() as s:
        db_user = s.execute(select(User).where(User.id == user.user_id)).scalar_one()
        db_user.display_name = "Jordan Tester"
        line = ProtectedLine(
            tenant_id=user.tenant_id,
            owner_user_id=user.user_id,
            e164=LINE_E164,
            verification_status="VERIFIED",
        )
        s.add(line)
        s.flush()
        s.add(
            LineOutboundOwnershipCheck(
                tenant_id=user.tenant_id,
                line_id=line.id,
                code_hash="x",
                channel="memory-outbox",
                expires_at=NOW + timedelta(days=1),
                verified_at=NOW,
            )
        )
        record_outbound_attestation(s, user.tenant_id, line.id, "A", "test-provider")
        s.add(
            CallbackSettingsRow(
                tenant_id=user.tenant_id,
                scope_key=f"USER:{user.user_id}",
                user_id=user.user_id,
                timezone=TEST_TZ,
                overrides={"callback_enabled": user_enabled},
            )
        )
        s.commit()
        return line.id


def second_env(env: CbEnv, user: TenantUser | None) -> CbEnv:
    assert user is not None
    """Another tenant on the SAME instance (shared runtime, clock, global tables)."""
    line_id = _add_line(env.engine, user, True)
    return CbEnv(env.client, env.engine, user, env.runtime, env.clock, line_id, env.tmp_path)


def bypass_queue(env: CbEnv, call_id: uuid.UUID, *, minutes_valid: int = 30) -> uuid.UUID:
    """Insert authorization + QUEUED request rows directly, skipping every queue-time check.
    Used to prove the dialer service enforces the gates/caps itself."""
    with env.session() as s:
        auth = CallbackAuthorization(
            tenant_id=env.user.tenant_id,
            user_id=env.user.user_id,
            inbound_call_id=call_id,
            app_version="test",
            authorized_at=env.clock(),
            expires_at=env.clock() + timedelta(minutes=minutes_valid),
            idempotency_key=env.key("bypass-auth"),
        )
        s.add(auth)
        s.flush()
        request = CallbackRequest(
            tenant_id=env.user.tenant_id,
            user_id=env.user.user_id,
            inbound_call_id=call_id,
            line_id=env.line_id,
            authorization_id=auth.id,
            destination_key=env.runtime.keyer.key(CALLER_E164),
            destination_masked="+1415***0123",
            status="QUEUED",
            idempotency_key=env.key("bypass-req"),
        )
        s.add(request)
        s.commit()
        return request.id


def dial_directly(env: CbEnv, request_id: uuid.UUID) -> DialResult:
    with env.session() as s:
        result = run_dial(s, env.runtime, env.user.tenant_id, request_id)
        s.commit()
        return result


def dump_db_text(engine: Engine) -> str:
    """Every column of every table rendered as text (to prove something is NOT stored)."""
    parts: list[str] = []
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            for row in conn.execute(select(table)):
                parts.append(repr(tuple(row)))
    return "\n".join(parts)


def result_of(env: CbEnv, request_id: str | uuid.UUID) -> CallbackResult:
    with env.session() as s:
        row = s.execute(
            select(CallbackResult).where(CallbackResult.request_id == uuid.UUID(str(request_id)))
        ).scalar_one()
        s.expunge(row)
        return row
