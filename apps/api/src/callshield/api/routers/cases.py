"""Enforce case endpoints (PRD 19.7). Filing/assessment routes live in other modules."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from callshield.auth.deps import TenantContext, get_app_settings, tenant_context
from callshield.auth.scoping import scoped_get_or_404, scoped_select
from callshield.casebuilder.attribution import repeat_contact_verified, review_attribution
from callshield.casebuilder.config import config_for_tenant
from callshield.casebuilder.export import (
    create_download_token,
    decode_download_token,
    export_bytes,
    get_export,
    request_export,
)
from callshield.casebuilder.imports import UserAccount, import_user_accounts
from callshield.casebuilder.lifecycle import USER_TRANSITIONS, transition_case
from callshield.casebuilder.linking import (
    current_links,
    effective_label,
    reclassify_incident,
    set_link,
)
from callshield.casebuilder.optout import recompute_optout_timeline
from callshield.casebuilder.pipeline import resolve_review_items
from callshield.casebuilder.recompute import recompute_case
from callshield.casebuilder.service import (
    authorize_line_owner,
    list_cases,
    open_case,
    user_idempotency_key,
)
from callshield.casebuilder.snapshot import refresh_dossier_snapshot
from callshield.casebuilder.sufficiency import evidence_sufficiency
from callshield.casebuilder.timeline import build_timeline
from callshield.core.config import Settings
from callshield.core.errors import ForbiddenError
from callshield.db.models.defend import Incident
from callshield.db.models.enforce import Case
from callshield.db.models.enforce_ext import CaseExport, CaseReviewItem
from callshield.db.session import bind_tenant
from callshield.domain.enums import AttributionStatus, EnforceCaseState, EvidenceLabel, Role
from callshield.evidence.access import describe_artifact
from callshield.evidence.store import ArtifactStore, get_artifact_store
from callshield.worker.queue import enqueue

router = APIRouter(tags=["enforce-cases"])

Ctx = Annotated[TenantContext, Depends(tenant_context)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=120)]
REVIEW_ROLES = frozenset({Role.REVIEWER, Role.OPERATOR, Role.HOUSEHOLD_ADMIN})
ELEVATED_ROLES = frozenset({Role.REVIEWER, Role.OPERATOR})


def store_dependency() -> ArtifactStore:
    return get_artifact_store()


Store = Annotated[ArtifactStore, Depends(store_dependency)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    protected_line_id: uuid.UUID
    title: str
    state: str
    attribution_status: str
    recording_authorization: str
    legal_assessment: str
    case_resolution: str
    review_version: int
    opened_at: datetime
    updated_at: datetime


class CasePageOut(BaseModel):
    items: list[CaseOut]
    total: int
    limit: int
    offset: int


class CreateCaseRequest(BaseModel):
    protected_line_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)


class LinkRequest(BaseModel):
    incident_id: uuid.UUID
    reason: str = Field(default="", max_length=1000)
    evidence_basis: dict[str, Any] = Field(default_factory=dict)
    user_confirms_same_operator: bool = False


class UnlinkRequest(BaseModel):
    incident_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=1000)


class ReclassifyRequest(BaseModel):
    label: str
    reason: str = Field(default="", max_length=1000)


class TransitionRequest(BaseModel):
    to_state: EnforceCaseState
    expected_review_version: int | None = None


class AttributionReviewRequest(BaseModel):
    status: AttributionStatus
    basis: str = Field(min_length=1, max_length=500)


class ExportRequest(BaseModel):
    artifact_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    mask_numbers: bool = False


class UserAccountIn(BaseModel):
    narrative: str = Field(min_length=1, max_length=5000)
    stated_event_date: date | None = None
    approximate_count: int | None = Field(default=None, ge=1, le=100000)
    category: str = Field(default="OTHER", max_length=48)


class ImportRequest(BaseModel):
    accounts: list[UserAccountIn] = Field(min_length=1, max_length=100)


def _case(ctx: TenantContext, case_id: uuid.UUID) -> Case:
    return scoped_get_or_404(ctx.session, Case, case_id, ctx.tenant_id)


def _refresh(
    session: Session, store: ArtifactStore, ctx: TenantContext, case_id: uuid.UUID
) -> None:
    recompute_case(session, store, ctx.tenant_id, case_id, config_for_tenant(ctx.tenant_id))


@router.post("/v1/cases", response_model=CaseOut, status_code=201)
def create_case(
    body: CreateCaseRequest, ctx: Ctx, response: Response, idempotency_key: IdempotencyKey = None
) -> Case:
    """Open a case for a protected line the caller may act for. Replays return the original."""
    authorize_line_owner(
        ctx.session,
        ctx.tenant_id,
        body.protected_line_id,
        ctx.principal.user_id,
        ctx.principal.role,
    )
    key = user_idempotency_key(ctx.principal.user_id, idempotency_key)
    case, created = open_case(
        ctx.session, ctx.tenant_id, body.protected_line_id, body.title,
        actor=ctx.principal.actor, idempotency_key=key,
    )  # fmt: skip
    if created:
        refresh_dossier_snapshot(ctx.session, ctx.tenant_id, case.id)
    else:
        response.status_code = 200
    return case


@router.get("/v1/cases", response_model=CasePageOut)
def get_cases(
    ctx: Ctx,
    state: Annotated[list[EnforceCaseState] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CasePageOut:
    page = list_cases(
        ctx.session, ctx.tenant_id, states=[s.value for s in state] if state else None,
        limit=limit, offset=offset,
    )  # fmt: skip
    items = [CaseOut.model_validate(c) for c in page.items]
    return CasePageOut(items=items, total=page.total, limit=page.limit, offset=page.offset)


def _link_views(ctx: TenantContext, case_id: uuid.UUID) -> list[dict[str, Any]]:
    views = []
    for incident_id, row in current_links(ctx.session, ctx.tenant_id, case_id).items():
        incident = scoped_get_or_404(ctx.session, Incident, incident_id, ctx.tenant_id)
        views.append(
            {
                "incident_id": str(incident_id),
                "linked": row.linkage_type in ("AUTO_MATCH", "USER_LINK", "REVIEWER_LINK"),
                "linkage_type": row.linkage_type,
                "evidence_basis": row.evidence_basis,
                "actor": row.actor,
                "updated_at": row.created_at.isoformat(),
                "effective_label": effective_label(ctx.session, ctx.tenant_id, incident),
                "evidence_label": incident.evidence_label,
            }
        )
    return views


@router.get("/v1/cases/{case_id}")
def get_case(case_id: uuid.UUID, ctx: Ctx) -> dict[str, Any]:
    """Case with incident links and the evidence sufficiency checklist (no legal-validity score)."""
    case = _case(ctx, case_id)
    timeline = recompute_optout_timeline(ctx.session, ctx.tenant_id, case, update_case=False)
    return {
        "case": CaseOut.model_validate(case).model_dump(mode="json"),
        "incident_links": _link_views(ctx, case_id),
        "evidence_sufficiency": evidence_sufficiency(ctx.session, ctx.tenant_id, case_id),
        "repeat_contact_verified": repeat_contact_verified(case.attribution_status),
        "possible_subsequent_incident_ids": [
            str(i) for i in timeline.possible_subsequent_incident_ids
        ],
        "available_transitions": sorted(
            t.value for f, t in USER_TRANSITIONS if f.value == case.state
        ),
        "match_ruleset_version": config_for_tenant(ctx.tenant_id).match_ruleset_version,
    }


@router.post("/v1/cases/{case_id}/incidents:link")
def link_incident(case_id: uuid.UUID, body: LinkRequest, ctx: Ctx, store: Store) -> dict[str, Any]:
    basis = {
        **body.evidence_basis,
        "reason": body.reason,
        "user_confirms_same_operator": body.user_confirms_same_operator,
    }
    row, created = set_link(
        ctx.session, ctx.tenant_id, case_id, body.incident_id, link=True,
        reviewer=ctx.principal.role in ELEVATED_ROLES,
        evidence_basis=basis,
        actor=ctx.principal.actor,
    )  # fmt: skip
    resolve_review_items(ctx.session, ctx.tenant_id, body.incident_id, "LINKED_BY_HUMAN")
    _refresh(ctx.session, store, ctx, case_id)
    return {"linked": True, "changed": created, "link_id": str(row.id) if row else None}


@router.post("/v1/cases/{case_id}/incidents:unlink")
def unlink_incident(
    case_id: uuid.UUID, body: UnlinkRequest, ctx: Ctx, store: Store
) -> dict[str, Any]:
    """Append an unlink row. The incident and its call data are untouched (ENF-007)."""
    row, created = set_link(
        ctx.session, ctx.tenant_id, case_id, body.incident_id, link=False,
        reviewer=ctx.principal.role in ELEVATED_ROLES, evidence_basis={"reason": body.reason},
        actor=ctx.principal.actor,
    )  # fmt: skip
    resolve_review_items(ctx.session, ctx.tenant_id, body.incident_id, "UNLINKED_BY_HUMAN")
    _refresh(ctx.session, store, ctx, case_id)
    return {"linked": False, "changed": created, "link_id": str(row.id) if row else None}


@router.post("/v1/cases/{case_id}/incidents/{incident_id}/reclassify", status_code=201)
def reclassify(
    case_id: uuid.UUID, incident_id: uuid.UUID, body: ReclassifyRequest, ctx: Ctx
) -> dict[str, Any]:
    _case(ctx, case_id)
    row = reclassify_incident(
        ctx.session, ctx.tenant_id, incident_id, body.label, body.reason,
        ctx.principal.actor, case_id=case_id,
    )  # fmt: skip
    enqueue(
        ctx.session, ctx.tenant_id, "incident.created", {"incident_id": str(incident_id)},
        idempotency_key=f"incident-reclass:{row.id}",
    )  # fmt: skip
    return {
        "reclassification_id": str(row.id), "from": row.previous_label, "to": row.new_label,
        "source_incident_modified": False,
    }  # fmt: skip


@router.get("/v1/cases/{case_id}/timeline")
def get_timeline(case_id: uuid.UUID, ctx: Ctx) -> dict[str, Any]:
    entries = build_timeline(ctx.session, ctx.tenant_id, case_id)
    return {
        "case_id": str(case_id),
        "count": len(entries),
        "source_labels": [label.value for label in EvidenceLabel],
        "entries": [e.as_dict() for e in entries],
    }


@router.post("/v1/cases/{case_id}/transition", response_model=CaseOut)
def transition(case_id: uuid.UUID, body: TransitionRequest, ctx: Ctx) -> Case:
    """Early lifecycle edges only; later edges are driven by filing services."""
    case = transition_case(
        ctx.session, ctx.tenant_id, case_id, body.to_state, ctx.principal.actor,
        expected_review_version=body.expected_review_version, user_facing=True,
    )  # fmt: skip
    refresh_dossier_snapshot(ctx.session, ctx.tenant_id, case_id)
    return case


@router.post("/v1/cases/{case_id}/attribution-review", response_model=CaseOut)
def attribution_review(
    case_id: uuid.UUID, body: AttributionReviewRequest, ctx: Ctx, store: Store
) -> Case:
    """Human review of attribution. CORROBORATED needs several independent features."""
    if ctx.principal.role not in REVIEW_ROLES:
        raise ForbiddenError(
            "attribution review requires a reviewing role", code="INSUFFICIENT_ROLE"
        )
    case = review_attribution(
        ctx.session, store, ctx.tenant_id, case_id, body.status, body.basis,
        ctx.principal.actor, config_for_tenant(ctx.tenant_id),
    )  # fmt: skip
    refresh_dossier_snapshot(ctx.session, ctx.tenant_id, case_id)
    return case


@router.post("/v1/cases/{case_id}/imports/user-attested", status_code=201)
def import_user_attested(
    case_id: uuid.UUID, body: ImportRequest, ctx: Ctx, store: Store, settings: AppSettings
) -> dict[str, Any]:
    accounts = [UserAccount(**a.model_dump()) for a in body.accounts]
    created = import_user_accounts(
        ctx.session, store, settings, ctx.tenant_id, case_id, accounts, ctx.principal.actor
    )
    refresh_dossier_snapshot(ctx.session, ctx.tenant_id, case_id)
    return {"imported": [describe_artifact(a) for a in created]}


def _export_view(export: CaseExport) -> dict[str, Any]:
    return {
        "id": str(export.id),
        "case_id": str(export.case_id),
        "snapshot_id": str(export.snapshot_id),
        "version": export.version,
        "status": export.status,
        "export_digest": export.export_digest,
        "zip_sha256": export.zip_sha256,
        "byte_length": export.byte_length,
        "summary": export.manifest_summary,
        "created_at": export.created_at.isoformat(),
        "completed_at": export.completed_at.isoformat() if export.completed_at else None,
        "expires_at": export.expires_at.isoformat() if export.expires_at else None,
        "downloadable": export.status == "COMPLETED" and export.deleted_at is None,
    }


@router.post("/v1/cases/{case_id}/exports", status_code=202)
def create_export(
    case_id: uuid.UUID, body: ExportRequest, ctx: Ctx, response: Response,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:  # fmt: skip
    export, created = request_export(
        ctx.session, ctx.tenant_id, case_id, ctx.principal.actor,
        artifact_ids=body.artifact_ids, mask_numbers=body.mask_numbers, client_key=idempotency_key,
    )  # fmt: skip
    if not created:
        response.status_code = 200
    return _export_view(export)


@router.get("/v1/cases/{case_id}/exports/{export_id}")
def export_status(case_id: uuid.UUID, export_id: uuid.UUID, ctx: Ctx) -> dict[str, Any]:
    return _export_view(get_export(ctx.session, ctx.tenant_id, case_id, export_id))


def _zip_response(export: CaseExport, data: bytes) -> Response:
    name = f"case_{export.case_id}_{export.snapshot_id}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-SHA256": export.zip_sha256 or "",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/v1/cases/{case_id}/exports/{export_id}/download")
def download_export(case_id: uuid.UUID, export_id: uuid.UUID, ctx: Ctx, store: Store) -> Response:
    export = get_export(ctx.session, ctx.tenant_id, case_id, export_id)
    return _zip_response(export, export_bytes(store, export))


@router.post("/v1/cases/{case_id}/exports/{export_id}/download-token")
def export_download_token(
    case_id: uuid.UUID, export_id: uuid.UUID, ctx: Ctx, settings: AppSettings
) -> dict[str, Any]:
    """Short-lived token scoped to this tenant and this one export."""
    export = get_export(ctx.session, ctx.tenant_id, case_id, export_id)
    token, expires = create_download_token(settings, ctx.tenant_id, export.id)
    return {"token": token, "expires_at": expires.isoformat(), "path": "/v1/exports/download"}


@router.get("/v1/exports/download")
def download_with_token(
    request: Request,
    token: Annotated[str, Query(min_length=10)],
    settings: AppSettings,
    store: Store,
) -> Response:
    """Token-authenticated download; the token pins tenant and export."""
    tenant_id, export_id = decode_download_token(settings, token)
    factory = request.app.state.session_factory
    with factory() as session:
        bind_tenant(session, tenant_id)
        export = scoped_get_or_404(session, CaseExport, export_id, tenant_id)
        return _zip_response(export, export_bytes(store, export))


@router.get("/v1/case-review-items")
def review_items(ctx: Ctx) -> dict[str, Any]:
    """Incidents the pipeline could not attach confidently (open items only)."""
    stmt = scoped_select(CaseReviewItem, ctx.tenant_id).where(CaseReviewItem.status == "OPEN")
    items = ctx.session.execute(stmt.order_by(CaseReviewItem.created_at)).scalars().all()
    return {
        "items": [
            {
                "id": str(i.id), "incident_id": str(i.incident_id), "reason": i.reason,
                "candidate_case_ids": i.candidate_case_ids,
                "match_ruleset_version": i.match_ruleset_version,
            }
            for i in items
        ]
    }  # fmt: skip
