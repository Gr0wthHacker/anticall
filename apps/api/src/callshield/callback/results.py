"""Persist a finished callback: labeled fields, observed metadata, media, blocklists, ledger.

Labels: every answering-party value is CLAIMED; platform observations are OBSERVED.
Originals are never mutated: the spoof marker is a NEW classification row + ledger event.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from callshield.audit import SYSTEM_ACTOR, record_audit
from callshield.auth.scoping import scoped_select
from callshield.callback import events
from callshield.callback.blocklist import block_destination
from callshield.callback.context import InboundContext
from callshield.callback.hardstop import HardStopKind
from callshield.callback.runtime import CallbackRuntime
from callshield.callback.session import SessionResult
from callshield.db.models.callback_ext import CallbackMedia, CallbackRequest, CallbackResult
from callshield.db.models.defend import Classification, Incident
from callshield.domain.enums import EvidenceLabel

FIELDS_RETENTION = timedelta(days=90)
MEDIA_RETENTION = timedelta(days=30)
SPOOF_MODEL = "callback-hardstop-v1"


def _claimed(value: Any) -> dict[str, Any]:
    return {"value": value, "label": EvidenceLabel.CLAIMED.value}


def _observed(value: Any) -> dict[str, Any]:
    return {"value": value, "label": EvidenceLabel.OBSERVED.value}


def labeled_fields(result: SessionResult) -> dict[str, Any]:
    f = result.fields
    greeting = result.listen.greeting_type.value if result.listen else None
    return {
        "rep_name": _claimed(f.rep_name),
        "company_claimed": _claimed(f.company_claimed),
        "greeting_type": _claimed(greeting),
        "represents_seller": _claimed(f.represents_seller),
        "offer_type": _claimed(f.offer_type),
        "transfer_target": _claimed(f.transfer_target),
        "numbers_mentioned": _claimed(list(f.numbers_mentioned)),
        "websites_mentioned": _claimed(list(f.websites_mentioned)),
        "hung_up_after_disclosure": _observed(result.hung_up_after_disclosure),
        "wrong_party": _claimed(result.wrong_party),
        "stop_requested": _claimed(result.stop_requested),
    }


def observed_metadata(
    result: SessionResult, inbound: InboundContext, started_at: datetime, line_type: str | None
) -> dict[str, Any]:
    return {
        "call_started_at": _observed(started_at.isoformat()),
        "duration_seconds": _observed(round(result.duration_seconds, 2)),
        "attestation_level": _observed(inbound.attestation_level),
        "line_type": _observed(line_type),
        "outcome": _observed(result.outcome.value),
        "log_code": _observed(result.log_code),
        "disclosed": _observed(result.disclosed),
        "disclosure_offset_seconds": _observed(result.disclosure_offset),
        "dtmf_steps": _observed(len(result.dtmf_sent)),
        "hard_stop": _observed(result.hard_stop.kind.value if result.hard_stop else None),
    }


def persist_result(
    session: Session,
    rt: CallbackRuntime,
    request: CallbackRequest,
    inbound: InboundContext,
    result: SessionResult,
    started_at: datetime,
    now: datetime,
) -> CallbackResult:
    geography = rt.line_lookup.lookup(inbound.destination) if inbound.destination else None
    row = CallbackResult(
        tenant_id=request.tenant_id,
        request_id=request.id,
        outcome=result.outcome.value,
        fields=labeled_fields(result),
        call_metadata=observed_metadata(
            result, inbound, started_at, geography.line_type if geography else None
        ),
        case_id=request.case_id,
        expires_at=now + FIELDS_RETENTION,
    )
    session.add(row)
    request.status = "COMPLETED" if result.answered else "FAILED"
    request.open_slot = None
    request.updated_at = now
    _apply_hard_stop(session, request, result)
    _store_media(session, rt, request, result, now)
    _emit_completed(session, request, row)
    record_audit(
        session,
        request.tenant_id,
        SYSTEM_ACTOR,
        "callback.completed",
        {"request_id": str(request.id), "outcome": result.outcome.value},
    )
    session.flush()
    return row


def _apply_hard_stop(session: Session, request: CallbackRequest, result: SessionResult) -> None:
    stop = result.hard_stop
    if stop is None:
        return
    block_destination(
        session,
        request.tenant_id,
        request.user_id,
        request.destination_key,
        request.destination_masked,
        stop.kind.value,
    )
    if stop.kind is HardStopKind.WRONG_PARTY:
        mark_possible_spoof(session, request)


def mark_possible_spoof(session: Session, request: CallbackRequest) -> None:
    """Append-only: a classification row + ledger event; the incident/call rows are untouched."""
    session.add(
        Classification(
            tenant_id=request.tenant_id,
            call_id=request.inbound_call_id,
            model_version=SPOOF_MODEL,
            label="possible_spoof",
            confidence=0.0,
            evidence_label=EvidenceLabel.CLAIMED.value,
        )
    )
    incident = (
        session.execute(
            scoped_select(Incident, request.tenant_id).where(
                Incident.call_session_id == request.inbound_call_id
            )
        )
        .scalars()
        .first()
    )
    events.emit(
        session,
        request.tenant_id,
        events.POSSIBLE_SPOOF,
        {
            "request_id": str(request.id),
            "inbound_call_id": str(request.inbound_call_id),
            "incident_id": str(incident.id) if incident else None,
            "basis": "answering party said wrong number (CLAIMED)",
        },
        request.id,
    )


def _store_media(
    session: Session,
    rt: CallbackRuntime,
    request: CallbackRequest,
    result: SessionResult,
    now: datetime,
) -> None:
    """Only when outbound_recording=true AND the disclosure completed. Never listen-window data."""
    if not request.outbound_recording or result.audio is None:
        return
    store = rt.media_store()
    transcript = "\n".join(f"{who}: {text}" for who, text in result.transcript).encode()
    for kind, data in (("AUDIO", result.audio), ("TRANSCRIPT", transcript)):
        key = f"{request.tenant_id}/{request.id}/{kind.lower()}-{uuid.uuid4().hex[:8]}.bin"
        digest = store.put(key, data)
        session.add(
            CallbackMedia(
                tenant_id=request.tenant_id,
                request_id=request.id,
                kind=kind,
                storage_key=key,
                sha256=digest,
                size_bytes=len(data),
                expires_at=now + MEDIA_RETENTION,
            )
        )


def _emit_completed(session: Session, request: CallbackRequest, row: CallbackResult) -> None:
    """Consumed by the casebuilder (by ledger event only; no import of enforce code)."""
    events.emit(
        session,
        request.tenant_id,
        events.COMPLETED,
        {
            "request_id": str(request.id),
            "inbound_call_id": str(request.inbound_call_id),
            "incident_id": str(request.incident_id) if request.incident_id else None,
            "case_id": str(request.case_id) if request.case_id else None,
            "outcome": row.outcome,
            "fields": row.fields,
            "metadata": row.call_metadata,
            "destination": request.destination_masked,
        },
        request.id,
    )
