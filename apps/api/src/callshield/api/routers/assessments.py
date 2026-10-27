"""Deterministic issue-spotting endpoints (PRD 19.7 / 19.9). Not legal advice."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from callshield.assessment.schemas import (
    AssessmentListOut,
    AssessmentRunOut,
    AssessRequest,
    run_to_out,
)
from callshield.assessment.service import latest_runs, run_assessment
from callshield.auth.deps import TenantContext, tenant_context
from callshield.auth.scoping import scoped_get_or_404
from callshield.core.errors import ForbiddenError
from callshield.db.models.enforce import Case
from callshield.domain.enums import Role

router = APIRouter(tags=["assessments"])

_FACT_ROLES = frozenset({Role.CONSUMER_SUBSCRIBER, Role.HOUSEHOLD_ADMIN})


@router.post("/v1/cases/{case_id}/assess", response_model=AssessmentRunOut)
def assess_case(
    case_id: uuid.UUID,
    body: AssessRequest,
    ctx: Annotated[TenantContext, Depends(tenant_context)],
) -> AssessmentRunOut:
    """Run the versioned ruleset. Output is an issue-spotting flag for counsel only."""
    case = scoped_get_or_404(ctx.session, Case, case_id, ctx.tenant_id)
    if body.user_facts is not None and ctx.principal.role not in _FACT_ROLES:
        raise ForbiddenError("only the account holder may state facts", code="INSUFFICIENT_ROLE")
    run, created = run_assessment(
        ctx.session, case, ctx.principal.user_id, body.user_facts, body.idempotency_key
    )
    return run_to_out(run, created)


@router.get("/v1/cases/{case_id}/assessments", response_model=AssessmentListOut)
def list_assessments(
    case_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(tenant_context)]
) -> AssessmentListOut:
    case = scoped_get_or_404(ctx.session, Case, case_id, ctx.tenant_id)
    runs = latest_runs(ctx.session, ctx.tenant_id, case.id)
    return AssessmentListOut(case_id=case.id, runs=[run_to_out(r, created=False) for r in runs])
