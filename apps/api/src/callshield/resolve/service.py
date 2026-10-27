"""start-contact: policy gate -> consume authorization -> run call -> record outcome.

Safety order (never reordered): idempotent replay check, deterministic policy evaluation
(every refusal audited), atomic single-use authorization consumption, attempt row committed
BEFORE dialing, call run through the bounded controller, conservative outcome mapping.
No automatic retries exist anywhere in this module.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from callshield.agent.context import AgentSessionContext
from callshield.agent.controller import CallController, CallRunResult
from callshield.agent.dialogue import DialogueModel, RuleBasedDialogueModel
from callshield.agent.gating import AiGate, evaluate_ai_gate
from callshield.agent.templates import (
    OUTBOUND_OPT_OUT,
    POLICY_VERSION,
    TEMPLATE_VERSION,
    versioned_id,
)
from callshield.audit import actor_for_user, record_audit
from callshield.auth.scoping import scoped_get, scoped_select
from callshield.core.clock import utcnow
from callshield.core.config import Settings
from callshield.core.errors import ConflictError
from callshield.core.idempotency import command_key
from callshield.core.phone import mask_phone
from callshield.db.models.defend import CallSession, ProtectedLine
from callshield.db.models.resolve import (
    ContactAttempt,
    ContactAuthorization,
    ContactDestination,
    OptOutEvent,
    ResolutionCase,
)
from callshield.db.models.resolve_ext import AttemptTranscriptLine, ContactAttemptDetail
from callshield.domain.enums import (
    CallDirection,
    ContactOutcome,
    EvidenceLabel,
    OptOutStatus,
    ResolutionState,
    Role,
)
from callshield.domain.topics import Topic
from callshield.ivr.navigator import IvrNavigator, NavLimits
from callshield.ivr.paths import load_approved_path, record_path_result
from callshield.resolve import attempts, authorizations, cases, machine
from callshield.resolve.dialer import CallHandle, OutboundDialer
from callshield.resolve.events import add_timeline, emit
from callshield.resolve.next_steps import persist_next_steps, recommend_next_steps
from callshield.resolve.outcomes import AttemptOutcome, derive_outcome
from callshield.resolve.policy import (
    AuthorizationFacts,
    Decision,
    OutboundPolicy,
    PolicyInput,
    Reason,
)

log = logging.getLogger(__name__)
_DAY = timedelta(hours=24)


@dataclass(frozen=True)
class StartContactCommand:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role: Role
    case_id: uuid.UUID
    destination_id: uuid.UUID
    authorization_id: uuid.UUID | None
    idempotency_key: str | None
    reason: str = ""
    mode: str = "AI"  # requested; falls back to HUMAN unless the AI gate allows


@dataclass
class StartContactResult:
    decision: Decision
    attempt: ContactAttempt | None = None
    replayed: bool = False
    outcome: AttemptOutcome | None = None
    ai_gate: AiGate | None = None


def _policy_input(
    session: Session,
    settings: Settings,
    dialer: OutboundDialer,
    cmd: StartContactCommand,
    case: ResolutionCase,
    dest: ContactDestination | None,
    auth: ContactAuthorization | None,
) -> PolicyInput:
    now = utcnow()
    line = scoped_get(session, ProtectedLine, case.protected_line_id, cmd.tenant_id)
    since = now - _DAY
    return PolicyInput(
        now=now,
        outbound_enabled=settings.outbound_enabled,
        dialer_configured=dialer.configured,
        idempotency_key=cmd.idempotency_key,
        actor_user_id=cmd.user_id,
        actor_role=cmd.role,
        case_id=case.id,
        case_owner_user_id=case.owner_user_id,
        case_state=case.status,
        case_policy_region=case.policy_region,
        line_verified=bool(line and line.verification_status == "VERIFIED"),
        destination_id=cmd.destination_id,
        destination=cases.destination_facts(session, cmd.tenant_id, dest) if dest else None,
        authorization_id=cmd.authorization_id,
        authorization=None
        if auth is None
        else AuthorizationFacts(
            auth.case_id,
            auth.destination_id,
            auth.user_id,
            auth.scope,
            auth.expires_at,
            auth.consumed_at,
        ),
        last_attempt_at=attempts.last_system_attempt_time(session, cmd.tenant_id, case.id),
        attempts_case_24h=attempts.count_system_attempts(session, cmd.tenant_id, since, case.id),
        attempts_tenant_24h=attempts.count_system_attempts(session, cmd.tenant_id, since),
        active_attempt_on_case=attempts.active_attempt_exists(
            session, cmd.tenant_id, case_id=case.id
        ),
        active_attempt_on_destination=bool(dest)
        and attempts.active_attempt_exists(
            session, cmd.tenant_id, phone=dest.phone if dest else None
        ),
        cooldown_seconds=settings.outbound_cooldown_seconds,
        max_attempts_per_case_per_day=settings.max_attempts_per_case_per_day,
        max_attempts_per_tenant_per_day=settings.max_attempts_per_tenant_per_day,
        max_call_seconds=settings.max_call_seconds,
    )


def _find_replay(session: Session, cmd: StartContactCommand, key: str) -> ContactAttempt | None:
    found = session.execute(
        scoped_select(ContactAttempt, cmd.tenant_id).where(ContactAttempt.idempotency_key == key)
    ).scalar_one_or_none()
    if found is not None and found.case_id != cmd.case_id:
        raise ConflictError(
            "Idempotency-Key reused with a different request", code="IDEMPOTENCY_KEY_REUSED"
        )
    return found


def _record_refusal(
    session: Session, cmd: StartContactCommand, case: ResolutionCase, decision: Decision
) -> None:
    actor = actor_for_user(cmd.user_id)
    codes = list(decision.codes)
    payload = {
        "case_id": str(case.id),
        "destination_id": str(cmd.destination_id),
        "authorization_id": str(cmd.authorization_id) if cmd.authorization_id else None,
        "reasons": codes,
    }
    record_audit(session, cmd.tenant_id, actor, "resolve.start_contact.refused", payload)
    add_timeline(session, cmd.tenant_id, case.id, "contact.blocked", actor, {"reasons": codes})
    emit(
        session,
        cmd.tenant_id,
        Topic.CONTACT_BLOCKED,
        payload,
        ("contact.blocked", case.id, cmd.user_id, uuid.uuid4().hex[:12]),
    )
    session.commit()


def start_contact(
    session: Session,
    settings: Settings,
    dialer: OutboundDialer,
    cmd: StartContactCommand,
    model: DialogueModel | None = None,
) -> StartContactResult:
    """Run ONE user-authorized contact attempt. Returns a refusal Decision instead of dialing
    when any policy rule fails. Commits (refusal audit, pre-dial attempt, final outcome)."""
    case = cases.get_case(session, cmd.tenant_id, cmd.case_id)
    key = command_key(cmd.user_id, f"start-contact:{case.id}", cmd.idempotency_key or "")
    if cmd.idempotency_key:
        replay = _find_replay(session, cmd, key)
        if replay is not None:
            return StartContactResult(Decision(True), replay, replayed=True)
    dest = scoped_get(session, ContactDestination, cmd.destination_id, cmd.tenant_id)
    auth = (
        scoped_get(session, ContactAuthorization, cmd.authorization_id, cmd.tenant_id)
        if cmd.authorization_id
        else None
    )
    decision = OutboundPolicy.evaluate(
        _policy_input(session, settings, dialer, cmd, case, dest, auth)
    )
    if not decision.allowed or dest is None or auth is None:
        _record_refusal(session, cmd, case, decision)
        return StartContactResult(decision)
    attempt = _begin_attempt(session, settings, cmd, case, dest, auth, key, dialer)
    if attempt is None:
        lost = Decision(
            False,
            (Reason("AUTHORIZATION_CONSUMED", "Authorization was already used (single use)."),),
        )
        _record_refusal(session, cmd, case, lost)
        return StartContactResult(lost)
    gate = _ai_gate(session, settings, cmd, case)
    try:
        outcome = _run_and_finalize(
            session,
            settings,
            dialer,
            cmd,
            case,
            dest,
            attempt,
            decision.max_call_seconds,
            gate,
            model,
        )
    except Exception:
        session.rollback()
        _close_failed(session, cmd, case.id, attempt.id)
        raise
    session.commit()
    return StartContactResult(decision, attempt, outcome=outcome, ai_gate=gate)


def _ai_gate(
    session: Session, settings: Settings, cmd: StartContactCommand, case: ResolutionCase
) -> AiGate:
    if cmd.mode != "AI":
        return AiGate(False, "AI_NOT_REQUESTED")
    return evaluate_ai_gate(settings, session, cmd.tenant_id, case.protected_line_id)


def _begin_attempt(
    session: Session,
    settings: Settings,
    cmd: StartContactCommand,
    case: ResolutionCase,
    dest: ContactDestination,
    auth: ContactAuthorization,
    key: str,
    dialer: OutboundDialer,
) -> ContactAttempt | None:
    """Consume the authorization atomically and persist the attempt BEFORE dialing."""
    if not authorizations.consume_authorization(session, cmd.tenant_id, auth.id):
        return None
    now = utcnow()
    attempt = ContactAttempt(
        tenant_id=cmd.tenant_id,
        case_id=case.id,
        destination_id=dest.id,
        user_authorization_id=auth.id,
        reason=cmd.reason or "user-initiated contact",
        started_at=now,
        ivr_route=[],
        agent_claims=[],
        mode="HUMAN",
        policy_state={
            "policy_version": POLICY_VERSION,
            "template_version": TEMPLATE_VERSION,
            "decision": "ALLOWED",
            "max_call_seconds": settings.max_call_seconds,
        },
        idempotency_key=key,
    )
    try:
        with session.begin_nested():
            session.add(attempt)
            session.flush()
            session.add(
                ContactAttemptDetail(
                    tenant_id=cmd.tenant_id,
                    attempt_id=attempt.id,
                    case_id=case.id,
                    source=attempts.SOURCE_SYSTEM,
                    evidence_label=EvidenceLabel.OBSERVED.value,
                    authorization_id=auth.id,
                    dialed_phone_masked=mask_phone(dest.phone),
                    policy_version=POLICY_VERSION,
                    template_version=TEMPLATE_VERSION,
                    dialer_kind=dialer.kind,
                )
            )
    except IntegrityError:
        session.rollback()
        return None
    actor = actor_for_user(cmd.user_id)
    machine.transition(
        session,
        case,
        ResolutionState.IN_PROGRESS,
        actor=actor,
        attempt_id=attempt.id,
        context={
            "policy_approved": True,
            "user_authorization_id": str(auth.id),
            "destination_independently_verified": True,
        },
    )
    payload = {
        "case_id": str(case.id),
        "attempt_id": str(attempt.id),
        "authorization_id": str(auth.id),
        "phone": mask_phone(dest.phone),
    }
    add_timeline(session, cmd.tenant_id, case.id, "contact.started", actor, payload, attempt.id)
    emit(session, cmd.tenant_id, Topic.CONTACT_STARTED, payload, ("contact.started", attempt.id))
    record_audit(session, cmd.tenant_id, actor, "resolve.contact.started", payload)
    _append_opt_out(
        session,
        cmd.tenant_id,
        case.id,
        attempt.id,
        OptOutStatus.DRAFTED,
        EvidenceLabel.OBSERVED,
        None,
    )
    session.commit()
    return attempt


def _append_opt_out(
    session: Session,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    attempt_id: uuid.UUID,
    status: OptOutStatus,
    label: EvidenceLabel,
    previous: OptOutEvent | None,
) -> OptOutEvent:
    row = OptOutEvent(
        tenant_id=tenant_id,
        case_id=case_id,
        attempt_id=attempt_id,
        request_text_version=versioned_id(OUTBOUND_OPT_OUT),
        status=status.value,
        evidence_label=label.value,
        supersedes_id=previous.id if previous else None,
        acknowledged_at=utcnow() if status is OptOutStatus.ACKNOWLEDGED else None,
    )
    session.add(row)
    session.flush()
    return row


def _close_failed(
    session: Session, cmd: StartContactCommand, case_id: uuid.UUID, attempt_id: uuid.UUID
) -> None:
    attempt = session.get(ContactAttempt, attempt_id)
    case = session.get(ResolutionCase, case_id)
    if attempt is None or case is None:
        return
    attempt.ended_at = utcnow()
    attempt.outcome = ContactOutcome.DROPPED.value
    attempt.policy_state = {**attempt.policy_state, "end_reason": "INTERNAL_ERROR"}
    if case.status == ResolutionState.IN_PROGRESS.value:
        machine.transition(
            session,
            case,
            ResolutionState.INTERRUPTED,
            actor=actor_for_user(cmd.user_id),
            attempt_id=attempt_id,
        )
    session.commit()


def _build_context(
    session: Session,
    cmd: StartContactCommand,
    case: ResolutionCase,
    dest: ContactDestination,
    handle_session: CallSession,
    handle: CallHandle,
    ai_allowed: bool,
) -> AgentSessionContext:
    return AgentSessionContext(
        session_id=handle_session.id,
        case_id=case.id,
        destination_id=dest.id,
        handle=handle,
        case_summary={
            "case_id": str(case.id),
            "objective": case.objective,
            "claimed_entity": case.claimed_entity,
            "claimed_entity_label": "CLAIMED",
            "policy_region": case.policy_region,
        },
        destination_info={
            "destination_id": str(dest.id),
            "provenance": dest.provenance,
            "verification_status": dest.verification_status,
            "phone_masked": mask_phone(dest.phone),
        },
        approved_path=load_approved_path(session, cmd.tenant_id, dest.id),
        ai_allowed=ai_allowed,
    )


def _run_and_finalize(
    session: Session,
    settings: Settings,
    dialer: OutboundDialer,
    cmd: StartContactCommand,
    case: ResolutionCase,
    dest: ContactDestination,
    attempt: ContactAttempt,
    max_seconds: int,
    gate: AiGate,
    model: DialogueModel | None,
) -> AttemptOutcome:
    call = CallSession(
        tenant_id=cmd.tenant_id,
        line_id=case.protected_line_id,
        provider=dialer.kind,
        provider_call_id=f"attempt-{attempt.id}",
        direction=CallDirection.OUTBOUND.value,
        started_at=utcnow(),
        disposition="UNKNOWN",
    )
    session.add(call)
    session.flush()
    attempt.call_session_id = call.id
    attempt.mode = "AI" if gate.allowed else "HUMAN"
    run = CallRunResult()
    nav = IvrNavigator(None)
    try:
        handle = dialer.place_call(dest.phone, call.id, max_seconds=max_seconds)
    except Exception:  # adapter failure: fail closed, record as a dropped attempt
        log.warning("dialer error for %s", mask_phone(dest.phone))
        run.setup_failed = True
        run.end_reason = "DIALER_ERROR"
        return _finalize(session, cmd, case, dest, attempt, call, None, run, nav, gate)
    ctx = _build_context(session, cmd, case, dest, call, handle, gate.allowed)
    nav = IvrNavigator(ctx.approved_path, NavLimits(max_total_seconds=float(max_seconds)))
    run = CallController(ctx, nav, model or RuleBasedDialogueModel(), max_seconds=max_seconds).run()
    return _finalize(session, cmd, case, dest, attempt, call, ctx, run, nav, gate)


def _finalize(
    session: Session,
    cmd: StartContactCommand,
    case: ResolutionCase,
    dest: ContactDestination,
    attempt: ContactAttempt,
    call: CallSession,
    ctx: AgentSessionContext | None,
    run: CallRunResult,
    nav: IvrNavigator,
    gate: AiGate,
) -> AttemptOutcome:
    if ctx is None:
        outcome = AttemptOutcome(ContactOutcome.DROPPED, (ResolutionState.INTERRUPTED,), False)
    else:
        outcome = derive_outcome(run, ctx)
    now = utcnow()
    attempt.ended_at = now
    attempt.outcome = outcome.outcome.value
    attempt.ivr_route = list(nav.route)
    if ctx is not None:
        _store_transcript(session, cmd.tenant_id, attempt.id, ctx)
        attempt.agent_claims = [c.as_dict() for c in ctx.claims] + [
            {"type": "DISPOSITION", **d} for d in ctx.dispositions
        ]
    attempt.policy_state = {
        **attempt.policy_state,
        "end_reason": run.end_reason,
        "stop_condition": str(run.stop_condition) if run.stop_condition else None,
        "parked_for_review": run.parked_for_review,
        "ai_gate": gate.reason or "ALLOWED",
        "ivr_failure": run.ivr_failure,
        "ivr_proposal": run.ivr_proposal,
        "tool_calls": len(ctx.tool_log) if ctx else 0,
        "rejected_tool_calls": len(ctx.rejected_tool_calls) if ctx else 0,
        "dtmf_sent": len(ctx.dtmf_sent) if ctx else 0,
    }
    detail = session.execute(
        scoped_select(ContactAttemptDetail, cmd.tenant_id).where(
            ContactAttemptDetail.attempt_id == attempt.id
        )
    ).scalar_one()
    detail.stop_condition = str(run.stop_condition) if run.stop_condition else None
    detail.parked_for_review = run.parked_for_review
    call.ended_at = now
    call.disposition = "ANSWERED_BY_USER" if run.human_connected else "UNKNOWN"
    _record_results(session, cmd, case, attempt, ctx, run, outcome, nav)
    return outcome


def _store_transcript(
    session: Session, tenant_id: uuid.UUID, attempt_id: uuid.UUID, ctx: AgentSessionContext
) -> None:
    for entry in ctx.transcript:
        session.add(
            AttemptTranscriptLine(
                tenant_id=tenant_id,
                attempt_id=attempt_id,
                seq=entry.seq,
                speaker=entry.speaker,
                text=entry.text,
                label=entry.label.value,
                complete=entry.complete,
                offset_seconds=entry.offset_seconds,
            )
        )


def _record_results(
    session: Session,
    cmd: StartContactCommand,
    case: ResolutionCase,
    attempt: ContactAttempt,
    ctx: AgentSessionContext | None,
    run: CallRunResult,
    outcome: AttemptOutcome,
    nav: IvrNavigator,
) -> None:
    actor = actor_for_user(cmd.user_id)
    tenant = cmd.tenant_id
    for index, step in enumerate(nav.route):
        emit(
            session,
            tenant,
            Topic.IVR_OPTION_SELECTED,
            {"attempt_id": str(attempt.id), **step},
            ("ivr.option.selected", attempt.id, index),
        )
    previous = None
    for status in outcome.opt_out_statuses:
        label = (
            EvidenceLabel.CLAIMED
            if status in (OptOutStatus.ACKNOWLEDGED, OptOutStatus.REFUSED)
            else EvidenceLabel.OBSERVED
        )
        previous = _append_opt_out(
            session,
            tenant,
            case.id,
            attempt.id,
            status,
            label,
            previous or _latest_opt_out(session, tenant, attempt.id),
        )
    for state in outcome.states:
        machine.transition(session, case, state, actor=actor, attempt_id=attempt.id)
    base = {
        "case_id": str(case.id),
        "attempt_id": str(attempt.id),
        "outcome": outcome.outcome.value,
    }
    if outcome.delivered:
        emit(session, tenant, Topic.REQUEST_DELIVERED, base, ("request.delivered", attempt.id))
    if ctx is not None and ctx.verified_ack is not None and outcome.delivered:
        emit(
            session,
            tenant,
            Topic.REQUEST_ACKNOWLEDGED,
            {**base, "label": "CLAIMED", "note": "acknowledgment is not suppression"},
            ("request.acknowledged", attempt.id),
        )
    emit(
        session,
        tenant,
        Topic.CONTACT_ENDED,
        {
            **base,
            "end_reason": run.end_reason,
            "stop_condition": str(run.stop_condition) if run.stop_condition else None,
        },
        ("contact.ended", attempt.id),
    )
    if run.stop_condition is not None:
        emit(
            session,
            tenant,
            Topic.CONTACT_ABORTED,
            {**base, "stop": str(run.stop_condition)},
            ("contact.aborted", attempt.id),
        )
    add_timeline(
        session,
        tenant,
        case.id,
        "contact.ended",
        actor,
        {
            **base,
            "end_reason": run.end_reason,
            "parked_for_review": run.parked_for_review,
            "stop_condition": str(run.stop_condition) if run.stop_condition else None,
        },
        attempt.id,
    )
    steps = recommend_next_steps(outcome.outcome)
    persist_next_steps(session, tenant, case.id, attempt.id, steps)
    if nav.path is not None and nav.path.path_id:
        record_path_result(
            session, tenant, nav.path.path_id, ok=not run.ivr_failure and run.human_connected
        )
    record_audit(session, tenant, actor, "resolve.contact.ended", base)


def _latest_opt_out(
    session: Session, tenant_id: uuid.UUID, attempt_id: uuid.UUID
) -> OptOutEvent | None:
    stmt = scoped_select(OptOutEvent, tenant_id).where(OptOutEvent.attempt_id == attempt_id)
    rows = list(session.execute(stmt.order_by(OptOutEvent.created_at)).scalars())
    superseded = {r.supersedes_id for r in rows if r.supersedes_id}
    heads = [r for r in rows if r.id not in superseded]
    return heads[-1] if heads else None
