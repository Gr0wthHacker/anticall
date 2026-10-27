"""Trusted / permitted / known-unwanted contacts per line (D-002)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from callshield.audit import actor_for_user, record_audit
from callshield.auth.scoping import scoped_get_or_404
from callshield.core.errors import ConflictError
from callshield.db.models.defend import ProtectedLine, TrustedContact
from callshield.defend.deps import Reader, Writer
from callshield.defend.schemas import ContactCreate, ContactOut, ContactPatch

router = APIRouter(tags=["contacts"])


def _out(c: TrustedContact) -> ContactOut:
    return ContactOut(
        id=c.id, line_id=c.line_id, e164=c.e164, kind=c.kind, name=c.name, created_at=c.created_at
    )


@router.post("/v1/contacts", response_model=ContactOut, status_code=201)
def create_contact(body: ContactCreate, ctx: Writer) -> ContactOut:
    line = scoped_get_or_404(ctx.session, ProtectedLine, body.line_id, ctx.tenant_id)
    duplicate = ctx.session.execute(
        select(TrustedContact.id).where(
            TrustedContact.tenant_id == ctx.tenant_id,
            TrustedContact.line_id == line.id,
            TrustedContact.e164 == body.e164,
        )
    ).first()
    if duplicate is not None:
        raise ConflictError("contact already exists for this line", code="CONTACT_EXISTS")
    contact = TrustedContact(
        tenant_id=ctx.tenant_id,
        line_id=line.id,
        e164=body.e164,
        kind=body.kind.value,
        name=body.name,
    )
    ctx.session.add(contact)
    ctx.session.flush()
    record_audit(
        ctx.session,
        ctx.tenant_id,
        actor_for_user(ctx.principal.user_id),
        "contact.created",
        {"contact_id": str(contact.id), "line_id": str(line.id), "kind": contact.kind},
    )
    return _out(contact)


@router.get("/v1/contacts", response_model=list[ContactOut])
def list_contacts(
    ctx: Reader,
    line_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[ContactOut]:
    stmt = select(TrustedContact).where(TrustedContact.tenant_id == ctx.tenant_id)
    if line_id is not None:
        stmt = stmt.where(TrustedContact.line_id == line_id)
    rows = ctx.session.execute(stmt.order_by(TrustedContact.created_at).limit(limit)).scalars()
    return [_out(c) for c in rows]


@router.get("/v1/contacts/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: uuid.UUID, ctx: Reader) -> ContactOut:
    return _out(scoped_get_or_404(ctx.session, TrustedContact, contact_id, ctx.tenant_id))


@router.patch("/v1/contacts/{contact_id}", response_model=ContactOut)
def patch_contact(contact_id: uuid.UUID, body: ContactPatch, ctx: Writer) -> ContactOut:
    contact = scoped_get_or_404(ctx.session, TrustedContact, contact_id, ctx.tenant_id)
    if body.kind is not None:
        contact.kind = body.kind.value
    if body.name is not None:
        contact.name = body.name
    record_audit(
        ctx.session,
        ctx.tenant_id,
        actor_for_user(ctx.principal.user_id),
        "contact.updated",
        {"contact_id": str(contact.id), "kind": contact.kind},
    )
    return _out(contact)


@router.delete("/v1/contacts/{contact_id}", status_code=204)
def delete_contact(contact_id: uuid.UUID, ctx: Writer) -> None:
    contact = scoped_get_or_404(ctx.session, TrustedContact, contact_id, ctx.tenant_id)
    record_audit(
        ctx.session,
        ctx.tenant_id,
        actor_for_user(ctx.principal.user_id),
        "contact.deleted",
        {"contact_id": str(contact.id), "line_id": str(contact.line_id), "kind": contact.kind},
    )
    ctx.session.delete(contact)
