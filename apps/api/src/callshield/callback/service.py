"""Queue-side service: eligibility preview, one-tap authorize + queue, cancel.

The dial itself happens in `callback.dialer_service.run_dial` (worker job `callback.dial`),
which re-runs every gate. Routers stay thin and call into here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from callshield.audit import actor_for_user, record_audit
from callshield.auth.scoping import scoped_get, scoped_select
from callshield.callback import events
from callshield.callback.authorization import create_authorization
from callshield.callback.context import (
    InboundContext,
    UserSettingsView,
    build_gate_inputs,
    load_inbound,
    load_user_settings,
    masked_destination,
)
from callshield.callback.disclosure import template_has_recording_notice
from callshield.callback.gates import AuthorizationMode, GateReport, evaluate_gates
from callshield.callback.lines import caller_id_problem
from callshield.callback.runtime import CallbackRuntime
from callshield.core.clock import utcnow
from callshield.core.errors import (
    ConflictError,
    NotFoundError,
    PolicyBlockedError,
    UnprocessableError,
)
from callshield.core.idempotency import command_key
from callshield.db.models.callback_ext import CallbackAuthorization, CallbackRequest
from callshield.db.models.defend import Incident
from callshield.db.models.enforce import Case
from callshield.worker.queue import enqueue

DIAL_JOB_KIND = "callback.dial"
OPEN_STATUSES = ("QUEUED", "DIALING")


@dataclass(frozen=True)
class QueueCommand:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    inbound_call_id: uuid.UUID
    idempotency_key: str
    app_version: str
    outbound_recording: bool = False
    recording_notice_confirmed: bool = False
    incident_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Eligibility:
    report: GateReport
    caller_id_problem: str | None
    call_id: uuid.UUID

    @property
    def allowed(self) -> bool:
        return self.report.allowed and self.caller_id_problem is None

    def as_dict(self) -> dict[str, Any]:
        body = self.report.as_dict()
        body["allowed"] = self.allowed
        body["complaint_flow_offered"] = not self.allowed
        body["caller_id"] = {"ok": self.caller_id_problem is None, "reason": self.caller_id_problem}
        body["complaint_flow"] = {
            "offered": not self.allowed,
            "start": "/v1/cases",
            "inbound_call_id": str(self.call_id),
        }
        return body


def eligibility(
    session: Session,
    rt: CallbackRuntime,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    inbound_call_id: uuid.UUID,
) -> Eligibility:
    inbound = load_inbound(session, tenant_id, user_id, inbound_call_id)
    if inbound is None:
        raise NotFoundError("call not found", code="NOT_FOUND")
    view = load_user_settings(session, rt.settings, tenant_id, user_id)
    return _evaluate(
        session, rt, tenant_id, user_id, inbound, view, None, AuthorizationMode.PREVIEW
    )


def _evaluate(
    session: Session,
    rt: CallbackRuntime,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    inbound: InboundContext,
    view: UserSettingsView,
    authorization: CallbackAuthorization | None,
    mode: AuthorizationMode,
) -> Eligibility:
    inputs, _key = build_gate_inputs(
        session,
        now=utcnow(),
        tenant_id=tenant_id,
        user_id=user_id,
        inbound=inbound,
        view=view,
        keyer=rt.keyer,
        lookup=rt.line_lookup,
        authorization=authorization,
        mode=mode,
    )
    report = evaluate_gates(inputs)
    return Eligibility(report, caller_id_problem(session, tenant_id, inbound.line), inbound.call.id)


def _refuse(
    session: Session, cmd: QueueCommand, elig: Eligibility, inbound: InboundContext
) -> PolicyBlockedError:
    reasons = elig.report.reason_codes() + (
        [elig.caller_id_problem] if elig.caller_id_problem else []
    )
    payload = {"inbound_call_id": str(cmd.inbound_call_id), "reasons": reasons}
    record_audit(
        session, cmd.tenant_id, actor_for_user(cmd.user_id), "callback.queue.refused", payload
    )
    events.emit(
        session, cmd.tenant_id, events.BLOCKED, payload, cmd.inbound_call_id, uuid.uuid4().hex[:12]
    )
    session.commit()  # keep the refusal record although the request fails
    failures = elig.report.failures
    message = failures[0].message if failures else "This line cannot be used for callbacks."
    return PolicyBlockedError(
        message,
        code=reasons[0] if reasons else "POLICY_BLOCKED",
        details={"eligibility": elig.as_dict()},
    )


def _validate_recording(rt: CallbackRuntime, view: UserSettingsView, cmd: QueueCommand) -> None:
    if not cmd.outbound_recording:
        return
    if not template_has_recording_notice(view.effective.disclosure_template_id):
        raise UnprocessableError(
            "Recording is unavailable: the disclosure has no recording notice.",
            code="RECORDING_NOTICE_UNAVAILABLE",
        )
    if not cmd.recording_notice_confirmed:
        raise UnprocessableError(
            "Confirm the recording notice is in the disclosure.", code="RECORDING_NOT_CONFIRMED"
        )


def _links(
    session: Session, cmd: QueueCommand, inbound: InboundContext
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """Optional enforce links by id only (verified in-tenant; no enforce code is imported)."""
    incident_id = cmd.incident_id
    if incident_id is not None:
        incident = scoped_get(session, Incident, incident_id, cmd.tenant_id)
        if incident is None or incident.call_session_id != inbound.call.id:
            raise NotFoundError("incident not found", code="NOT_FOUND")
    else:
        found = (
            session.execute(
                scoped_select(Incident, cmd.tenant_id).where(
                    Incident.call_session_id == inbound.call.id
                )
            )
            .scalars()
            .first()
        )
        incident_id = found.id if found else None
    if cmd.case_id is not None and scoped_get(session, Case, cmd.case_id, cmd.tenant_id) is None:
        raise NotFoundError("case not found", code="NOT_FOUND")
    return incident_id, cmd.case_id


def _find_request(session: Session, tenant_id: uuid.UUID, key: str) -> CallbackRequest | None:
    return session.execute(
        scoped_select(CallbackRequest, tenant_id).where(CallbackRequest.idempotency_key == key)
    ).scalar_one_or_none()


def queue_callback(
    session: Session, rt: CallbackRuntime, cmd: QueueCommand
) -> tuple[CallbackRequest, bool]:
    """One tap: re-check gates, record the authorization, queue the dial job."""
    if not cmd.idempotency_key:
        raise UnprocessableError("Idempotency-Key is required", code="IDEMPOTENCY_KEY_MISSING")
    key = command_key(cmd.user_id, f"callback-req:{cmd.inbound_call_id}", cmd.idempotency_key)
    existing = _find_request(session, cmd.tenant_id, key)
    if existing is not None:
        if existing.inbound_call_id != cmd.inbound_call_id or existing.user_id != cmd.user_id:
            raise ConflictError("Idempotency-Key reused", code="IDEMPOTENCY_KEY_REUSED")
        return existing, False
    inbound = load_inbound(session, cmd.tenant_id, cmd.user_id, cmd.inbound_call_id)
    if inbound is None:
        raise NotFoundError("call not found", code="NOT_FOUND")
    view = load_user_settings(session, rt.settings, cmd.tenant_id, cmd.user_id)
    if not view.effective.callback_enabled:
        raise PolicyBlockedError(
            "Callbacks are not enabled for your account.", code="CALLBACK_NOT_ENABLED_FOR_USER"
        )
    _validate_recording(rt, view, cmd)
    preview = _evaluate(
        session, rt, cmd.tenant_id, cmd.user_id, inbound, view, None, AuthorizationMode.PREVIEW
    )
    if not preview.allowed:
        raise _refuse(session, cmd, preview, inbound)
    return _create(session, rt, cmd, inbound, view, key), True


def _build_request(
    session: Session,
    rt: CallbackRuntime,
    cmd: QueueCommand,
    inbound: InboundContext,
    auth: CallbackAuthorization,
    final: Eligibility,
    request_key: str,
) -> CallbackRequest:
    incident_id, case_id = _links(session, cmd, inbound)
    return CallbackRequest(
        tenant_id=cmd.tenant_id,
        user_id=cmd.user_id,
        inbound_call_id=cmd.inbound_call_id,
        line_id=inbound.line.id,
        authorization_id=auth.id,
        destination_key=rt.keyer.key(inbound.destination or ""),
        destination_masked=masked_destination(inbound),
        status="QUEUED",
        open_slot=str(cmd.inbound_call_id),
        outbound_recording=cmd.outbound_recording,
        incident_id=incident_id,
        case_id=case_id,
        gate_report=final.as_dict(),
        idempotency_key=request_key,
    )


def _create(
    session: Session,
    rt: CallbackRuntime,
    cmd: QueueCommand,
    inbound: InboundContext,
    view: UserSettingsView,
    request_key: str,
) -> CallbackRequest:
    auth, _ = create_authorization(
        session,
        cmd.tenant_id,
        cmd.user_id,
        cmd.inbound_call_id,
        cmd.app_version,
        cmd.idempotency_key,
    )
    final = _evaluate(
        session, rt, cmd.tenant_id, cmd.user_id, inbound, view, auth, AuthorizationMode.ENFORCE
    )
    if not final.allowed:
        raise PolicyBlockedError(
            "Callback is not allowed.",
            code=(final.report.reason_codes() or ["POLICY_BLOCKED"])[0],
            details={"eligibility": final.as_dict()},
        )
    request = _build_request(session, rt, cmd, inbound, auth, final, request_key)
    try:
        with session.begin_nested():
            session.add(request)
    except IntegrityError as exc:
        raise ConflictError(
            "A callback for this call is already queued.", code="CALLBACK_ALREADY_QUEUED"
        ) from exc
    _record_queued(session, cmd, request, auth)
    return request


def _record_queued(
    session: Session, cmd: QueueCommand, request: CallbackRequest, auth: CallbackAuthorization
) -> None:
    payload = {
        "request_id": str(request.id),
        "authorization_id": str(auth.id),
        "inbound_call_id": str(cmd.inbound_call_id),
        "user_id": str(cmd.user_id),
        "app_version": auth.app_version,
        "authorized_at": auth.authorized_at.isoformat(),
        "expires_at": auth.expires_at.isoformat(),
        "destination": request.destination_masked,
    }
    actor = actor_for_user(cmd.user_id)
    record_audit(session, cmd.tenant_id, actor, "callback.authorized", payload)
    events.emit(session, cmd.tenant_id, events.AUTHORIZED, payload, auth.id)
    events.emit(session, cmd.tenant_id, events.QUEUED, payload, request.id)
    enqueue(
        session,
        cmd.tenant_id,
        DIAL_JOB_KIND,
        {"request_id": str(request.id)},
        idempotency_key=f"{DIAL_JOB_KIND}:{request.id}",
    )


def cancel_callback(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, request_id: uuid.UUID
) -> CallbackRequest:
    request = scoped_get(session, CallbackRequest, request_id, tenant_id)
    if request is None or request.user_id != user_id:
        raise NotFoundError("callback not found", code="NOT_FOUND")
    if request.status != "QUEUED":
        raise ConflictError("Only a queued callback can be cancelled.", code="NOT_CANCELLABLE")
    now = utcnow()
    request.status = "CANCELLED"
    request.open_slot = None
    request.updated_at = now
    auth = scoped_get(session, CallbackAuthorization, request.authorization_id, tenant_id)
    if auth is not None and auth.consumed_at is None:
        auth.expires_at = now
    payload = {"request_id": str(request.id), "inbound_call_id": str(request.inbound_call_id)}
    events.emit(session, tenant_id, events.CANCELLED, payload, request.id)
    record_audit(session, tenant_id, actor_for_user(user_id), "callback.cancelled", payload)
    return request
