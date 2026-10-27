"""Run and persist deterministic assessments (append-only; a new run is new rows)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.assessment.engine import IssueResult, evaluate_case
from callshield.assessment.facts import UserFacts, load_case_facts
from callshield.assessment.rulesets import get_ruleset
from callshield.assessment.safety import assert_clean, find_forbidden
from callshield.audit.service import actor_for_user, record_case_audit
from callshield.auth.scoping import scoped_select
from callshield.core.clock import utcnow
from callshield.core.hashing import sha256_json
from callshield.core.idempotency import make_idempotency_key
from callshield.core.ids import new_id
from callshield.db.models.enforce import Case, CaseAssessment
from callshield.db.models.filing_ext import AssessmentRun
from callshield.domain.enums import (
    ActorType,
    AssessmentStatus,
    EventOrigin,
    EvidenceLabel,
    LegalAssessment,
)
from callshield.domain.topics import Topic
from callshield.ledger.service import append_event

# (issue results as JSON) -> (commentary text, model_version). Default: none.
CommentaryProvider = Callable[[list[dict[str, Any]]], tuple[str, str]]

_FLAGGED = frozenset({AssessmentStatus.POSSIBLY_RELEVANT, AssessmentStatus.REQUIRES_COUNSEL_REVIEW})


def latest_runs(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> Sequence[AssessmentRun]:
    stmt = (
        scoped_select(AssessmentRun, tenant_id)
        .where(AssessmentRun.case_id == case_id)
        .order_by(AssessmentRun.evaluated_at.desc(), AssessmentRun.id)
    )
    return session.execute(stmt).scalars().all()


def prior_user_facts(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID) -> UserFacts:
    runs = latest_runs(session, tenant_id, case_id)
    return UserFacts.model_validate(runs[0].user_facts) if runs else UserFacts()


def find_run_by_key(session: Session, tenant_id: uuid.UUID, key: str) -> AssessmentRun | None:
    stmt = scoped_select(AssessmentRun, tenant_id).where(AssessmentRun.idempotency_key == key)
    return session.execute(stmt).scalar_one_or_none()


def _persist_rows(
    session: Session, case: Case, ruleset_version: str, results: list[IssueResult]
) -> list[dict[str, Any]]:
    now = utcnow()
    out: list[dict[str, Any]] = []
    for res in results:
        row = CaseAssessment(
            id=new_id(),
            tenant_id=case.tenant_id,
            case_id=case.id,
            ruleset_version=ruleset_version,
            model_version=None,
            issue_type=res.issue_type,
            status=res.status.value,
            supporting_event_ids=res.supporting_event_ids,
            missing_requirements=res.missing_requirements,
            evaluated_at=now,
        )
        session.add(row)
        out.append({"assessment_id": str(row.id), **res.to_json()})
    return out


def _sync_case_flag(case: Case, results: list[IssueResult]) -> None:
    """legal_assessment is advisory: flagged => POTENTIALLY_RELEVANT. Never COUNSEL_REVIEWED."""
    if case.legal_assessment == LegalAssessment.COUNSEL_REVIEWED.value:
        return
    flagged = any(r.status in _FLAGGED for r in results)
    case.legal_assessment = (
        LegalAssessment.POTENTIALLY_RELEVANT.value
        if flagged
        else LegalAssessment.NOT_REVIEWED.value
    )


def _commentary(
    provider: CommentaryProvider | None, rows: list[dict[str, Any]]
) -> tuple[str | None, str | None]:
    if provider is None:
        return None, None
    text, model_version = provider(rows)
    if find_forbidden(text):
        return None, None  # discard commentary that asserts proof or amounts
    return text, model_version


def run_assessment(
    session: Session,
    case: Case,
    user_id: uuid.UUID,
    new_facts: UserFacts | None,
    idempotency_key: str | None,
    commentary_provider: CommentaryProvider | None = None,
) -> tuple[AssessmentRun, bool]:
    """Evaluate the case and append results. Returns (run, created)."""
    key = make_idempotency_key("assess", case.id, idempotency_key) if idempotency_key else None
    if key and (existing := find_run_by_key(session, case.tenant_id, key)) is not None:
        return existing, False
    ruleset = get_ruleset()
    base = prior_user_facts(session, case.tenant_id, case.id)
    user_facts = new_facts.merged_over(base) if new_facts else base
    facts = load_case_facts(session, case, user_facts)
    results = evaluate_case(facts, ruleset)
    rows = _persist_rows(session, case, ruleset["ruleset_version"], results)
    text, model_version = _commentary(commentary_provider, rows)
    assert_clean([rows, ruleset["disclaimer"]], "assessment output")
    run = AssessmentRun(
        id=new_id(),
        tenant_id=case.tenant_id,
        case_id=case.id,
        ruleset_version=ruleset["ruleset_version"],
        user_facts=user_facts.to_json(),
        facts_digest=sha256_json({"snapshot": facts.snapshot.digest, "user": user_facts.to_json()}),
        results=rows,
        disclaimer=ruleset["disclaimer"],
        ai_commentary=text,
        ai_commentary_label=EvidenceLabel.MODEL_INFERRED.value if text else None,
        ai_model_version=model_version,
        actor=actor_for_user(user_id),
        idempotency_key=key,
        evaluated_at=utcnow(),
    )
    session.add(run)
    _sync_case_flag(case, results)
    _record(session, case, run, user_id, key)
    session.flush()
    return run, True


def _record(
    session: Session, case: Case, run: AssessmentRun, user_id: uuid.UUID, key: str | None
) -> None:
    summary = {r["issue_type"]: r["status"] for r in run.results}
    record_case_audit(
        session,
        case.tenant_id,
        case.id,
        ActorType.USER,
        "assessment.completed",
        {
            "run_id": str(run.id),
            "ruleset_version": run.ruleset_version,
            "scope": "case.assess",
            "authorization_basis": "authenticated tenant user",
            "idempotency_key": key,
            "statuses": summary,
        },
        actor_id=actor_for_user(user_id),
    )
    append_event(
        session,
        case.tenant_id,
        Topic.ASSESSMENT_COMPLETED,
        {"case_id": str(case.id), "run_id": str(run.id), "statuses": summary},
        EventOrigin.USER_ACTION,
        make_idempotency_key("assessment", run.id),
    )


def assessments_for_case(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> Sequence[CaseAssessment]:
    stmt = (
        select(CaseAssessment)
        .where(CaseAssessment.tenant_id == tenant_id, CaseAssessment.case_id == case_id)
        .order_by(CaseAssessment.evaluated_at.desc(), CaseAssessment.issue_type)
    )
    return session.execute(stmt).scalars().all()
