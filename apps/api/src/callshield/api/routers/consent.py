"""Processing consent per line, plus a side-effect-free decision preview."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from callshield.db.models.defend import ConsentRecord
from callshield.defend import consent as consent_service
from callshield.defend import lines as line_service
from callshield.defend.deps import AppSettings, Reader, Writer
from callshield.defend.schemas import ConsentCreate, ConsentOut, ConsentView, DecisionOut

router = APIRouter(tags=["consent"])


def _record_out(r: ConsentRecord) -> ConsentOut:
    return ConsentOut(
        id=r.id,
        processing_mode=r.processing_mode,
        authorized=r.authorized,
        authorized_at=r.authorized_at,
        revoked_at=r.revoked_at,
        legal_basis=r.legal_basis_config,
        created_at=r.created_at,
    )


@router.get("/v1/lines/{line_id}/consent", response_model=ConsentView)
def get_consent(line_id: uuid.UUID, ctx: Reader, settings: AppSettings) -> ConsentView:
    line = line_service.get_line(ctx.session, ctx.tenant_id, line_id)
    records = consent_service.line_consents(ctx.session, line)
    decision = consent_service.current_decision(ctx.session, settings, line)
    return ConsentView(
        records=[_record_out(r) for r in records], decision=DecisionOut(**decision.as_dict())
    )


@router.post("/v1/lines/{line_id}/consent", response_model=ConsentView, status_code=201)
def post_consent(
    line_id: uuid.UUID, body: ConsentCreate, ctx: Writer, settings: AppSettings
) -> ConsentView:
    """Append a grant or revocation. Takes effect on the next call/turn."""
    line = line_service.get_line(ctx.session, ctx.tenant_id, line_id)
    consent_service.record_grant(
        ctx.session,
        line,
        ctx.principal.user_id,
        body.processing_mode,
        body.authorized,
        {
            "all_party_basis_confirmed": body.all_party_basis_confirmed,
            "remote_stt_ok": body.remote_stt_ok,
            "notes": body.notes,
        },
    )
    ctx.session.flush()
    records = consent_service.line_consents(ctx.session, line)
    decision = consent_service.current_decision(ctx.session, settings, line)
    return ConsentView(
        records=[_record_out(r) for r in records], decision=DecisionOut(**decision.as_dict())
    )


@router.get("/v1/lines/{line_id}/consent/decision", response_model=DecisionOut)
def preview_decision(line_id: uuid.UUID, ctx: Reader, settings: AppSettings) -> DecisionOut:
    """What would be allowed for the next call. Writes nothing."""
    line = line_service.get_line(ctx.session, ctx.tenant_id, line_id)
    return DecisionOut(**consent_service.current_decision(ctx.session, settings, line).as_dict())
