"""Call session lifecycle: inbound event -> session, observation, incident, ledger, job,
routing. Everything up to the incident row is persisted synchronously in the webhook
request (acceptance scenario 1)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from callshield.audit import SYSTEM_ACTOR, record_audit
from callshield.core.clock import utcnow
from callshield.core.config import Settings
from callshield.core.idempotency import make_idempotency_key
from callshield.core.phone import mask_phone
from callshield.db.models.defend import (
    CallerObservation,
    CallSession,
    Incident,
    ProtectedLine,
    RoutingRule,
    TrustedContact,
)
from callshield.db.models.defend_ext import CallRouting
from callshield.defend.consent import line_consents, record_call_decision
from callshield.domain.enums import (
    CallDirection,
    CallDisposition,
    EventOrigin,
    EvidenceLabel,
    RoutingAction,
)
from callshield.domain.topics import Topic
from callshield.ledger import append_event
from callshield.policy.config import LineRoutingConfig, parse_routing_config
from callshield.policy.processing import ProcessingDecision, evaluate_processing
from callshield.policy.routing import (
    CallContext,
    ContactEntry,
    RoutingDecision,
    RuleEntry,
    decide_route,
)
from callshield.screening.templates import DISCLOSURE
from callshield.telephony.attestation import parse_attestation_level
from callshield.telephony.base import ProviderAdapter, ProviderCallEvent, RouteInstruction
from callshield.worker.queue import enqueue

logger = logging.getLogger("callshield.defend")

INCIDENT_JOB_KIND = "incident.created"
_OP_DISPOSITION = {
    "answer": CallDisposition.SCREENED,
    "voicemail": CallDisposition.VOICEMAIL,
    "hangup": CallDisposition.REJECTED,
}


@dataclass
class IngestOutcome:
    status: str  # accepted | duplicate | ignored
    call_id: uuid.UUID | None = None
    incident_id: uuid.UUID | None = None
    instructions: list[RouteInstruction] = field(default_factory=list)
    detail: str = ""


def find_call(
    session: Session, tenant_id: uuid.UUID, provider: str, provider_call_id: str
) -> CallSession | None:
    stmt = select(CallSession).where(
        CallSession.tenant_id == tenant_id,
        CallSession.provider == provider,
        CallSession.provider_call_id == provider_call_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def find_routing(session: Session, tenant_id: uuid.UUID, call_id: uuid.UUID) -> CallRouting | None:
    stmt = select(CallRouting).where(
        CallRouting.tenant_id == tenant_id, CallRouting.session_id == call_id
    )
    return session.execute(stmt).scalar_one_or_none()


def load_routing_inputs(
    session: Session, line: ProtectedLine
) -> tuple[list[ContactEntry], list[RuleEntry]]:
    contacts = session.execute(
        select(TrustedContact).where(
            TrustedContact.tenant_id == line.tenant_id, TrustedContact.line_id == line.id
        )
    ).scalars()
    rules = session.execute(
        select(RoutingRule).where(
            RoutingRule.tenant_id == line.tenant_id,
            RoutingRule.line_id == line.id,
            RoutingRule.enabled.is_(True),
        )
    ).scalars()
    return (
        [ContactEntry(c.e164, c.kind) for c in contacts],
        [RuleEntry(r.id, r.priority, r.matcher, r.action) for r in rules],
    )


def build_context(line: ProtectedLine, event: ProviderCallEvent) -> CallContext:
    caller = event.from_number
    valid: str | None = None
    if caller is not None and caller.startswith("+") and caller[1:].isdigit():
        valid = caller
    return CallContext(
        caller=valid,
        line_e164=line.e164,
        hop_count=event.hop_count,
        diversions=tuple(d for d in event.diversions if d),
        attestation=event.attestation,
    )


def context_for_call(session: Session, line: ProtectedLine, call: CallSession) -> CallContext:
    """Rebuild the routing context from stored facts (later events carry no CLI)."""
    obs = (
        session.execute(
            select(CallerObservation).where(
                CallerObservation.tenant_id == line.tenant_id,
                CallerObservation.session_id == call.id,
            )
        )
        .scalars()
        .first()
    )
    routing = find_routing(session, line.tenant_id, call.id)
    state = (routing.screening_state or {}) if routing else {}
    shown = obs.displayed_caller_id if obs else None
    caller = shown if shown and shown.startswith("+") and shown[1:].isdigit() else None
    attestation = (obs.attestation_metadata or {}).get("network_attestation") if obs else None
    return CallContext(
        caller=caller,
        line_e164=line.e164,
        hop_count=routing.hop_count if routing else 0,
        diversions=tuple(state.get("diversions", ())),
        attestation=attestation,
    )


def processing_for(
    session: Session, settings: Settings, line: ProtectedLine, config: LineRoutingConfig
) -> ProcessingDecision:
    return evaluate_processing(
        settings, line.routing_config or {}, line_consents(session, line), config.jurisdiction
    )


def instruction_for(
    adapter: ProviderAdapter, call_id: str, decision: RoutingDecision
) -> RouteInstruction:
    if decision.op == "transfer" and decision.target:
        return adapter.transfer(call_id, decision.target, decision.reason)
    if decision.op == "hangup":
        return adapter.hangup(call_id, decision.reason)
    if decision.op == "answer":
        return adapter.answer(call_id)
    return adapter.voicemail(call_id, decision.reason)


def disposition_for(decision: RoutingDecision) -> CallDisposition:
    if decision.action == RoutingAction.FALLBACK:
        return CallDisposition.FALLBACK_ROUTED
    return _OP_DISPOSITION.get(decision.op, CallDisposition.RECEIVED)


def store_routing(
    session: Session,
    call: CallSession,
    decision: RoutingDecision,
    hop_count: int | None,
    processing: ProcessingDecision,
    screening_active: bool,
    diversions: tuple[str, ...] | None = None,
) -> CallRouting:
    routing = find_routing(session, call.tenant_id, call.id)
    state: dict[str, Any] = {"op": decision.op}
    if diversions is not None:
        state["diversions"] = list(diversions)
    if routing is None:
        routing = CallRouting(tenant_id=call.tenant_id, session_id=call.id, action="", reason="")
        session.add(routing)
    routing.action = decision.action.value
    routing.reason = decision.reason
    routing.target_kind = "USER" if decision.target else "NONE"
    routing.rule_id = decision.rule_id
    if hop_count is not None:
        routing.hop_count = hop_count
    routing.processing_mode = processing.mode.value
    routing.screening_active = screening_active
    routing.screening_state = {**(routing.screening_state or {}), **state}
    call.disposition = disposition_for(decision).value
    return routing


def _insert_call(session: Session, line: ProtectedLine, event: ProviderCallEvent) -> CallSession:
    call = CallSession(
        tenant_id=line.tenant_id,
        line_id=line.id,
        provider=event.provider,
        provider_call_id=event.call_id or "",
        direction=CallDirection.INBOUND.value,
        started_at=event.occurred_at or utcnow(),
        disposition=CallDisposition.RECEIVED.value,
    )
    with session.begin_nested():
        session.add(call)
    return call


def _record_facts(
    session: Session, line: ProtectedLine, call: CallSession, event: ProviderCallEvent
) -> Incident:
    session.add(
        CallerObservation(
            tenant_id=line.tenant_id,
            session_id=call.id,
            displayed_caller_id=event.from_number,
            attestation_metadata={
                "attestation_level": parse_attestation_level(event.attestation),
                "network_attestation": event.attestation,
                "is_verdict": False,
                "note": "Network-reported attestation only; not a finding about the caller.",
            },
            source=event.provider,
            label=EvidenceLabel.OBSERVED.value,
        )
    )
    incident = Incident(
        tenant_id=line.tenant_id,
        call_session_id=call.id,
        line_id=line.id,
        displayed_caller_id=event.from_number,
        evidence_label=EvidenceLabel.OBSERVED.value,
        summary="Inbound call observed on protected line",
        occurred_at=call.started_at,
    )
    session.add(incident)
    session.flush()
    return incident


def _emit_received(
    session: Session,
    line: ProtectedLine,
    call: CallSession,
    incident: Incident,
    event: ProviderCallEvent,
) -> None:
    key = make_idempotency_key("call", event.provider, event.call_id, "received")
    payload = {
        "call_session_id": str(call.id),
        "incident_id": str(incident.id),
        "line_id": str(line.id),
        "caller_masked": mask_phone(event.from_number),
        "provider_event_id": event.event_id,
    }
    _, created = append_event(
        session,
        line.tenant_id,
        Topic.CALL_RECEIVED,
        payload,
        EventOrigin.PROVIDER_WEBHOOK,
        key,
        provider_ref=event.event_id,
    )
    if created:
        enqueue(
            session,
            line.tenant_id,
            INCIDENT_JOB_KIND,
            {"incident_id": str(incident.id)},
            idempotency_key=f"incident:{incident.id}",
        )


def _outcome_from_existing(
    session: Session,
    line: ProtectedLine,
    call: CallSession,
    event: ProviderCallEvent,
    adapter: ProviderAdapter,
) -> IngestOutcome:
    incident = session.execute(
        select(Incident).where(
            Incident.tenant_id == line.tenant_id, Incident.call_session_id == call.id
        )
    ).scalar_one_or_none()
    routing = find_routing(session, line.tenant_id, call.id)
    out = IngestOutcome("duplicate", call.id, incident.id if incident else None)
    if routing is not None and event.call_id:
        out.instructions.append(replay_instruction(line, routing, event.call_id, adapter))
        if routing.screening_active:
            out.instructions.append(adapter.play(event.call_id, DISCLOSURE))
    return out


def replay_instruction(
    line: ProtectedLine, routing: CallRouting, call_id: str, adapter: ProviderAdapter
) -> RouteInstruction:
    config = parse_routing_config(line.routing_config)
    op = str((routing.screening_state or {}).get("op", "voicemail"))
    target = config.user_number if op == "transfer" else None
    decision = RoutingDecision(RoutingAction(routing.action), op, routing.reason, target)
    return instruction_for(adapter, call_id, decision)


def handle_call_received(
    session: Session,
    settings: Settings,
    line: ProtectedLine,
    event: ProviderCallEvent,
    adapter: ProviderAdapter,
) -> IngestOutcome:
    assert event.call_id is not None
    existing = find_call(session, line.tenant_id, event.provider, event.call_id)
    if existing is not None:
        return _outcome_from_existing(session, line, existing, event, adapter)
    try:
        call = _insert_call(session, line, event)
    except IntegrityError:  # concurrent duplicate delivery lost the race
        existing = find_call(session, line.tenant_id, event.provider, event.call_id)
        if existing is None:
            raise
        return _outcome_from_existing(session, line, existing, event, adapter)
    incident = _record_facts(session, line, call, event)
    _emit_received(session, line, call, incident, event)
    decision, processing = route_new_call(session, settings, line, call, event)
    routing_row = find_routing(session, line.tenant_id, call.id)
    if routing_row is not None:
        routing_row.to_e164 = line.e164
        routing_row.from_displayed = event.from_number
    instructions = [instruction_for(adapter, event.call_id, decision)]
    if decision.screens:
        instructions.append(adapter.play(event.call_id, DISCLOSURE))
    logger.info(
        "call received line=%s caller=%s route=%s reason=%s",
        line.id,
        mask_phone(event.from_number),
        decision.action.value,
        decision.reason,
    )
    return IngestOutcome("accepted", call.id, incident.id, instructions, processing.mode.value)


def route_new_call(
    session: Session,
    settings: Settings,
    line: ProtectedLine,
    call: CallSession,
    event: ProviderCallEvent,
) -> tuple[RoutingDecision, ProcessingDecision]:
    config = parse_routing_config(line.routing_config)
    processing = processing_for(session, settings, line, config)
    record_call_decision(session, line, call, processing)
    contacts, rules = load_routing_inputs(session, line)
    screening_allowed = line.screening_enabled and processing.allow_live_stt
    decision = decide_route(config, contacts, rules, build_context(line, event), screening_allowed)
    store_routing(
        session,
        call,
        decision,
        event.hop_count,
        processing,
        decision.screens,
        tuple(d for d in event.diversions if d),
    )
    append_event(
        session,
        line.tenant_id,
        Topic.CALL_ROUTED,
        {
            "call_session_id": str(call.id),
            "action": decision.action.value,
            "reason": decision.reason,
            "rule_id": str(decision.rule_id) if decision.rule_id else None,
            "processing_mode": processing.mode.value,
            "screening_allowed": screening_allowed,
        },
        EventOrigin.SYSTEM,
        make_idempotency_key("call", call.provider, call.provider_call_id, "routed"),
    )
    record_audit(
        session,
        line.tenant_id,
        SYSTEM_ACTOR,
        "call.routed",
        {"call_id": str(call.id), "action": decision.action.value, "reason": decision.reason},
    )
    return decision, processing
