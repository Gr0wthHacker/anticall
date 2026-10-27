"""API schemas for assessments. There is deliberately no field for liability or money."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from callshield.assessment.facts import UserFacts
from callshield.db.models.filing_ext import AssessmentRun


class AssessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_facts: UserFacts | None = None
    idempotency_key: str | None = Field(default=None, max_length=120)


class IssueOut(BaseModel):
    assessment_id: uuid.UUID
    issue_type: str
    status: str
    supporting_event_ids: list[str]
    missing_requirements: list[dict[str, Any]]
    contradictions: list[dict[str, Any]]
    counsel_reasons: list[dict[str, Any]]
    checklist: dict[str, str]


class AiCommentaryOut(BaseModel):
    label: str
    text: str
    model_version: str | None


class AssessmentRunOut(BaseModel):
    run_id: uuid.UUID
    case_id: uuid.UUID
    ruleset_version: str
    evaluated_at: datetime
    disclaimer: str
    user_facts: dict[str, Any]
    user_facts_label: str = "USER_ATTESTED"
    results: list[IssueOut]
    ai_commentary: AiCommentaryOut | None = None
    created: bool = True


class AssessmentListOut(BaseModel):
    case_id: uuid.UUID
    runs: list[AssessmentRunOut]


def run_to_out(run: AssessmentRun, created: bool = True) -> AssessmentRunOut:
    commentary = (
        AiCommentaryOut(
            label=run.ai_commentary_label or "MODEL_INFERRED",
            text=run.ai_commentary,
            model_version=run.ai_model_version,
        )
        if run.ai_commentary
        else None
    )
    return AssessmentRunOut(
        run_id=run.id,
        case_id=run.case_id,
        ruleset_version=run.ruleset_version,
        evaluated_at=run.evaluated_at,
        disclaimer=run.disclaimer,
        user_facts=run.user_facts or {},
        results=[IssueOut.model_validate(r) for r in run.results],
        ai_commentary=commentary,
        created=created,
    )
