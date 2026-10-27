"""Screening turn handling: caller speech -> claims + classification -> deterministic route.

Gates, in order, evaluated freshly on every turn (consent can be revoked and screening can
be disabled mid-call): line screening flag, processing permission, then classification.
If a gate is closed the call returns to ordinary provider-only routing and the speech is
discarded: never stored, never sent to a model.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.audit import SYSTEM_ACTOR, record_audit
from callshield.core.config import Settings
from callshield.core.errors import ConflictError, NotFoundError
from callshield.core.idempotency import make_idempotency_key
from callshield.db.models.defend import (
    CallSession,
    Classification,
    Incident,
    OrganizationClaim,
    ProtectedLine,
)
from callshield.db.models.defend_ext import CallRouting
from callshield.defend.consent import record_call_decision
from callshield.defend.lifecycle import (
    IngestOutcome,
    context_for_call,
    find_call,
    find_routing,
    instruction_for,
    load_routing_inputs,
    processing_for,
    replay_instruction,
    store_routing,
)
from callshield.domain.enums import EventOrigin, EvidenceLabel, StatementType, VerificationStatus
from callshield.domain.topics import Topic
from callshield.ledger import append_event
from callshield.llm.client import LLMClient
from callshield.policy.config import parse_routing_config
from callshield.policy.processing import ProcessingDecision
from callshield.policy.routing import RoutingDecision, decide_route, fallback_decision
from callshield.screening.decision import decide_after_screening
from callshield.screening.extract import extract_claims
from callshield.screening.refine import ClassificationOutcome, classify_statement
from callshield.telephony.base import ProviderAdapter, ProviderCallEvent

logger = logging.getLogger("callshield.defend")


def _screening_call(
    session: Session, line: ProtectedLine, event: ProviderCallEvent
) -> tuple[CallSession, CallRouting]:
    if event.call_id is None:
        raise NotFoundError("unknown call", code="UNKNOWN_CALL")
    call = find_call(session, line.tenant_id, event.provider, event.call_id)
    if call is None or call.line_id != line.id:
        raise NotFoundError("unknown call", code="UNKNOWN_CALL")
    routing = find_routing(session, line.tenant_id, call.id)
    if routing is None:
        raise ConflictError("call is not being screened", code="NOT_SCREENING")
    return call, routing


def _already_final(
    line: ProtectedLine, call: CallSession, routing: CallRouting, adapter: ProviderAdapter
) -> IngestOutcome:
    """Screening already concluded (duplicate/late event): replay the stored decision."""
    out = IngestOutcome("duplicate", call.id, detail="screening_already_concluded")
    out.instructions.append(replay_instruction(line, routing, call.provider_call_id, adapter))
    return out


def _apply(
    session: Session,
    adapter: ProviderAdapter,
    call: CallSession,
    decision: RoutingDecision,
    processing: ProcessingDecision,
    say: str | None = None,
) -> IngestOutcome:
    store_routing(session, call, decision, None, processing, screening_active=False)
    append_event(
        session,
        call.tenant_id,
        Topic.CALL_ROUTED,
        {
            "call_session_id": str(call.id),
            "action": decision.action.value,
            "reason": decision.reason,
            "stage": "post_screening",
        },
        EventOrigin.SYSTEM,
        make_idempotency_key("call", call.provider, call.provider_call_id, "routed-final"),
    )
    out = IngestOutcome("accepted", call.id, detail=decision.reason)
    if say:
        out.instructions.append(adapter.play(call.provider_call_id, say))
    out.instructions.append(instruction_for(adapter, call.provider_call_id, decision))
    return out


def _ordinary_routing(
    session: Session,
    settings: Settings,
    line: ProtectedLine,
    call: CallSession,
    adapter: ProviderAdapter,
    reason: str,
) -> IngestOutcome:
    """A screening gate closed mid-call: restore ordinary (non-AI) routing immediately."""
    config = parse_routing_config(line.routing_config)
    processing = processing_for(session, settings, line, config)
    record_call_decision(session, line, call, processing)
    contacts, rules = load_routing_inputs(session, line)
    decision = decide_route(config, contacts, rules, context_for_call(session, line, call), False)
    logger.info("screening gate closed reason=%s call=%s", reason, call.id)
    return _apply(session, adapter, call, decision, processing)


def _claim(
    line: ProtectedLine, call: CallSession, name: str, kind: StatementType
) -> OrganizationClaim:
    return OrganizationClaim(
        tenant_id=line.tenant_id,
        session_id=call.id,
        claimed_name=name,
        statement_type=kind.value,
        label=EvidenceLabel.CLAIMED.value,
        verification_status=VerificationStatus.UNVERIFIED.value,
    )


def _persist_claims(
    session: Session, line: ProtectedLine, call: CallSession, text: str, allow_persist: bool
) -> None:
    claims = extract_claims(text)
    name = claims.organization or claims.person
    if name:
        session.add(_claim(line, call, name, StatementType.SELLER_NAME))
    if allow_persist and claims.purpose:  # purpose text is transcript-like: consent-gated
        session.add(_claim(line, call, claims.purpose, StatementType.PURPOSE))


def _persist_classification(
    session: Session, line: ProtectedLine, call: CallSession, outcome: ClassificationOutcome
) -> None:
    row = Classification(
        tenant_id=line.tenant_id,
        call_id=call.id,
        model_version=outcome.model_version,
        label=outcome.label.value,
        confidence=outcome.confidence,
        evidence_label=EvidenceLabel.MODEL_INFERRED.value,
    )
    session.add(row)
    session.flush()
    incident = session.execute(
        select(Incident).where(
            Incident.tenant_id == line.tenant_id, Incident.call_session_id == call.id
        )
    ).scalar_one_or_none()
    if incident is not None:
        incident.label = outcome.label.value
    append_event(
        session,
        line.tenant_id,
        Topic.CLASSIFICATION_CREATED,
        {
            "call_session_id": str(call.id),
            "classification_id": str(row.id),
            "label": outcome.label.value,
            "confidence": outcome.confidence,
            "model_version": outcome.model_version,
            "evidence_label": EvidenceLabel.MODEL_INFERRED.value,
            "fallback_reason": outcome.fallback_reason,
            "injection_suspected": outcome.injection_suspected,
        },
        EventOrigin.MODEL,
        make_idempotency_key("call", call.provider, call.provider_call_id, "classified"),
    )


def handle_utterance(
    session: Session,
    settings: Settings,
    line: ProtectedLine,
    event: ProviderCallEvent,
    adapter: ProviderAdapter,
    llm: LLMClient | None,
) -> IngestOutcome:
    call, routing = _screening_call(session, line, event)
    if not routing.screening_active:
        return _already_final(line, call, routing, adapter)
    if not line.screening_enabled:
        return _ordinary_routing(session, settings, line, call, adapter, "SCREENING_DISABLED")
    config = parse_routing_config(line.routing_config)
    processing = processing_for(session, settings, line, config)
    if not processing.allow_live_stt:
        return _ordinary_routing(session, settings, line, call, adapter, "PROCESSING_DENIED")
    text = event.text or ""
    outcome = classify_statement(text, llm, config.ai_timeout_seconds)
    _persist_claims(session, line, call, text, processing.allow_transcript_persist)
    _persist_classification(session, line, call, outcome)
    ctx = context_for_call(session, line, call)
    result = decide_after_screening(config, ctx, outcome)
    return _apply(session, adapter, call, result.routing, processing, result.say)


def handle_screening_failure(
    session: Session,
    settings: Settings,
    line: ProtectedLine,
    event: ProviderCallEvent,
    adapter: ProviderAdapter,
) -> IngestOutcome:
    """AI latency exceeded / stream lost: configured fallback, never a dropped call."""
    call, routing = _screening_call(session, line, event)
    if not routing.screening_active:
        return _already_final(line, call, routing, adapter)
    config = parse_routing_config(line.routing_config)
    processing = processing_for(session, settings, line, config)
    ctx = context_for_call(session, line, call)
    decision = fallback_decision(config, ctx, "AI_FAILURE_OR_TIMEOUT")
    append_event(
        session,
        line.tenant_id,
        Topic.ROUTING_FALLBACK,
        {"call_session_id": str(call.id), "reason": event.event_type.value, "op": decision.op},
        EventOrigin.SYSTEM,
        make_idempotency_key("call", call.provider, call.provider_call_id, "fallback"),
    )
    record_audit(
        session,
        line.tenant_id,
        SYSTEM_ACTOR,
        "screening.fallback",
        {"call_id": str(call.id), "op": decision.op},
    )
    return _apply(session, adapter, call, decision, processing)
