"""The dialer service: the ONLY code path that places a callback.

`run_dial` re-evaluates every gate itself (so skipping the queue-time check, or inserting a
request row by hand, cannot skip them), then atomically claims the request, consumes the
single-use authorization and records the attempt (re-enforcing all caps) BEFORE ringing.
Idempotent: a request is dialed at most once, replays are no-ops.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC

from sqlalchemy import update
from sqlalchemy.orm import Session

from callshield.audit import SYSTEM_ACTOR, record_audit
from callshield.auth.scoping import scoped_get
from callshield.callback import events
from callshield.callback.authorization import consume_authorization
from callshield.callback.caps import record_attempt
from callshield.callback.config import EffectiveSettings
from callshield.callback.context import (
    InboundContext,
    UserSettingsView,
    build_gate_inputs,
    load_inbound,
    load_user_settings,
)
from callshield.callback.dialer_port import VerifiedCaller
from callshield.callback.gates import AuthorizationMode, GateName, evaluate_gates
from callshield.callback.lines import caller_id_problem, verified_caller
from callshield.callback.results import persist_result
from callshield.callback.runtime import CallbackRuntime
from callshield.callback.session import CallbackSession, Outcome, SessionConfig, SessionResult
from callshield.callback.timeutil import resolve_timezone
from callshield.core.clock import utcnow
from callshield.core.errors import PolicyBlockedError
from callshield.db.models.callback_ext import CallbackAuthorization, CallbackRequest
from callshield.db.models.tenancy import User
from callshield.worker.queue import enqueue

PLACE_JOB_KIND = "callback.place"

logger = logging.getLogger("callshield.callback")


@dataclass
class DialResult:
    status: str
    placed: bool = False
    reasons: list[str] = field(default_factory=list)


def run_dial(
    session: Session,
    rt: CallbackRuntime,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    *,
    defer_place: bool = False,
) -> DialResult:
    """Prepare (fresh gates, claim, consume authorization, count the attempt) then place.

    With `defer_place` (worker) the ring happens in a separate no-retry job, so the counted
    attempt is committed BEFORE the phone rings and a crash can never cause a second ring.
    """
    request = scoped_get(session, CallbackRequest, request_id, tenant_id)
    if request is None:
        return DialResult("NOT_FOUND")
    if request.status != "QUEUED":
        return DialResult(request.status)  # replay / cancelled / already dialed
    problems = _predial_problems(session, rt, request)
    if problems:
        return _block(session, request, problems)
    blocked = _prepare(session, rt, request)
    if blocked is not None:
        return blocked
    if defer_place:
        enqueue(
            session,
            tenant_id,
            PLACE_JOB_KIND,
            {"request_id": str(request.id)},
            idempotency_key=f"{PLACE_JOB_KIND}:{request.id}",
            max_attempts=1,
        )
        return DialResult("DIALING")
    return place_call(session, rt, tenant_id, request.id)


def _predial_problems(session: Session, rt: CallbackRuntime, request: CallbackRequest) -> list[str]:
    """Fresh gate pass at dial time. Anything unexpected is a reason to refuse."""
    if not rt.settings.callback_enabled:
        return ["CALLBACK_DISABLED"]
    if not getattr(rt.dialer, "configured", False):
        return ["DIALER_NOT_CONFIGURED"]
    tid, uid = request.tenant_id, request.user_id
    inbound = load_inbound(session, tid, uid, request.inbound_call_id)
    view = load_user_settings(session, rt.settings, tid, uid)
    auth = scoped_get(session, CallbackAuthorization, request.authorization_id, tid)
    inputs, key = build_gate_inputs(
        session,
        now=utcnow(),
        tenant_id=tid,
        user_id=uid,
        inbound=inbound,
        view=view,
        keyer=rt.keyer,
        lookup=rt.line_lookup,
        authorization=auth,
        mode=AuthorizationMode.ENFORCE,
    )
    problems = evaluate_gates(inputs).reason_codes()
    if not view.effective.callback_enabled:
        problems.append("CALLBACK_NOT_ENABLED_FOR_USER")
    if key is None or key != request.destination_key:
        problems.append("DESTINATION_CHANGED")
    if inbound is None:
        problems.append("INBOUND_NOT_FOUND")
    else:
        problem = caller_id_problem(session, tid, inbound.line)
        problems += [problem] if problem else []
    return list(dict.fromkeys(problems))


def _block(session: Session, request: CallbackRequest, reasons: list[str]) -> DialResult:
    request.status = "BLOCKED"
    request.open_slot = None
    request.updated_at = utcnow()
    request.gate_report = {**request.gate_report, "predial_blocked_by": reasons}
    payload = {"request_id": str(request.id), "reasons": reasons, "stage": "predial"}
    events.emit(session, request.tenant_id, events.BLOCKED, payload, request.id, "predial")
    record_audit(session, request.tenant_id, SYSTEM_ACTOR, "callback.dial.refused", payload)
    session.flush()
    return DialResult("BLOCKED", reasons=reasons)


def _claim(session: Session, request: CallbackRequest) -> bool:
    """Compare-and-set QUEUED -> DIALING: exactly one worker can win."""
    result = session.execute(
        update(CallbackRequest)
        .where(CallbackRequest.id == request.id, CallbackRequest.status == "QUEUED")
        .values(status="DIALING", updated_at=utcnow())
        .execution_options(synchronize_session="fetch")
    )
    return bool(getattr(result, "rowcount", 0) == 1)


def _prepare(session: Session, rt: CallbackRuntime, request: CallbackRequest) -> DialResult | None:
    """Claim + consume + count. Returns a DialResult only when it ended up blocked."""
    tid, uid = request.tenant_id, request.user_id
    if not _claim(session, request):
        return DialResult("DIALING")
    session.refresh(request)
    if not consume_authorization(session, tid, request.authorization_id):
        return _block(session, request, ["AUTHORIZATION_USED"])
    view = load_user_settings(session, rt.settings, tid, uid)
    try:
        record_attempt(
            session,
            tid,
            uid,
            request.id,
            request.inbound_call_id,
            request.destination_key,
            view.effective,
            utcnow(),
        )
    except PolicyBlockedError as exc:
        return _block(session, request, [exc.code])
    payload = {"request_id": str(request.id), "destination": request.destination_masked}
    events.emit(session, tid, events.STARTED, payload, request.id)
    return None


_RECHECK_GATES = frozenset(
    {
        GateName.INBOUND_RECORD,
        GateName.ATTESTATION_A,
        GateName.WITHIN_WINDOW,
        GateName.NOT_BLOCKLISTED,
        GateName.CALLING_HOURS,
    }
)


def _place_problems(session: Session, rt: CallbackRuntime, request: CallbackRequest) -> list[str]:
    """Last look immediately before ringing. The authorization is consumed and the attempt is
    already counted, so only the time/identity/blocklist gates are re-evaluated."""
    tid, uid = request.tenant_id, request.user_id
    inbound = load_inbound(session, tid, uid, request.inbound_call_id)
    view = load_user_settings(session, rt.settings, tid, uid)
    inputs, key = build_gate_inputs(
        session,
        now=utcnow(),
        tenant_id=tid,
        user_id=uid,
        inbound=inbound,
        view=view,
        keyer=rt.keyer,
        lookup=rt.line_lookup,
        authorization=None,
        mode=AuthorizationMode.PREVIEW,
    )
    report = evaluate_gates(inputs)
    problems = [r.reason for r in report.failures if r.gate in _RECHECK_GATES]
    if key is None or key != request.destination_key:
        problems.append("DESTINATION_CHANGED")
    if inbound is not None:
        problem = caller_id_problem(session, tid, inbound.line)
        problems += [problem] if problem else []
    return problems


def place_call(
    session: Session, rt: CallbackRuntime, tenant_id: uuid.UUID, request_id: uuid.UUID
) -> DialResult:
    """Ring the phone for a prepared (DIALING) request. At most once per request."""
    request = scoped_get(session, CallbackRequest, request_id, tenant_id)
    if request is None or request.status != "DIALING" or request.placed_at is not None:
        return DialResult(request.status if request else "NOT_FOUND")
    problems = _place_problems(session, rt, request)
    if problems:
        return _block(session, request, problems)
    inbound = load_inbound(session, tenant_id, request.user_id, request.inbound_call_id)
    assert inbound is not None and inbound.destination is not None
    caller = verified_caller(session, tenant_id, inbound.line)
    started = utcnow()
    request.placed_at = started
    session.flush()
    view = load_user_settings(session, rt.settings, tenant_id, request.user_id)
    result = _run_call(session, rt, request, inbound, view, caller)
    persist_result(session, rt, request, inbound, result, started, utcnow())
    return DialResult(request.status, placed=True)


def _run_call(
    session: Session,
    rt: CallbackRuntime,
    request: CallbackRequest,
    inbound: InboundContext,
    view: UserSettingsView,
    caller: VerifiedCaller,
) -> SessionResult:
    eff = view.effective
    assert inbound.destination is not None
    try:
        handle = rt.dialer.place_callback(
            inbound.destination,
            request.id,
            caller=caller,
            max_seconds=eff.max_call_seconds,
        )
    except PolicyBlockedError:
        return SessionResult(outcome=Outcome.FAILED)
    runner = CallbackSession(handle, _session_config(session, request, inbound, view), rt.model)
    try:
        return runner.run()
    except Exception as exc:  # the call is already hung up by the session's cleanup
        logger.warning("callback session failed: %s", type(exc).__name__)
        runner.result.outcome = Outcome.FAILED
        return runner.result


def _session_config(
    session: Session, request: CallbackRequest, inbound: InboundContext, view: UserSettingsView
) -> SessionConfig:
    eff: EffectiveSettings = view.effective
    user = scoped_get(session, User, request.user_id, request.tenant_id)
    started = inbound.call.started_at or utcnow()
    call_time = started.astimezone(resolve_timezone(view.timezone) or UTC)
    return SessionConfig(
        template_id=eff.disclosure_template_id,
        first_name=user.display_name if user else "",  # reduced to a first name when rendered
        call_time=call_time,
        outbound_recording=request.outbound_recording,
        listen_window_seconds=eff.listen_window_seconds,
        bare_greeting_disclose_ms=eff.bare_greeting_disclose_ms,
        max_question_turns=eff.max_question_turns,
        max_call_seconds=eff.max_call_seconds,
    )
