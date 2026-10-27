"""Filing endpoints (PRD 19.7): drafts, approval, submit, external receipts, supplements.

Nothing here transmits anything unless a registered documented_api adapter exists, the
deployment has `filing_submission_enabled`, and the user approved the exact payload digest.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from callshield.auth.deps import TenantContext, get_app_settings, require_role, tenant_context
from callshield.auth.scoping import scoped_get_or_404
from callshield.auth.tokens import Principal
from callshield.core.config import Settings
from callshield.core.errors import UnprocessableError
from callshield.db.models.enforce import Case, FilingDraft, FilingSubmission
from callshield.db.models.filing_ext import FilingDraftMeta, FilingSubmissionExt
from callshield.domain.enums import Role
from callshield.filing import queries
from callshield.filing.approvals import ApprovalInput, approve_draft
from callshield.filing.common import Actor
from callshield.filing.destinations import get_destination_by_id, list_destinations
from callshield.filing.drafts import create_draft, parent_for_supplement
from callshield.filing.external import ExternalReceiptInput, record_external_filing
from callshield.filing.handoff import draft_view, prepared_for
from callshield.filing.schemas import (
    ApprovalOut,
    DestinationOut,
    DraftCreateRequest,
    DraftOut,
    FilingOut,
    SubmitRequest,
    approval_out,
    destination_out,
    draft_out,
    filing_out,
)
from callshield.filing.submit import SubmitResult, refresh_status, submit_draft
from callshield.filing.unknown_resolution import ResolveUnknownInput, resolve_unknown

router = APIRouter(tags=["filings"])

_WRITER_ROLES = (Role.CONSUMER_SUBSCRIBER, Role.HOUSEHOLD_ADMIN)


def writer_context(
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    _role: Annotated[Principal, Depends(require_role(*_WRITER_ROLES))],
) -> TenantContext:
    """Only the account holder (or household admin) may draft, attest, or record a filing."""
    return ctx


Reader = Annotated[TenantContext, Depends(tenant_context)]
Writer = Annotated[TenantContext, Depends(writer_context)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]


def _actor(ctx: TenantContext) -> Actor:
    return Actor(ctx.tenant_id, ctx.principal.user_id, ctx.principal.role)


def _prepared(
    ctx: TenantContext, draft: FilingDraft, meta: FilingDraftMeta
) -> dict[str, object] | None:
    dest = get_destination_by_id(ctx.session, meta.destination_id)
    return prepared_for(dest, draft_view(draft, meta)) if dest else None


def _draft_response(
    ctx: TenantContext, draft: FilingDraft, meta: FilingDraftMeta, created: bool
) -> DraftOut:
    return draft_out(draft, meta, created, _prepared(ctx, draft, meta))


def _filing_response(ctx: TenantContext, result: SubmitResult, settings: Settings) -> FilingOut:
    receipts = list(queries.receipts_for(ctx.session, ctx.tenant_id, result.submission.id))
    return filing_out(
        result.submission,
        result.ext,
        result.ext.case_id,
        receipts,
        result.created,
        settings.filing_unknown_verify_after_hours,
    )


@router.get("/v1/filing-destinations", response_model=list[DestinationOut])
def filing_destinations(ctx: Reader) -> list[DestinationOut]:
    """The registry with last-reviewed date and honest capability (none is an API today)."""
    return [destination_out(d) for d in list_destinations(ctx.session)]


@router.post("/v1/cases/{case_id}/filings/drafts", response_model=DraftOut, status_code=201)
def create_filing_draft(
    case_id: uuid.UUID, body: DraftCreateRequest, ctx: Writer, response: Response
) -> DraftOut:
    case = scoped_get_or_404(ctx.session, Case, case_id, ctx.tenant_id)
    result = create_draft(
        ctx.session,
        _actor(ctx),
        case,
        body.destination,
        body.fields,
        body.scope,
        body.idempotency_key,
    )
    if not result.created:
        response.status_code = 200
    return _draft_response(ctx, result.draft, result.meta, result.created)


@router.get("/v1/filing-drafts/{draft_id}", response_model=DraftOut)
def get_filing_draft(draft_id: uuid.UUID, ctx: Reader) -> DraftOut:
    draft, meta = queries.get_draft_with_meta(ctx.session, ctx.tenant_id, draft_id)
    return _draft_response(ctx, draft, meta, False)


@router.post("/v1/filing-drafts/{draft_id}/approve", response_model=ApprovalOut)
def approve_filing_draft(draft_id: uuid.UUID, body: ApprovalInput, ctx: Writer) -> ApprovalOut:
    draft, meta = queries.get_draft_with_meta(ctx.session, ctx.tenant_id, draft_id)
    case = scoped_get_or_404(ctx.session, Case, draft.case_id, ctx.tenant_id)
    result = approve_draft(ctx.session, _actor(ctx), case, draft, meta, body)
    return approval_out(result.approval, result.detail, result.created)


@router.post("/v1/filing-drafts/{draft_id}/submit", response_model=FilingOut)
def submit_filing_draft(
    draft_id: uuid.UUID, body: SubmitRequest, ctx: Writer, settings: AppSettings
) -> FilingOut:
    """422 UNSUPPORTED_DESTINATION unless a documented API adapter is registered and enabled."""
    draft, meta = queries.get_draft_with_meta(ctx.session, ctx.tenant_id, draft_id)
    case = scoped_get_or_404(ctx.session, Case, draft.case_id, ctx.tenant_id)
    result = submit_draft(
        ctx.session, settings, _actor(ctx), case, draft, meta, body.idempotency_key
    )
    return _filing_response(ctx, result, settings)


@router.post("/v1/filing-drafts/{draft_id}/external-receipt", response_model=FilingOut)
def record_external_receipt(
    draft_id: uuid.UUID, body: ExternalReceiptInput, ctx: Writer, settings: AppSettings
) -> FilingOut:
    """Record that YOU filed on the official site (USER_ATTESTED, not agency proof)."""
    draft, meta = queries.get_draft_with_meta(ctx.session, ctx.tenant_id, draft_id)
    case = scoped_get_or_404(ctx.session, Case, draft.case_id, ctx.tenant_id)
    result = record_external_filing(ctx.session, _actor(ctx), case, draft, meta, body)
    return _filing_response(ctx, result, settings)


def _load_filing(
    ctx: TenantContext, filing_id: uuid.UUID
) -> tuple[FilingSubmission, FilingSubmissionExt]:
    return queries.get_submission_with_ext(ctx.session, ctx.tenant_id, filing_id)


@router.get("/v1/filings/{filing_id}", response_model=FilingOut)
def get_filing(filing_id: uuid.UUID, ctx: Reader, settings: AppSettings) -> FilingOut:
    """State and recorded receipts only. A receipt is never inferred or invented."""
    sub, ext = _load_filing(ctx, filing_id)
    return _filing_response(ctx, SubmitResult(sub, ext, False), settings)


@router.post("/v1/filings/{filing_id}/status-check", response_model=FilingOut)
def check_filing_status(filing_id: uuid.UUID, ctx: Writer, settings: AppSettings) -> FilingOut:
    """Ask the agency's status interface. Never re-sends the filing."""
    sub, ext = _load_filing(ctx, filing_id)
    _draft, meta = queries.get_draft_with_meta(ctx.session, ctx.tenant_id, sub.draft_id)
    case = scoped_get_or_404(ctx.session, Case, ext.case_id, ctx.tenant_id)
    refresh_status(ctx.session, _actor(ctx), case, sub, ext, meta)
    return _filing_response(ctx, SubmitResult(sub, ext, False), settings)


@router.post("/v1/filings/{filing_id}/resolve-unknown", response_model=FilingOut)
def resolve_unknown_filing(
    filing_id: uuid.UUID, body: ResolveUnknownInput, ctx: Writer, settings: AppSettings
) -> FilingOut:
    """User records what they verified about an UNKNOWN filing. Never resends or invents."""
    sub, ext = _load_filing(ctx, filing_id)
    _draft, meta = queries.get_draft_with_meta(ctx.session, ctx.tenant_id, sub.draft_id)
    case = scoped_get_or_404(ctx.session, Case, ext.case_id, ctx.tenant_id)
    created = resolve_unknown(ctx.session, _actor(ctx), case, sub, ext, meta, body)
    return _filing_response(ctx, SubmitResult(sub, ext, created), settings)


@router.post("/v1/filings/{filing_id}/supplements", response_model=DraftOut, status_code=201)
def create_supplement(
    filing_id: uuid.UUID, body: DraftCreateRequest, ctx: Writer, response: Response
) -> DraftOut:
    """A separate draft that needs its own approval. The earlier filing is never altered."""
    actor = _actor(ctx)
    parent = parent_for_supplement(ctx.session, actor, filing_id)
    if body.destination != parent.ext.destination_slug:
        raise UnprocessableError(
            "a supplement goes to the same destination as the filing it supplements",
            code="DESTINATION_MISMATCH",
            details={"expected": parent.ext.destination_slug},
        )
    case = scoped_get_or_404(ctx.session, Case, parent.ext.case_id, ctx.tenant_id)
    result = create_draft(
        ctx.session,
        actor,
        case,
        body.destination,
        body.fields,
        body.scope,
        body.idempotency_key,
        parent,
    )
    if not result.created:
        response.status_code = 200
    return _draft_response(ctx, result.draft, result.meta, result.created)
