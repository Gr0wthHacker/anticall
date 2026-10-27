"""Processing consent service: line-level grants, per-call decisions (consent_records + audit)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.audit import SYSTEM_ACTOR, record_audit
from callshield.core.clock import utcnow
from callshield.core.config import Settings
from callshield.db.models.defend import CallSession, ConsentRecord, ProtectedLine
from callshield.db.models.defend_ext import ConsentLineLink
from callshield.domain.enums import ProcessingMode
from callshield.policy.config import parse_routing_config
from callshield.policy.processing import ProcessingDecision, evaluate_processing


def line_consents(
    session: Session, line: ProtectedLine, include_decisions: bool = False
) -> list[ConsentRecord]:
    """Line-level consent grants/revocations, oldest first."""
    stmt = (
        select(ConsentRecord)
        .join(ConsentLineLink, ConsentLineLink.consent_id == ConsentRecord.id)
        .where(
            ConsentRecord.tenant_id == line.tenant_id,
            ConsentLineLink.tenant_id == line.tenant_id,
            ConsentLineLink.line_id == line.id,
        )
        .order_by(ConsentRecord.created_at, ConsentRecord.id)
    )
    if not include_decisions:
        stmt = stmt.where(ConsentRecord.session_id.is_(None))
    return list(session.execute(stmt).scalars().all())


def current_decision(
    session: Session, settings: Settings, line: ProtectedLine
) -> ProcessingDecision:
    config = parse_routing_config(line.routing_config)
    return evaluate_processing(
        settings, line.routing_config or {}, line_consents(session, line), config.jurisdiction
    )


def record_grant(
    session: Session,
    line: ProtectedLine,
    user_id: uuid.UUID,
    mode: ProcessingMode,
    authorized: bool,
    legal_basis: dict[str, Any],
) -> ConsentRecord:
    """Append a grant (or a revocation when authorized=False). Never edits earlier rows."""
    now = utcnow()
    record = ConsentRecord(
        tenant_id=line.tenant_id,
        session_id=None,
        user_id=user_id,
        processing_mode=mode.value,
        legal_basis_config={**legal_basis, "kind": "GRANT" if authorized else "REVOCATION"},
        authorized=authorized,
        authorized_at=now if authorized else None,
        revoked_at=None if authorized else now,
    )
    session.add(record)
    session.flush()
    session.add(ConsentLineLink(tenant_id=line.tenant_id, consent_id=record.id, line_id=line.id))
    record_audit(
        session,
        line.tenant_id,
        f"user:{user_id}",
        "consent.granted" if authorized else "consent.revoked",
        {"line_id": str(line.id), "mode": mode.value, "consent_id": str(record.id)},
    )
    return record


def record_call_decision(
    session: Session, line: ProtectedLine, call: CallSession, decision: ProcessingDecision
) -> ConsentRecord:
    """Every per-call processing decision is a consent_records row plus an audit event."""
    record = ConsentRecord(
        tenant_id=line.tenant_id,
        session_id=call.id,
        processing_mode=decision.mode.value,
        legal_basis_config={"kind": "DECISION", "line_id": str(line.id), **decision.as_dict()},
        authorized=decision.allow_live_stt,
        authorized_at=utcnow() if decision.allow_live_stt else None,
    )
    session.add(record)
    session.flush()
    session.add(ConsentLineLink(tenant_id=line.tenant_id, consent_id=record.id, line_id=line.id))
    record_audit(
        session,
        line.tenant_id,
        SYSTEM_ACTOR,
        "processing.decision",
        {"call_id": str(call.id), "line_id": str(line.id), **decision.as_dict()},
    )
    return record
