"""RESOLVE API (PRD 8.1): /v1/resolution-cases. Thin: logic lives in callshield.resolve."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import select

from callshield.auth.deps import TenantContext, require_role, tenant_context
from callshield.auth.scoping import scoped_get_or_404, scoped_select
from callshield.core.errors import ApiError, ConflictError, PolicyBlockedError, UnprocessableError
from callshield.core.phone import mask_phone
from callshield.db.models.resolve import (
    ContactAttempt,
    ContactDestination,
    IvrPath,
    OptOutEvent,
    ResolutionCase,
)
from callshield.db.models.resolve_ext import (
    AttemptTranscriptLine,
    ContactAttemptDetail,
    RecontactProposal,
    ResolutionNextStep,
    ResolutionTimelineEntry,
)
from callshield.domain.enums import (
    ContactOutcome,
    DestinationProvenance,
    ResolutionState,
    Role,
    Urgency,
)
from callshield.ivr.navigator import PathStep
from callshield.ivr.paths import approve_path
from callshield.resolve import attempts as attempt_svc
from callshield.resolve import cases, machine, next_steps, stop

router = APIRouter(tags=["resolve"])

Ctx = Annotated[TenantContext, Depends(tenant_context)]
Writer = Depends(require_role(Role.CONSUMER_SUBSCRIBER, Role.HOUSEHOLD_ADMIN))
IdemHeader = Annotated[str | None, Header(alias="Idempotency-Key")]
_USER_STATES = frozenset(
    {ResolutionState.MONITORING, ResolutionState.NEXT_STEP_REVIEW, ResolutionState.CLOSED}
)


# ------------------------------------------------------------------ schemas


class CaseCreate(BaseModel):
    line_id: uuid.UUID
    claimed_entity: str = Field(default="", max_length=300)
    objective: str = Field(default="", max_length=4000)
    summary: str = Field(default="", max_length=8000)
    urgency: Urgency = Urgency.NORMAL
    policy_region: str = Field(default="", max_length=32)
    call_session_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class DestinationOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    phone_masked: str
    provenance: str
    verification_status: str
    evidence: dict[str, Any]
    supersedes_id: uuid.UUID | None
    is_current: bool
    verified_at: datetime | None
    dialable: bool


class CaseOut(BaseModel):
    id: uuid.UUID
    protected_line_id: uuid.UUID
    owner_user_id: uuid.UUID | None
    claimed_entity: str
    claimed_entity_status: str
    objective: str
    summary: str
    urgency: str
    policy_region: str
    status: str
    created_at: datetime
    updated_at: datetime


class AttemptOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    destination_id: uuid.UUID
    authorization_id: uuid.UUID | None
    reason: str
    outcome: str | None
    started_at: datetime | None
    ended_at: datetime | None
    ivr_route: list[Any]
    agent_claims: list[Any]
    policy_state: dict[str, Any]
    mode: str
    review_state: str
    source: str
    evidence_label: str
    dialed_phone_masked: str
    stop_condition: str | None
    parked_for_review: bool


class NextStepOut(BaseModel):
    id: uuid.UUID
    kind: str
    rationale: str
    involves_call: bool
    requires_new_authorization: bool
    status: str


class CaseDetail(CaseOut):
    destinations: list[DestinationOut]
    attempts: list[AttemptOut]
    next_steps: list[NextStepOut]
    opt_out_events: list[dict[str, Any]]


class DestinationCreate(BaseModel):
    phone: str = Field(max_length=32)
    provenance: DestinationProvenance = DestinationProvenance.USER_PROVIDED_UNVERIFIED
    source: dict[str, Any] = Field(default_factory=dict)


class VerifyRequest(BaseModel):
    evidence_kind: str
    reference: str = Field(max_length=2000)
    note: str = Field(default="", max_length=2000)
    confirms_entity: bool = False


class PathStepIn(BaseModel):
    digit: str = Field(min_length=1, max_length=1)
    expect_keywords: list[str] = Field(default_factory=list, max_length=8)


class IvrPathIn(BaseModel):
    steps: list[PathStepIn] = Field(min_length=1, max_length=6)
    timeout_seconds: int = Field(default=60, ge=5, le=300)
    fallback: str = "ABORT"


class ReviewIn(BaseModel):
    review_state: str
    notes: str = Field(default="", max_length=2000)
    corrected_outcome: ContactOutcome | None = None


class ManualAttemptIn(BaseModel):
    destination_id: uuid.UUID
    outcome: ContactOutcome
    occurred_at: datetime | None = None
    duration_seconds: int | None = Field(default=None, ge=0, le=86400)
    notes: str = Field(default="", max_length=4000)
    historic: bool = False
    idempotency_key: str | None = None


class StopIn(BaseModel):
    reason: str = Field(default="", max_length=500)
    close: bool = False


class StopOut(BaseModel):
    case_state: str
    aborted_attempts: int
    revoked_authorizations: int
    non_calling_alternatives: list[str]


class CorrectionIn(BaseModel):
    field: str
    new_value: str = Field(max_length=8000)
    reason: str = Field(default="", max_length=1000)


class StateIn(BaseModel):
    target: ResolutionState


class RecontactIn(BaseModel):
    incident_id: uuid.UUID


class RecontactDecisionIn(BaseModel):
    related: bool


# ------------------------------------------------------------------ builders


def _case_out(ctx: TenantContext, case: ResolutionCase) -> CaseOut:
    return CaseOut(
        id=case.id,
        protected_line_id=case.protected_line_id,
        owner_user_id=case.owner_user_id,
        claimed_entity=case.claimed_entity,
        claimed_entity_status=cases.alleged_entity_status(ctx.session, ctx.tenant_id, case),
        objective=case.objective,
        summary=case.summary,
        urgency=case.urgency,
        policy_region=case.policy_region,
        status=case.status,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def _dest_out(
    ctx: TenantContext, dest: ContactDestination, current: set[uuid.UUID]
) -> DestinationOut:
    verified = dest.provenance == DestinationProvenance.INDEPENDENTLY_VERIFIED.value
    return DestinationOut(
        id=dest.id,
        case_id=dest.case_id,
        phone_masked=mask_phone(dest.phone),
        provenance=dest.provenance,
        verification_status=dest.verification_status,
        evidence=dest.source_url_or_evidence,
        supersedes_id=dest.supersedes_id,
        is_current=dest.id in current,
        verified_at=dest.verified_at,
        dialable=verified and dest.id in current,
    )


def _attempt_out(ctx: TenantContext, attempt: ContactAttempt) -> AttemptOut:
    detail = ctx.session.execute(
        scoped_select(ContactAttemptDetail, ctx.tenant_id).where(
            ContactAttemptDetail.attempt_id == attempt.id
        )
    ).scalar_one_or_none()
    return AttemptOut(
        id=attempt.id,
        case_id=attempt.case_id,
        destination_id=attempt.destination_id,
        authorization_id=attempt.user_authorization_id,
        reason=attempt.reason,
        outcome=attempt.outcome,
        started_at=attempt.started_at,
        ended_at=attempt.ended_at,
        ivr_route=attempt.ivr_route,
        agent_claims=attempt.agent_claims,
        policy_state=attempt.policy_state,
        mode=attempt.mode,
        review_state=attempt.review_state,
        source=detail.source if detail else "UNKNOWN",
        evidence_label=detail.evidence_label if detail else "UNKNOWN",
        dialed_phone_masked=detail.dialed_phone_masked if detail else "",
        stop_condition=detail.stop_condition if detail else None,
        parked_for_review=bool(detail and detail.parked_for_review),
    )


def _destinations_out(ctx: TenantContext, case_id: uuid.UUID) -> list[DestinationOut]:
    all_dest = cases.list_destinations(ctx.session, ctx.tenant_id, case_id)
    current = {d.id for d in cases.current_destinations(ctx.session, ctx.tenant_id, case_id)}
    return [_dest_out(ctx, d, current) for d in all_dest]


def _next_steps_out(ctx: TenantContext, case_id: uuid.UUID) -> list[NextStepOut]:
    stmt = (
        scoped_select(ResolutionNextStep, ctx.tenant_id)
        .where(ResolutionNextStep.case_id == case_id)
        .order_by(ResolutionNextStep.created_at, ResolutionNextStep.id)
    )
    return [
        NextStepOut(
            id=r.id,
            kind=r.kind,
            rationale=r.rationale,
            involves_call=r.involves_call,
            requires_new_authorization=r.requires_new_authorization,
            status=r.status,
        )
        for r in ctx.session.execute(stmt).scalars()
    ]


def _attempts_for(ctx: TenantContext, case_id: uuid.UUID) -> list[ContactAttempt]:
    stmt = (
        scoped_select(ContactAttempt, ctx.tenant_id)
        .where(ContactAttempt.case_id == case_id)
        .order_by(ContactAttempt.created_at, ContactAttempt.id)
    )
    return list(ctx.session.execute(stmt).scalars())


def _chain_order(rows: list[OptOutEvent]) -> list[OptOutEvent]:
    """Order by time, but keep each supersedes chain in causal order (ties are possible)."""
    ordered: list[OptOutEvent] = []
    placed: set[uuid.UUID] = set()
    pending = list(rows)
    while pending:
        for row in pending:
            if row.supersedes_id is None or row.supersedes_id in placed:
                ordered.append(row)
                placed.add(row.id)
                pending.remove(row)
                break
        else:
            ordered.extend(pending)
            break
    return ordered


def _detail(ctx: TenantContext, case: ResolutionCase) -> CaseDetail:
    opt_stmt = (
        scoped_select(OptOutEvent, ctx.tenant_id)
        .where(OptOutEvent.case_id == case.id)
        .order_by(OptOutEvent.created_at, OptOutEvent.id)
    )
    opt_events = [
        {
            "id": str(o.id),
            "attempt_id": str(o.attempt_id) if o.attempt_id else None,
            "request_text_version": o.request_text_version,
            "status": o.status,
            "evidence_label": o.evidence_label,
            "acknowledged_at": o.acknowledged_at,
            "supersedes_id": str(o.supersedes_id) if o.supersedes_id else None,
        }
        for o in _chain_order(list(ctx.session.execute(opt_stmt).scalars()))
    ]
    return CaseDetail(
        **_case_out(ctx, case).model_dump(),
        destinations=_destinations_out(ctx, case.id),
        attempts=[_attempt_out(ctx, a) for a in _attempts_for(ctx, case.id)],
        next_steps=_next_steps_out(ctx, case.id),
        opt_out_events=opt_events,
    )


def _idem(header: str | None, body: str | None) -> str | None:
    return header or body


# ------------------------------------------------------------------ cases


@router.post("/v1/resolution-cases", response_model=CaseOut, status_code=201)
def create_case(body: CaseCreate, ctx: Ctx, _w: Any = Writer) -> CaseOut:
    case = cases.create_case(
        ctx.session,
        ctx.tenant_id,
        ctx.principal.user_id,
        cases.NewCase(
            line_id=body.line_id,
            claimed_entity=body.claimed_entity,
            objective=body.objective,
            summary=body.summary,
            urgency=body.urgency,
            policy_region=body.policy_region,
            call_session_ids=body.call_session_ids,
        ),
    )
    return _case_out(ctx, case)


@router.get("/v1/resolution-cases", response_model=list[CaseOut])
def list_cases(ctx: Ctx) -> list[CaseOut]:
    return [_case_out(ctx, c) for c in cases.list_cases(ctx.session, ctx.tenant_id)]


@router.get("/v1/resolution-cases/{case_id}", response_model=CaseDetail)
def get_case(case_id: uuid.UUID, ctx: Ctx) -> CaseDetail:
    return _detail(ctx, cases.get_case(ctx.session, ctx.tenant_id, case_id))


@router.post("/v1/resolution-cases/{case_id}/corrections", response_model=CaseOut)
def correct_case(case_id: uuid.UUID, body: CorrectionIn, ctx: Ctx, _w: Any = Writer) -> CaseOut:
    case = cases.get_case(ctx.session, ctx.tenant_id, case_id)
    cases.correct_case_field(
        ctx.session,
        ctx.tenant_id,
        ctx.principal.user_id,
        case,
        body.field,
        body.new_value,
        body.reason,
    )
    return _case_out(ctx, case)


@router.post("/v1/resolution-cases/{case_id}/state", response_model=CaseOut)
def move_case(case_id: uuid.UUID, body: StateIn, ctx: Ctx, _w: Any = Writer) -> CaseOut:
    """User-driven review moves only: MONITORING, NEXT_STEP_REVIEW, CLOSED."""
    case = cases.get_case(ctx.session, ctx.tenant_id, case_id)
    if body.target not in _USER_STATES:
        raise UnprocessableError("target state not user-selectable", code="BAD_TARGET_STATE")
    machine.transition(
        ctx.session, case, body.target, actor=f"user:{ctx.principal.user_id}", context={}
    )
    return _case_out(ctx, case)


@router.get("/v1/resolution-cases/{case_id}/timeline")
def get_timeline(case_id: uuid.UUID, ctx: Ctx) -> list[dict[str, Any]]:
    cases.get_case(ctx.session, ctx.tenant_id, case_id)
    stmt = (
        scoped_select(ResolutionTimelineEntry, ctx.tenant_id)
        .where(ResolutionTimelineEntry.case_id == case_id)
        .order_by(ResolutionTimelineEntry.created_at, ResolutionTimelineEntry.seq)
    )
    return [
        {
            "seq": e.seq,
            "kind": e.kind,
            "actor": e.actor,
            "attempt_id": str(e.attempt_id) if e.attempt_id else None,
            "data": e.data,
            "created_at": e.created_at.isoformat(),
        }
        for e in ctx.session.execute(stmt).scalars()
    ]


@router.get("/v1/resolution-cases/{case_id}/next-steps", response_model=list[NextStepOut])
def get_next_steps(case_id: uuid.UUID, ctx: Ctx) -> list[NextStepOut]:
    cases.get_case(ctx.session, ctx.tenant_id, case_id)
    return _next_steps_out(ctx, case_id)


# ------------------------------------------------------------------ destinations


@router.post(
    "/v1/resolution-cases/{case_id}/destinations", response_model=DestinationOut, status_code=201
)
def add_destination(
    case_id: uuid.UUID, body: DestinationCreate, ctx: Ctx, _w: Any = Writer
) -> DestinationOut:
    case = cases.get_case(ctx.session, ctx.tenant_id, case_id)
    dest = cases.add_destination(
        ctx.session,
        ctx.tenant_id,
        ctx.principal.user_id,
        case,
        phone=body.phone,
        provenance=body.provenance,
        source=body.source,
    )
    return _dest_out(ctx, dest, {dest.id})


@router.post(
    "/v1/resolution-cases/{case_id}/destinations/{destination_id}/verify",
    response_model=DestinationOut,
)
def verify_destination(
    case_id: uuid.UUID, destination_id: uuid.UUID, body: VerifyRequest, ctx: Ctx, _w: Any = Writer
) -> DestinationOut:
    """Appends a NEW verified destination row (the unverified original is kept)."""
    case = cases.get_case(ctx.session, ctx.tenant_id, case_id)
    dest = cases.get_destination(ctx.session, ctx.tenant_id, case_id, destination_id)
    verified = cases.verify_destination(
        ctx.session,
        ctx.tenant_id,
        ctx.principal.user_id,
        case,
        dest,
        evidence_kind=body.evidence_kind,
        reference=body.reference,
        note=body.note,
        confirms_entity=body.confirms_entity,
    )
    return _dest_out(ctx, verified, {verified.id})


@router.post(
    "/v1/resolution-cases/{case_id}/destinations/{destination_id}/ivr-paths", status_code=201
)
def approve_ivr_path(
    case_id: uuid.UUID, destination_id: uuid.UUID, body: IvrPathIn, ctx: Ctx, _w: Any = Writer
) -> dict[str, Any]:
    """The user approves a DTMF route for this destination (R-004)."""
    case = cases.get_case(ctx.session, ctx.tenant_id, case_id)
    dest = cases.get_destination(ctx.session, ctx.tenant_id, case_id, destination_id)
    if case.owner_user_id != ctx.principal.user_id:
        raise PolicyBlockedError(
            "only the case owner may approve an IVR path", code="NOT_CASE_OWNER"
        )
    steps = tuple(
        PathStep(s.digit, tuple(k.lower() for k in s.expect_keywords)) for s in body.steps
    )
    row = approve_path(
        ctx.session,
        ctx.tenant_id,
        dest.id,
        steps,
        timeout_seconds=body.timeout_seconds,
        fallback=body.fallback,
    )
    return {
        "id": str(row.id),
        "version": row.version,
        "choices": row.choices,
        "user_approved": row.user_approved,
        "confidence": row.confidence,
    }


@router.get("/v1/resolution-cases/{case_id}/destinations/{destination_id}/ivr-paths")
def list_ivr_paths(case_id: uuid.UUID, destination_id: uuid.UUID, ctx: Ctx) -> list[dict[str, Any]]:
    cases.get_case(ctx.session, ctx.tenant_id, case_id)
    dest = cases.get_destination(ctx.session, ctx.tenant_id, case_id, destination_id)
    stmt = scoped_select(IvrPath, ctx.tenant_id).where(IvrPath.destination_id == dest.id)
    return [
        {
            "id": str(p.id),
            "version": p.version,
            "choices": p.choices,
            "user_approved": p.user_approved,
            "confidence": p.confidence,
            "last_tested_at": p.last_tested_at,
            "timeout_seconds": p.timeout_seconds,
            "fallback": p.fallback,
        }
        for p in ctx.session.execute(stmt.order_by(IvrPath.version)).scalars()
    ]


# ------------------------------------------------------------------ authorization + contact


def _outbound_opt_out_removed() -> ApiError:
    """PRD v3 opt-out outbound path was removed by CHANGE_callback_flow (ADR 0006)."""
    return ApiError(
        "The outbound opt-out call was removed. Use the callback API on a logged inbound call "
        "(POST /v1/calls/{id}/callbacks) or the complaint flow (POST /v1/cases).",
        code="OUTBOUND_OPT_OUT_REMOVED",
        status_code=410,
        details={
            "callback_api": "/v1/calls/{id}/callbacks",
            "eligibility": "/v1/calls/{id}/callback-eligibility",
            "complaint_flow": "/v1/cases",
        },
    )


@router.post("/v1/resolution-cases/{case_id}/contact-authorizations", status_code=410)
def create_contact_authorization(case_id: uuid.UUID, ctx: Ctx, _w: Any = Writer) -> None:
    """Removed (410): opt-out contact authorizations no longer exist."""
    cases.get_case(ctx.session, ctx.tenant_id, case_id)  # 404 for other tenants
    raise _outbound_opt_out_removed()


@router.post("/v1/resolution-cases/{case_id}/start-contact", status_code=410)
def start_contact_route(case_id: uuid.UUID, ctx: Ctx) -> None:
    """Removed (410): the AI opt-out call to a researched company contact is gone."""
    cases.get_case(ctx.session, ctx.tenant_id, case_id)  # 404 for other tenants
    raise _outbound_opt_out_removed()


# ------------------------------------------------------------------ attempts


@router.post(
    "/v1/resolution-cases/{case_id}/attempts/manual", response_model=AttemptOut, status_code=201
)
def log_manual_attempt(
    case_id: uuid.UUID,
    body: ManualAttemptIn,
    ctx: Ctx,
    idempotency_key: IdemHeader = None,
    _w: Any = Writer,
) -> AttemptOut:
    """R-012: the user's own attempt (USER_ATTESTED). Not capped, not auto-verified."""
    case = cases.get_case(ctx.session, ctx.tenant_id, case_id)
    attempt, _ = attempt_svc.log_manual_attempt(
        ctx.session,
        ctx.tenant_id,
        ctx.principal.user_id,
        case,
        body.destination_id,
        outcome=body.outcome,
        idempotency_key=_idem(idempotency_key, body.idempotency_key) or "",
        occurred_at=body.occurred_at,
        duration_seconds=body.duration_seconds,
        notes=body.notes,
        historic=body.historic,
    )
    return _attempt_out(ctx, attempt)


@router.get("/v1/resolution-cases/{case_id}/attempts/{attempt_id}")
def get_attempt(case_id: uuid.UUID, attempt_id: uuid.UUID, ctx: Ctx) -> dict[str, Any]:
    cases.get_case(ctx.session, ctx.tenant_id, case_id)
    attempt = attempt_svc.get_attempt(ctx.session, ctx.tenant_id, case_id, attempt_id)
    lines = ctx.session.execute(
        select(AttemptTranscriptLine)
        .where(
            AttemptTranscriptLine.tenant_id == ctx.tenant_id,
            AttemptTranscriptLine.attempt_id == attempt.id,
        )
        .order_by(AttemptTranscriptLine.seq)
    ).scalars()
    return {
        "attempt": _attempt_out(ctx, attempt).model_dump(mode="json"),
        "transcript": [
            {
                "seq": ln.seq,
                "speaker": ln.speaker,
                "text": ln.text,
                "label": ln.label,
                "complete": ln.complete,
            }
            for ln in lines
        ],
    }


@router.post(
    "/v1/resolution-cases/{case_id}/attempts/{attempt_id}/review", response_model=AttemptOut
)
def review_attempt(
    case_id: uuid.UUID, attempt_id: uuid.UUID, body: ReviewIn, ctx: Ctx, _w: Any = Writer
) -> AttemptOut:
    case = cases.get_case(ctx.session, ctx.tenant_id, case_id)
    attempt = attempt_svc.get_attempt(ctx.session, ctx.tenant_id, case_id, attempt_id)
    attempt_svc.review_attempt(
        ctx.session,
        ctx.tenant_id,
        ctx.principal.user_id,
        case,
        attempt,
        review_state=body.review_state,
        notes=body.notes,
        corrected_outcome=body.corrected_outcome,
    )
    return _attempt_out(ctx, attempt)


@router.post("/v1/resolution-cases/{case_id}/stop", response_model=StopOut)
def stop_case(case_id: uuid.UUID, body: StopIn, ctx: Ctx, _w: Any = Writer) -> StopOut:
    case = cases.get_case(ctx.session, ctx.tenant_id, case_id)
    result = stop.stop_case(
        ctx.session,
        ctx.tenant_id,
        ctx.principal.user_id,
        case,
        reason=body.reason,
        close=body.close,
    )
    return StopOut(
        case_state=result.state,
        aborted_attempts=result.aborted_attempts,
        revoked_authorizations=result.revoked_authorizations,
        non_calling_alternatives=[str(a) for a in result.alternatives],
    )


# ------------------------------------------------------------------ recontact (R-010)


@router.post("/v1/resolution-cases/{case_id}/recontacts", status_code=201)
def propose_recontact(
    case_id: uuid.UUID, body: RecontactIn, ctx: Ctx, _w: Any = Writer
) -> dict[str, Any]:
    cases.get_case(ctx.session, ctx.tenant_id, case_id)
    proposal = next_steps.propose_recontact(ctx.session, ctx.tenant_id, case_id, body.incident_id)
    return {"id": str(proposal.id), "status": proposal.status}


@router.post("/v1/resolution-cases/{case_id}/recontacts/{proposal_id}/decision")
def decide_recontact(
    case_id: uuid.UUID,
    proposal_id: uuid.UUID,
    body: RecontactDecisionIn,
    ctx: Ctx,
    _w: Any = Writer,
) -> dict[str, Any]:
    case = cases.get_case(ctx.session, ctx.tenant_id, case_id)
    proposal = scoped_get_or_404(ctx.session, RecontactProposal, proposal_id, ctx.tenant_id)
    if proposal.case_id != case.id:
        raise ConflictError("proposal belongs to another case", code="PROPOSAL_MISMATCH")
    decided = next_steps.decide_recontact(
        ctx.session, ctx.tenant_id, ctx.principal.user_id, case, proposal_id, related=body.related
    )
    return {"id": str(decided.id), "status": decided.status, "case_state": case.status}
