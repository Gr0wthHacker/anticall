from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from callshield.audit import record_audit, record_case_audit
from callshield.db.models import (
    AuditEvent,
    Case,
    CaseAuditEvent,
    LedgerEvent,
    ProtectedLine,
    Tenant,
)
from callshield.domain.enums import ActorType, EventOrigin
from callshield.domain.topics import Topic
from callshield.ledger import append_event, list_events


def _count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(LedgerEvent)).scalar_one()


def test_append_event_is_idempotent(session: Session, tenant_row: Tenant) -> None:
    first, created = append_event(
        session,
        tenant_row.id,
        Topic.CALL_RECEIVED,
        {"a": 1},
        EventOrigin.PROVIDER_WEBHOOK,
        "k1",
        provider_ref="CA1",
    )
    session.commit()
    assert created is True
    again, created2 = append_event(
        session, tenant_row.id, Topic.CALL_RECEIVED, {"a": 2}, EventOrigin.PROVIDER_WEBHOOK, "k1"
    )
    assert created2 is False
    assert again.id == first.id
    assert again.payload == {"a": 1}  # original payload retained
    assert _count(session) == 1


def test_envelope_fields(session: Session, tenant_row: Tenant) -> None:
    event, _ = append_event(session, tenant_row.id, "call.ended", {}, EventOrigin.SYSTEM, "k-env")
    session.commit()
    assert event.schema_version == 1
    assert event.origin == "SYSTEM"
    assert event.tenant_id == tenant_row.id
    assert event.created_at.tzinfo is not None
    assert event.idempotency_key == "k-env"


def test_same_key_different_tenants_are_distinct(session: Session) -> None:
    a, b = Tenant(name="a"), Tenant(name="b")
    session.add_all([a, b])
    session.commit()
    _, c1 = append_event(session, a.id, Topic.CALL_ENDED, {}, EventOrigin.SYSTEM, "same")
    _, c2 = append_event(session, b.id, Topic.CALL_ENDED, {}, EventOrigin.SYSTEM, "same")
    session.commit()
    assert c1 and c2
    assert len(list_events(session, a.id)) == 1
    assert len(list_events(session, b.id)) == 1


def test_list_events_filters_by_topic(session: Session, tenant_row: Tenant) -> None:
    append_event(session, tenant_row.id, Topic.CALL_RECEIVED, {}, EventOrigin.SYSTEM, "x1")
    append_event(session, tenant_row.id, Topic.CALL_ENDED, {}, EventOrigin.SYSTEM, "x2")
    session.commit()
    assert [e.topic for e in list_events(session, tenant_row.id, Topic.CALL_ENDED)] == [
        "call.ended"
    ]


def test_audit_writers(session: Session, tenant_row: Tenant) -> None:
    line = ProtectedLine(tenant_id=tenant_row.id, e164="+14155550123")
    session.add(line)
    session.flush()
    case = Case(tenant_id=tenant_row.id, protected_line_id=line.id, title="t")
    session.add(case)
    session.flush()
    audit = record_audit(session, tenant_row.id, "system", "thing.happened", {"k": "v"})
    case_audit = record_case_audit(session, tenant_row.id, case.id, ActorType.SYSTEM, "case.note")
    session.commit()
    assert len(audit.immutable_ref) == 64
    assert session.execute(select(func.count()).select_from(AuditEvent)).scalar_one() == 1
    assert case_audit.event_time.tzinfo is not None
    assert session.execute(select(func.count()).select_from(CaseAuditEvent)).scalar_one() == 1
