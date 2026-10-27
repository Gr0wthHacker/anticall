"""Provider-reported call state changes (answered / transferred / ended). Each emits a
call.* ledger event exactly once per (call, transition)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from callshield.core.clock import utcnow
from callshield.core.errors import NotFoundError
from callshield.core.idempotency import make_idempotency_key
from callshield.db.models.defend import CallSession, ProtectedLine
from callshield.defend.lifecycle import IngestOutcome, find_call, find_routing
from callshield.domain.enums import CallDisposition, EventOrigin
from callshield.domain.topics import Topic
from callshield.ledger import append_event
from callshield.telephony.base import ProviderCallEvent, ProviderEventType

_TERMINAL = frozenset(
    {
        CallDisposition.VOICEMAIL,
        CallDisposition.REJECTED,
        CallDisposition.MISSED,
        CallDisposition.NO_ANSWER,
        CallDisposition.FAILED,
        CallDisposition.ANSWERED_BY_USER,
        CallDisposition.TIMED_OUT,
    }
)
_TOPICS = {
    ProviderEventType.CALL_ANSWERED: (Topic.CALL_ANSWERED, "answered"),
    ProviderEventType.CALL_TRANSFERRED: (Topic.CALL_TRANSFERRED, "transferred"),
    ProviderEventType.CALL_ENDED: (Topic.CALL_ENDED, "ended"),
}


def _answered_disposition(call: CallSession, screening_active: bool, op: str) -> None:
    if screening_active:
        return  # the screening agent answered; the user has not
    if op == "transfer" and CallDisposition(call.disposition) not in _TERMINAL:
        call.disposition = CallDisposition.ANSWERED_BY_USER.value


def _ended_disposition(call: CallSession, op: str) -> None:
    if CallDisposition(call.disposition) == CallDisposition.RECEIVED:
        call.disposition = (
            CallDisposition.NO_ANSWER.value if op == "transfer" else CallDisposition.MISSED.value
        )


def apply_transition(
    session: Session, line: ProtectedLine, event: ProviderCallEvent
) -> IngestOutcome:
    if event.call_id is None:
        raise NotFoundError("unknown call", code="UNKNOWN_CALL")
    call = find_call(session, line.tenant_id, event.provider, event.call_id)
    if call is None or call.line_id != line.id:
        raise NotFoundError("unknown call", code="UNKNOWN_CALL")
    topic, label = _TOPICS[event.event_type]
    routing = find_routing(session, line.tenant_id, call.id)
    op = str((routing.screening_state or {}).get("op", "")) if routing else ""
    screening_active = bool(routing and routing.screening_active)
    _, created = append_event(
        session,
        line.tenant_id,
        topic,
        {"call_session_id": str(call.id), "provider_event_id": event.event_id},
        EventOrigin.PROVIDER_WEBHOOK,
        make_idempotency_key("call", call.provider, call.provider_call_id, label),
        provider_ref=event.event_id,
    )
    if not created:
        return IngestOutcome("duplicate", call.id, detail=label)
    if event.event_type == ProviderEventType.CALL_ANSWERED:
        _answered_disposition(call, screening_active, op)
    elif event.event_type == ProviderEventType.CALL_TRANSFERRED:
        if CallDisposition(call.disposition) != CallDisposition.FALLBACK_ROUTED:
            call.disposition = CallDisposition.TRANSFERRED.value
    else:
        _finish(call, event, op)
    return IngestOutcome("accepted", call.id, detail=label)


def _finish(call: CallSession, event: ProviderCallEvent, op: str) -> None:
    call.ended_at = event.occurred_at or utcnow()
    if event.duration_seconds is not None:
        call.duration_seconds = event.duration_seconds
    elif call.started_at is not None:
        call.duration_seconds = max(int((call.ended_at - call.started_at).total_seconds()), 0)
    _ended_disposition(call, op)
