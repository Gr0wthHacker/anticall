"""Call read models and user corrections.

Facts (OBSERVED) are kept apart from claims (CLAIMED) and model conclusions
(MODEL_INFERRED). A correction appends a new classification row (USER_ATTESTED) that
supersedes the previous one; the original row is never edited.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from callshield.audit import record_audit
from callshield.auth.scoping import scoped_get_or_404
from callshield.db.models.defend import (
    CallerObservation,
    CallSession,
    Classification,
    Incident,
    OrganizationClaim,
)
from callshield.db.models.defend_ext import CallRouting
from callshield.defend.schemas import CallSummary
from callshield.domain.enums import (
    ClassificationLabel,
    EventOrigin,
    EvidenceLabel,
    ReviewStatus,
)
from callshield.domain.topics import Topic
from callshield.ledger import append_event
from callshield.screening.classifier import coarse_category


def effective_classification(rows: list[Classification]) -> Classification | None:
    """The end of the supersedes chain (latest row that nothing supersedes)."""
    superseded = {r.supersedes_id for r in rows if r.supersedes_id is not None}
    live = [r for r in rows if r.id not in superseded]
    return live[-1] if live else None


def _classifications(
    session: Session, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> list[Classification]:
    stmt = (
        select(Classification)
        .where(Classification.tenant_id == tenant_id, Classification.call_id == call_id)
        .order_by(Classification.created_at, Classification.id)
    )
    return list(session.execute(stmt).scalars().all())


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def filtered_calls(
    tenant_id: uuid.UUID,
    date_from: datetime | None,
    date_to: datetime | None,
    direction: str | None,
    disposition: str | None,
    label: str | None,
    claimed: str | None,
) -> Select[Any]:
    stmt = select(CallSession).where(CallSession.tenant_id == tenant_id)
    if date_from is not None:
        stmt = stmt.where(CallSession.started_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(CallSession.started_at <= date_to)
    if direction:
        stmt = stmt.where(CallSession.direction == direction.upper())
    if disposition:
        stmt = stmt.where(CallSession.disposition == disposition.upper())
    if label:
        superseded = select(Classification.supersedes_id).where(
            Classification.tenant_id == tenant_id, Classification.supersedes_id.is_not(None)
        )
        live = select(Classification.call_id).where(
            Classification.tenant_id == tenant_id,
            Classification.label == label.upper(),
            Classification.id.not_in(superseded),
        )
        stmt = stmt.where(CallSession.id.in_(live))
    if claimed:
        pattern = f"%{_escape_like(claimed)}%"
        match = select(OrganizationClaim.session_id).where(
            OrganizationClaim.tenant_id == tenant_id,
            OrganizationClaim.claimed_name.ilike(pattern, escape="\\"),
        )
        stmt = stmt.where(CallSession.id.in_(match))
    return stmt


def count_calls(session: Session, stmt: Select[Any]) -> int:
    return session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()


def summaries(
    session: Session, tenant_id: uuid.UUID, calls: list[CallSession]
) -> list[CallSummary]:
    ids = [c.id for c in calls]
    obs = {
        o.session_id: o.displayed_caller_id
        for o in session.execute(
            select(CallerObservation).where(
                CallerObservation.tenant_id == tenant_id, CallerObservation.session_id.in_(ids)
            )
        ).scalars()
    }
    by_call: dict[uuid.UUID, list[Classification]] = {}
    for row in session.execute(
        select(Classification)
        .where(Classification.tenant_id == tenant_id, Classification.call_id.in_(ids))
        .order_by(Classification.created_at, Classification.id)
    ).scalars():
        by_call.setdefault(row.call_id, []).append(row)
    out: list[CallSummary] = []
    for c in calls:
        eff = effective_classification(by_call.get(c.id, []))
        out.append(
            CallSummary(
                id=c.id,
                line_id=c.line_id,
                direction=c.direction,
                disposition=c.disposition,
                started_at=c.started_at,
                ended_at=c.ended_at,
                displayed_caller_id=obs.get(c.id),
                label=eff.label if eff else None,
                category=coarse_category(eff.label) if eff else None,
            )
        )
    return out


def _classification_out(row: Classification) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "label": row.label,
        "confidence": row.confidence,
        "evidence_label": row.evidence_label,
        "model_version": row.model_version,
        "reviewed_status": row.reviewed_status,
        "supersedes_id": str(row.supersedes_id) if row.supersedes_id else None,
        "created_at": row.created_at.isoformat(),
        "note": "Confidence is a model score, not verification.",
    }


def call_detail(session: Session, tenant_id: uuid.UUID, call_id: uuid.UUID) -> dict[str, Any]:
    call = scoped_get_or_404(session, CallSession, call_id, tenant_id)
    obs = (
        session.execute(
            select(CallerObservation)
            .where(
                CallerObservation.tenant_id == tenant_id, CallerObservation.session_id == call.id
            )
            .order_by(CallerObservation.observed_at)
        )
        .scalars()
        .first()
    )
    claims = (
        session.execute(
            select(OrganizationClaim)
            .where(
                OrganizationClaim.tenant_id == tenant_id, OrganizationClaim.session_id == call.id
            )
            .order_by(OrganizationClaim.created_at)
        )
        .scalars()
        .all()
    )
    rows = _classifications(session, tenant_id, call.id)
    eff = effective_classification(rows)
    routing = session.execute(
        select(CallRouting).where(
            CallRouting.tenant_id == tenant_id, CallRouting.session_id == call.id
        )
    ).scalar_one_or_none()
    incident = session.execute(
        select(Incident.id).where(
            Incident.tenant_id == tenant_id, Incident.call_session_id == call.id
        )
    ).scalar_one_or_none()
    return {
        "id": str(call.id),
        "line_id": str(call.line_id) if call.line_id else None,
        "incident_id": str(incident) if incident else None,
        "facts": {
            "evidence_label": EvidenceLabel.OBSERVED.value,
            "provider": call.provider,
            "direction": call.direction,
            "disposition": call.disposition,
            "started_at": call.started_at.isoformat() if call.started_at else None,
            "ended_at": call.ended_at.isoformat() if call.ended_at else None,
            "duration_seconds": call.duration_seconds,
            "displayed_caller_id": obs.displayed_caller_id if obs else None,
            "network_attestation": (obs.attestation_metadata if obs else {}),
            "note": "Displayed caller ID is observed, not a verified calling party.",
        },
        "claims": [
            {
                "id": str(c.id),
                "statement_type": c.statement_type,
                "claimed": c.claimed_name,
                "evidence_label": c.label,
                "verification_status": c.verification_status,
            }
            for c in claims
        ],
        "model_conclusions": [_classification_out(r) for r in rows],
        "effective_classification": _classification_out(eff) if eff else None,
        "routing": (
            {
                "action": routing.action,
                "reason": routing.reason,
                "processing_mode": routing.processing_mode,
                "screening_active": routing.screening_active,
            }
            if routing
            else None
        ),
    }


def add_correction(
    session: Session,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    user_id: uuid.UUID,
    label: ClassificationLabel,
    note: str,
) -> Classification:
    call = scoped_get_or_404(session, CallSession, call_id, tenant_id)
    previous = effective_classification(_classifications(session, tenant_id, call.id))
    row = Classification(
        tenant_id=tenant_id,
        call_id=call.id,
        model_version="user-correction",
        label=label.value,
        confidence=1.0,
        evidence_label=EvidenceLabel.USER_ATTESTED.value,
        reviewed_status=ReviewStatus.CORRECTED.value,
        supersedes_id=previous.id if previous else None,
    )
    session.add(row)
    session.flush()
    incident = session.execute(
        select(Incident).where(Incident.tenant_id == tenant_id, Incident.call_session_id == call.id)
    ).scalar_one_or_none()
    if incident is not None:
        incident.label = label.value
    append_event(
        session,
        tenant_id,
        Topic.CORRECTION_RECORDED,
        {
            "call_session_id": str(call.id),
            "classification_id": str(row.id),
            "supersedes_id": str(previous.id) if previous else None,
            "label": label.value,
        },
        EventOrigin.USER_ACTION,
        f"correction:{row.id}",
    )
    record_audit(
        session,
        tenant_id,
        f"user:{user_id}",
        "call.classification_corrected",
        {"call_id": str(call.id), "classification_id": str(row.id), "note": note},
    )
    return row
