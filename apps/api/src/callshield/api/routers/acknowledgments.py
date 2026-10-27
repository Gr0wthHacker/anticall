"""Acknowledgment endpoints (PRD Appendix B): serve the versioned text, record acceptance.

Nothing here unlocks a feature by itself; it records that this user accepted this exact text.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from callshield.acknowledgment import service
from callshield.acknowledgment.content import ACKNOWLEDGMENT_VERSION
from callshield.auth.deps import TenantContext, require_role, tenant_context
from callshield.auth.tokens import Principal
from callshield.db.models.platform_ext import Acknowledgment
from callshield.domain.enums import Role

router = APIRouter(tags=["acknowledgments"])

Reader = Annotated[TenantContext, Depends(tenant_context)]
_ACCEPTING_ROLES = (Role.CONSUMER_SUBSCRIBER, Role.HOUSEHOLD_ADMIN, Role.OPERATOR)


def accepting_context(
    ctx: Reader, _role: Annotated[Principal, Depends(require_role(*_ACCEPTING_ROLES))]
) -> TenantContext:
    return ctx


Accepter = Annotated[TenantContext, Depends(accepting_context)]


class AcknowledgmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text_sha256: str = Field(min_length=64, max_length=64)  # digest of the text the user saw
    items: dict[str, bool]  # item id -> checked; ALL must be true


class AcknowledgmentOut(BaseModel):
    acknowledgment_id: str
    user_id: str
    acknowledgment_version: str
    text_sha256: str
    operator_id: str
    item_ids: list[str]
    accepted_at: datetime
    created: bool = True


class MyAcknowledgmentOut(BaseModel):
    acknowledged: bool
    current_version: str
    current_text_sha256: str
    history: list[AcknowledgmentOut]


def _out(row: Acknowledgment, created: bool = False) -> AcknowledgmentOut:
    return AcknowledgmentOut(
        acknowledgment_id=str(row.id),
        user_id=str(row.user_id),
        acknowledgment_version=row.acknowledgment_version,
        text_sha256=row.text_sha256,
        operator_id=row.operator_id,
        item_ids=[str(i) for i in row.item_ids],
        accepted_at=row.accepted_at,
        created=created,
    )


@router.get("/v1/acknowledgments/current")
def current_acknowledgment(ctx: Reader) -> dict[str, Any]:
    """The exact versioned text to display. Items are unchecked by default."""
    return service.current_document()


@router.post("/v1/acknowledgments", response_model=AcknowledgmentOut, status_code=201)
def accept_acknowledgment(
    body: AcknowledgmentIn, ctx: Accepter, response: Response
) -> AcknowledgmentOut:
    row, created = service.accept(
        ctx.session, ctx.tenant_id, ctx.principal.user_id, body.items, body.text_sha256
    )
    if not created:
        response.status_code = 200
    return _out(row, created)


@router.get("/v1/acknowledgments/me", response_model=MyAcknowledgmentOut)
def my_acknowledgments(ctx: Reader) -> MyAcknowledgmentOut:
    user_id = ctx.principal.user_id
    return MyAcknowledgmentOut(
        acknowledged=service.has_acknowledged(ctx.session, ctx.tenant_id, user_id),
        current_version=ACKNOWLEDGMENT_VERSION,
        current_text_sha256=service.current_text_sha256(),
        history=[_out(r) for r in service.history(ctx.session, ctx.tenant_id, user_id)],
    )
