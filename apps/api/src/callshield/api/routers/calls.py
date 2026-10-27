"""Call history: GET /v1/calls, GET /v1/calls/{id}, POST /v1/calls/{id}/corrections."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query

from callshield.db.models.defend import CallSession
from callshield.defend import calls as call_service
from callshield.defend.deps import Reader, Writer
from callshield.defend.schemas import CallList, CorrectionRequest

router = APIRouter(tags=["calls"])


@router.get("/v1/calls", response_model=CallList)
def list_calls(
    ctx: Reader,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    direction: Annotated[str | None, Query(max_length=16)] = None,
    disposition: Annotated[str | None, Query(max_length=32)] = None,
    label: Annotated[str | None, Query(alias="class", max_length=32)] = None,
    claimed: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CallList:
    stmt = call_service.filtered_calls(
        ctx.tenant_id, date_from, date_to, direction, disposition, label, claimed
    )
    total = call_service.count_calls(ctx.session, stmt)
    page = stmt.order_by(CallSession.started_at.desc(), CallSession.id).limit(limit).offset(offset)
    calls = list(ctx.session.execute(page).scalars().all())
    items = call_service.summaries(ctx.session, ctx.tenant_id, calls) if calls else []
    return CallList(items=items, total=total, limit=limit, offset=offset)


@router.get("/v1/calls/{call_id}")
def get_call(call_id: uuid.UUID, ctx: Reader) -> dict[str, Any]:
    return call_service.call_detail(ctx.session, ctx.tenant_id, call_id)


@router.post("/v1/calls/{call_id}/corrections", status_code=201)
def correct_call(call_id: uuid.UUID, body: CorrectionRequest, ctx: Writer) -> dict[str, Any]:
    row = call_service.add_correction(
        ctx.session, ctx.tenant_id, call_id, ctx.principal.user_id, body.label, body.note
    )
    return {
        "id": str(row.id),
        "label": row.label,
        "evidence_label": row.evidence_label,
        "supersedes_id": str(row.supersedes_id) if row.supersedes_id else None,
    }
