"""GET /v1/audit-events: tenant-scoped, paginated, newest first."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from callshield.db.models.platform import AuditEvent
from callshield.defend.deps import Reader
from callshield.defend.schemas import AuditEventList, AuditEventOut

router = APIRouter(tags=["audit"])


@router.get("/v1/audit-events", response_model=AuditEventList)
def list_audit_events(
    ctx: Reader,
    action: Annotated[str | None, Query(max_length=100)] = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventList:
    stmt = select(AuditEvent).where(AuditEvent.tenant_id == ctx.tenant_id)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if since is not None:
        stmt = stmt.where(AuditEvent.time >= since)
    total = ctx.session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = ctx.session.execute(
        stmt.order_by(AuditEvent.time.desc(), AuditEvent.id).limit(limit).offset(offset)
    ).scalars()
    items = [
        AuditEventOut(
            id=r.id,
            actor=r.actor,
            action=r.action,
            data=r.data,
            time=r.time,
            immutable_ref=r.immutable_ref,
        )
        for r in rows
    ]
    return AuditEventList(items=items, total=total, limit=limit, offset=offset)
