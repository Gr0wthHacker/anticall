"""Callback flow API (docs/prd/CHANGE_callback_flow.md). 404 everywhere unless callback_enabled.

The destination is NEVER supplied by the client: every route takes an `inbound_call_id`
(path) and the number is derived server-side from that logged call's displayed caller ID.
Request bodies forbid extra fields, so a `number`/`to`/`caller_id` field is a 422.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from callshield.audit import actor_for_user, record_audit
from callshield.auth.deps import TenantContext, tenant_context
from callshield.auth.scoping import scoped_select
from callshield.callback import lines as line_service
from callshield.callback import service
from callshield.callback.config import (
    CEIL_PER_INBOUND,
    CEIL_PER_NUMBER_30D,
    CEIL_PER_USER_DAY,
    CHANGEABLE_BY,
    INSTANCE_MAX_PER_NUMBER_30D,
    MIN_MINUTES_BETWEEN_ATTEMPTS,
    SettingsRejectedError,
    validate_patch,
)
from callshield.callback.context import load_user_settings
from callshield.callback.runtime import CallbackRuntime, get_runtime
from callshield.callback.timeutil import resolve_timezone
from callshield.core.clock import utcnow
from callshield.core.errors import ApiError, ForbiddenError, NotFoundError, UnprocessableError
from callshield.db.models.callback_ext import (
    CallbackBlocklist,
    CallbackRequest,
    CallbackResult,
    CallbackSettingsRow,
)
from callshield.defend.deps import Writer
from callshield.domain.enums import Role

router = APIRouter(tags=["callback"])

IdemHeader = Annotated[str | None, Header(alias="Idempotency-Key")]
Ctx = Annotated[TenantContext, Depends(tenant_context)]
_SETTINGS_ROLES = frozenset({Role.CONSUMER_SUBSCRIBER, Role.HOUSEHOLD_ADMIN, Role.OPERATOR})


def require_callback_enabled() -> CallbackRuntime:
    runtime = get_runtime()
    if not runtime.settings.callback_enabled:
        raise NotFoundError("Not found", code="NOT_FOUND")
    return runtime


Rt = Annotated[CallbackRuntime, Depends(require_callback_enabled)]


class CallbackQueueIn(BaseModel):
    """No destination, caller ID or phone-number field exists (extra fields are rejected)."""

    model_config = ConfigDict(extra="forbid")

    app_version: Annotated[str, Field(min_length=1, max_length=40)] = "unknown"
    outbound_recording: bool = False
    recording_notice_confirmed: bool = False
    incident_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None


class OwnershipCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[str | None, Field(min_length=4, max_length=12)] = None


def _reject_query(request: Request) -> None:
    if request.query_params:
        raise UnprocessableError(
            "Destinations cannot be supplied; use the inbound call id.",
            code="FREE_TEXT_NUMBER_REJECTED",
        )


def _serialize(row: CallbackRequest, result: CallbackResult | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(row.id),
        "inbound_call_id": str(row.inbound_call_id),
        "line_id": str(row.line_id),
        "status": row.status,
        "destination": row.destination_masked,
        "outbound_recording": row.outbound_recording,
        "incident_id": str(row.incident_id) if row.incident_id else None,
        "case_id": str(row.case_id) if row.case_id else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
    if result is not None:
        body["result"] = {
            "outcome": result.outcome,
            "fields": result.fields,  # each value labeled CLAIMED / OBSERVED
            "metadata": result.call_metadata,
            "tombstoned": result.tombstoned_at is not None,
            "retention_expires_at": result.expires_at.isoformat(),
        }
    return body


def _results_for(
    session: Session, ctx: TenantContext, rows: list[CallbackRequest]
) -> dict[Any, Any]:
    if not rows:
        return {}
    stmt = scoped_select(CallbackResult, ctx.tenant_id).where(
        CallbackResult.request_id.in_([r.id for r in rows])
    )
    return {r.request_id: r for r in session.execute(stmt).scalars()}


def _own_request(ctx: TenantContext, callback_id: uuid.UUID) -> CallbackRequest:
    row = ctx.session.execute(
        scoped_select(CallbackRequest, ctx.tenant_id).where(CallbackRequest.id == callback_id)
    ).scalar_one_or_none()
    if row is None or row.user_id != ctx.principal.user_id:
        raise NotFoundError("callback not found", code="NOT_FOUND")
    return row


# ------------------------------------------------------------------ capabilities


@router.get("/v1/callback/capabilities")
def callback_capabilities(ctx: Ctx) -> dict[str, Any]:
    settings = get_runtime().settings
    return {
        "callback_enabled": settings.callback_enabled,
        "ceilings": {
            "max_attempts_per_inbound": CEIL_PER_INBOUND,
            "max_attempts_per_number_30d": CEIL_PER_NUMBER_30D,
            "max_attempts_per_user_day": CEIL_PER_USER_DAY,
            "instance_max_per_number_30d": INSTANCE_MAX_PER_NUMBER_30D,
            "min_minutes_between_attempts": MIN_MINUTES_BETWEEN_ATTEMPTS,
        },
        "remote_ai_processing": False,
    }


# ------------------------------------------------------------------ eligibility + queue


@router.get("/v1/calls/{call_id}/callback-eligibility")
def callback_eligibility(call_id: uuid.UUID, request: Request, ctx: Ctx, rt: Rt) -> dict[str, Any]:
    _reject_query(request)
    elig = service.eligibility(ctx.session, rt, ctx.tenant_id, ctx.principal.user_id, call_id)
    return elig.as_dict()


@router.post("/v1/calls/{call_id}/callbacks", status_code=202)
def queue_callback(
    call_id: uuid.UUID,
    body: CallbackQueueIn,
    ctx: Writer,
    rt: Rt,
    idempotency_key: IdemHeader = None,
) -> dict[str, Any]:
    cmd = service.QueueCommand(
        tenant_id=ctx.tenant_id,
        user_id=ctx.principal.user_id,
        inbound_call_id=call_id,
        idempotency_key=idempotency_key or "",
        app_version=body.app_version,
        outbound_recording=body.outbound_recording,
        recording_notice_confirmed=body.recording_notice_confirmed,
        incident_id=body.incident_id,
        case_id=body.case_id,
    )
    row, created = service.queue_callback(ctx.session, rt, cmd)
    return {**_serialize(row, None), "replayed": not created}


@router.post("/v1/callbacks/{callback_id}/cancel")
def cancel_callback(callback_id: uuid.UUID, ctx: Writer, rt: Rt) -> dict[str, Any]:
    row = service.cancel_callback(ctx.session, ctx.tenant_id, ctx.principal.user_id, callback_id)
    return _serialize(row, None)


@router.get("/v1/callbacks")
def list_callbacks(ctx: Ctx, rt: Rt) -> dict[str, Any]:
    rows = list(
        ctx.session.execute(
            scoped_select(CallbackRequest, ctx.tenant_id)
            .where(CallbackRequest.user_id == ctx.principal.user_id)
            .order_by(CallbackRequest.created_at.desc(), CallbackRequest.id)
            .limit(100)
        ).scalars()
    )
    results = _results_for(ctx.session, ctx, rows)
    return {"items": [_serialize(r, results.get(r.id)) for r in rows]}


@router.get("/v1/callbacks/{callback_id}")
def get_callback(callback_id: uuid.UUID, ctx: Ctx, rt: Rt) -> dict[str, Any]:
    row = _own_request(ctx, callback_id)
    result = _results_for(ctx.session, ctx, [row]).get(row.id)
    body = _serialize(row, result)
    body["gate_report"] = row.gate_report
    return body


# ------------------------------------------------------------------ settings + blocklist


def _settings_body(ctx: Ctx, rt: CallbackRuntime) -> dict[str, Any]:
    view = load_user_settings(ctx.session, rt.settings, ctx.tenant_id, ctx.principal.user_id)
    return {
        "settings": view.effective.model_dump(),
        "timezone": view.timezone,
        "changeable_by": {k: v.value for k, v in CHANGEABLE_BY.items()},
    }


@router.get("/v1/callback/settings")
def get_callback_settings(ctx: Ctx, rt: Rt) -> dict[str, Any]:
    return _settings_body(ctx, rt)


def _settings_row(
    ctx: TenantContext, scope_key: str, user_id: uuid.UUID | None
) -> CallbackSettingsRow:
    row = ctx.session.execute(
        scoped_select(CallbackSettingsRow, ctx.tenant_id).where(
            CallbackSettingsRow.scope_key == scope_key
        )
    ).scalar_one_or_none()
    if row is None:
        row = CallbackSettingsRow(
            tenant_id=ctx.tenant_id, scope_key=scope_key, user_id=user_id, overrides={}
        )
        ctx.session.add(row)
    return row


@router.patch("/v1/callback/settings")
def patch_callback_settings(patch: dict[str, Any], ctx: Ctx, rt: Rt) -> dict[str, Any]:
    role = ctx.principal.role
    if role not in _SETTINGS_ROLES:
        raise ForbiddenError("insufficient role", code="INSUFFICIENT_ROLE")
    is_operator = role is Role.OPERATOR
    timezone = patch.pop("timezone", None) if "timezone" in patch else None
    if timezone is not None and (is_operator or resolve_timezone(str(timezone)) is None):
        code = "USER_ONLY" if is_operator else "INVALID_TIMEZONE"
        raise ApiError("timezone cannot be set", code=code, status_code=403 if is_operator else 422)
    current = load_user_settings(ctx.session, rt.settings, ctx.tenant_id, ctx.principal.user_id)
    try:
        accepted = validate_patch(patch, is_operator=is_operator, current=current.effective)
    except SettingsRejectedError as exc:
        status = 403 if exc.code in {"OPERATOR_ONLY", "USER_ONLY"} else 422
        raise ApiError(
            str(exc), code=exc.code, status_code=status, details={"key": exc.key}
        ) from exc
    _store_settings(ctx, is_operator, accepted, str(timezone) if timezone else None)
    record_audit(
        ctx.session,
        ctx.tenant_id,
        actor_for_user(ctx.principal.user_id),
        "callback.settings.updated",
        {"keys": sorted(accepted) + (["timezone"] if timezone else [])},
    )
    ctx.session.flush()
    return _settings_body(ctx, rt)


def _store_settings(
    ctx: TenantContext, is_operator: bool, accepted: dict[str, Any], timezone: str | None
) -> None:
    uid = ctx.principal.user_id
    row = _settings_row(
        ctx, "TENANT" if is_operator else f"USER:{uid}", None if is_operator else uid
    )
    row.overrides = {**row.overrides, **accepted}
    row.updated_at = utcnow()
    if timezone:
        row.timezone = timezone


@router.get("/v1/callback/blocklist")
def get_callback_blocklist(ctx: Ctx, rt: Rt) -> dict[str, Any]:
    rows = ctx.session.execute(
        scoped_select(CallbackBlocklist, ctx.tenant_id)
        .where(CallbackBlocklist.user_id == ctx.principal.user_id)
        .order_by(CallbackBlocklist.created_at.desc())
    ).scalars()
    return {
        "items": [
            {
                "destination": r.destination_masked,
                "reason": r.reason,
                "since": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


# ------------------------------------------------------------------ caller ID ownership


@router.post("/v1/lines/{line_id}/outbound-ownership-check")
def outbound_ownership_check(
    line_id: uuid.UUID, body: OwnershipCheckIn, ctx: Writer, rt: Rt
) -> dict[str, Any]:
    """No code: send one to the line itself. With code: confirm (required before callbacks)."""
    line = line_service.get_line_or_404(ctx.session, ctx.tenant_id, line_id)
    if line.owner_user_id != ctx.principal.user_id:
        raise NotFoundError("line not found", code="NOT_FOUND")
    actor = actor_for_user(ctx.principal.user_id)
    if body.code is None:
        line_service.start_ownership_check(ctx.session, line, rt.channel(), actor)
        return {"status": "CODE_SENT"}
    line_service.confirm_ownership_check(ctx.session, line, body.code, actor)
    return {"status": "OWNERSHIP_CONFIRMED"}
