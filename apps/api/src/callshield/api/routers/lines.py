"""Protected lines: enrollment, ownership verification, routing config, screening toggle."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from callshield.audit import actor_for_user
from callshield.core.config import Settings
from callshield.db.models.defend import ProtectedLine
from callshield.defend import lines as line_service
from callshield.defend import verification
from callshield.defend.deps import AppSettings, Reader, Writer
from callshield.defend.schemas import (
    LineCreate,
    LineCreated,
    LineOut,
    LinePatch,
    ScreeningToggleResponse,
    VerifyRequest,
    VerifyResponse,
    WebhookCredentials,
)

router = APIRouter(tags=["lines"])


@router.post("/v1/lines", response_model=LineCreated, status_code=201)
def create_line(body: LineCreate, ctx: Writer) -> LineCreated:
    line, creds = line_service.create_line(
        ctx.session,
        ctx.tenant_id,
        ctx.principal.user_id,
        body.e164,
        body.label,
        body.provider,
        body.endpoint_id,
        body.routing_config,
    )
    return LineCreated(**line_service.line_out(line).model_dump(), webhook=creds)


@router.get("/v1/lines")
def list_lines(
    ctx: Reader,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    base = select(ProtectedLine).where(ProtectedLine.tenant_id == ctx.tenant_id)
    total = ctx.session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = ctx.session.execute(
        base.order_by(ProtectedLine.created_at, ProtectedLine.id).limit(limit).offset(offset)
    ).scalars()
    items = [line_service.line_out(r) for r in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/v1/lines/{line_id}", response_model=LineOut)
def get_line(line_id: uuid.UUID, ctx: Reader) -> LineOut:
    return line_service.line_out(line_service.get_line(ctx.session, ctx.tenant_id, line_id))


@router.patch("/v1/lines/{line_id}", response_model=LineOut)
def patch_line(line_id: uuid.UUID, body: LinePatch, ctx: Writer) -> LineOut:
    line = line_service.get_line(ctx.session, ctx.tenant_id, line_id)
    actor = actor_for_user(ctx.principal.user_id)
    line_service.update_line(ctx.session, line, actor, body.label, body.routing_config)
    return line_service.line_out(line)


@router.post("/v1/lines/{line_id}/verify", response_model=VerifyResponse)
def verify_line(
    line_id: uuid.UUID, body: VerifyRequest, ctx: Writer, settings: AppSettings
) -> VerifyResponse:
    """No code: send a challenge to the line owner. With code: confirm ownership."""
    line = line_service.get_line(ctx.session, ctx.tenant_id, line_id)
    actor = actor_for_user(ctx.principal.user_id)
    if body.code is None:
        verification.start_challenge(ctx.session, settings, line, actor)
        return VerifyResponse(status=line.verification_status, detail="challenge sent")
    verification.confirm_challenge(ctx.session, line, body.code, actor)
    return VerifyResponse(status=line.verification_status, detail="line verified")


def _toggle(
    line_id: uuid.UUID, ctx: Writer, settings: Settings, enabled: bool
) -> ScreeningToggleResponse:
    line = line_service.get_line(ctx.session, ctx.tenant_id, line_id)
    actor = actor_for_user(ctx.principal.user_id)
    info = line_service.set_screening(ctx.session, settings, line, enabled, actor)
    return ScreeningToggleResponse(
        line_id=line.id, screening_enabled=line.screening_enabled, **info
    )


@router.post("/v1/lines/{line_id}/screening:disable", response_model=ScreeningToggleResponse)
def disable_screening(
    line_id: uuid.UUID, ctx: Writer, settings: AppSettings
) -> ScreeningToggleResponse:
    return _toggle(line_id, ctx, settings, False)


@router.post("/v1/lines/{line_id}/screening:enable", response_model=ScreeningToggleResponse)
def enable_screening(
    line_id: uuid.UUID, ctx: Writer, settings: AppSettings
) -> ScreeningToggleResponse:
    return _toggle(line_id, ctx, settings, True)


@router.post("/v1/lines/{line_id}/webhook-secret:rotate", response_model=WebhookCredentials)
def rotate_webhook_secret(line_id: uuid.UUID, ctx: Writer) -> WebhookCredentials:
    line = line_service.get_line(ctx.session, ctx.tenant_id, line_id)
    return line_service.rotate_secret(ctx.session, line, actor_for_user(ctx.principal.user_id))
