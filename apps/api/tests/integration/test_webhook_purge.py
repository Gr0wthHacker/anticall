"""Job webhook.purge_receipts: bounded retention for replay nonces."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from callshield.core.clock import utcnow
from callshield.core.config import Settings, reset_settings_cache
from callshield.db.models.defend import ProtectedLine
from callshield.db.models.defend_ext import WebhookReceipt
from callshield.db.models.platform import Job
from callshield.db.session import make_session_factory
from callshield.domain.enums import JobState
from callshield.worker.queue import enqueue, registered_kinds
from callshield.worker.runner import load_handler_modules, run_one
from tests.conftest import TenantUser, TenantUserFactory

KIND = "webhook.purge_receipts"


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    reset_settings_cache()
    yield
    reset_settings_cache()


def _line(session: Session, user: TenantUser, e164: str) -> ProtectedLine:
    line = ProtectedLine(tenant_id=user.tenant_id, owner_user_id=user.user_id, e164=e164)
    session.add(line)
    session.flush()
    return line


def _receipt(session: Session, line: ProtectedLine, age_days: float) -> uuid.UUID:
    row = WebhookReceipt(
        tenant_id=line.tenant_id,
        line_id=line.id,
        nonce=f"nonce-{uuid.uuid4().hex}",
        received_at=utcnow() - timedelta(days=age_days),
    )
    session.add(row)
    session.flush()
    return row.id


def _remaining(session: Session) -> set[uuid.UUID]:
    session.expire_all()
    return set(session.execute(select(WebhookReceipt.id)).scalars())


def _run(engine: Engine, user: TenantUser, key: str) -> None:
    with make_session_factory(engine)() as s:
        enqueue(s, user.tenant_id, KIND, {}, idempotency_key=key)
        s.commit()
    assert run_one(make_session_factory(engine), [KIND])


def test_handler_is_registered() -> None:
    load_handler_modules()
    assert KIND in registered_kinds()


def test_default_retention_is_30_days_and_validated() -> None:
    assert Settings().webhook_receipt_retention_days == 30
    with pytest.raises(ValueError):
        Settings(webhook_receipt_retention_days=0)


def test_purges_only_receipts_older_than_retention_and_is_idempotent(
    engine: Engine, session: Session, make_tenant_user: TenantUserFactory
) -> None:
    load_handler_modules()
    user = make_tenant_user("Owner")
    line = _line(session, user, "+14155550123")
    old = [_receipt(session, line, 31), _receipt(session, line, 400)]
    fresh = [_receipt(session, line, 29), _receipt(session, line, 0)]
    session.commit()
    _run(engine, user, "purge-1")
    assert _remaining(session) == set(fresh) and not set(old) & _remaining(session)
    _run(engine, user, "purge-2")  # second run deletes nothing more and succeeds
    assert _remaining(session) == set(fresh)
    states = {j.state for j in session.execute(select(Job).where(Job.kind == KIND)).scalars()}
    assert states == {JobState.SUCCEEDED.value}


def test_retention_is_configurable(
    engine: Engine,
    session: Session,
    make_tenant_user: TenantUserFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_handler_modules()
    monkeypatch.setenv("CALLSHIELD_WEBHOOK_RECEIPT_RETENTION_DAYS", "2")
    reset_settings_cache()
    user = make_tenant_user("Owner")
    line = _line(session, user, "+14155550123")
    gone, kept = _receipt(session, line, 3), _receipt(session, line, 1)
    session.commit()
    _run(engine, user, "purge-cfg")
    assert _remaining(session) == {kept} and gone not in _remaining(session)


def test_purge_is_scoped_to_the_jobs_tenant(
    engine: Engine, session: Session, make_tenant_user: TenantUserFactory
) -> None:
    load_handler_modules()
    mine, theirs = make_tenant_user("Mine"), make_tenant_user("Theirs")
    my_old = _receipt(session, _line(session, mine, "+14155550123"), 90)
    their_old = _receipt(session, _line(session, theirs, "+14155550124"), 90)
    session.commit()
    _run(engine, mine, "purge-scope")
    assert _remaining(session) == {their_old} and my_old not in _remaining(session)
