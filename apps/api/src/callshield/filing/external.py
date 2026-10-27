"""User-directed external filing (ENF-016/017): the user filed on the official site themselves.

What we store is the USER's statement, labeled USER_ATTESTED. A confirmation number is
saved only if the user typed one, and is never presented as an agency acknowledgment.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from callshield.core.clock import utcnow
from callshield.core.errors import ConflictError, UnprocessableError
from callshield.db.models.enforce import Case, FilingDraft, FilingSubmission, SubmissionApproval
from callshield.db.models.filing_ext import (
    FilingApprovalDetail,
    FilingDraftMeta,
    FilingSubmissionExt,
)
from callshield.domain.enums import EnforceCaseState as E
from callshield.domain.enums import EvidenceLabel
from callshield.domain.enums import SubmissionState as S
from callshield.domain.topics import Topic
from callshield.filing import queries
from callshield.filing.approvals import approval_problems, void_approval
from callshield.filing.common import Actor
from callshield.filing.destinations import get_destination_by_id
from callshield.filing.lifecycle import advance_case, case_state
from callshield.filing.states import SUBMITTED_EXTERNALLY
from callshield.filing.submission import (
    add_receipt,
    audit_submission,
    create_submission,
    move_submission,
    sanitize_text,
)
from callshield.filing.submit import SubmitResult, emit_event

_CLOCK_SKEW = timedelta(minutes=5)


class ExternalReceiptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filed_at: datetime
    endpoint: str | None = Field(default=None, max_length=500)
    confirmation_number: str | None = Field(default=None, max_length=100)
    response_text: str | None = Field(default=None, max_length=5000)
    idempotency_key: str | None = Field(default=None, max_length=120)


def _validate_times(filed_at: datetime, authorized_at: datetime) -> None:
    if filed_at.tzinfo is None:
        raise UnprocessableError("filed_at must include a timezone", code="INVALID_FIELD")
    if filed_at > utcnow() + _CLOCK_SKEW:
        raise UnprocessableError("filed_at cannot be in the future", code="INVALID_FIELD")
    if filed_at < authorized_at:
        raise UnprocessableError(
            "filing cannot precede the approval of this exact content",
            code="FILED_BEFORE_APPROVAL",
        )


def _verified_approval(
    session: Session, actor: Actor, case: Case, draft: FilingDraft, meta: FilingDraftMeta
) -> tuple[SubmissionApproval, FilingApprovalDetail]:
    found = queries.latest_approval(session, actor.tenant_id, draft.id)
    if found is None:
        raise ConflictError("the draft must be approved before filing", code="NOT_APPROVED")
    problems = approval_problems(session, case, draft, meta, found[1], found[0])
    if problems:
        void_approval(session, actor, case, draft, problems)
        session.commit()
        raise ConflictError(
            "approval is no longer valid; review and approve again",
            code="APPROVAL_INVALID",
            details={"problems": problems.codes},
        )
    return found


def record_external_filing(
    session: Session,
    actor: Actor,
    case: Case,
    draft: FilingDraft,
    meta: FilingDraftMeta,
    inp: ExternalReceiptInput,
) -> SubmitResult:
    approval, _detail = _verified_approval(session, actor, case, draft, meta)
    existing = queries.submission_for_approval(session, actor.tenant_id, approval.id)
    if existing is not None and existing.state == SUBMITTED_EXTERNALLY:
        ext = queries.get_submission_with_ext(session, actor.tenant_id, existing.id)[1]
        return SubmitResult(existing, ext, False)  # idempotent replay
    _validate_times(inp.filed_at, approval.authorized_at)
    sub, ext, _ = create_submission(
        session, actor, case, draft, meta, approval, f"external:{meta.destination_slug}",
        S.EXTERNAL_SUBMISSION_REQUIRED.value, "filing.external_receipt", inp.idempotency_key,
    )  # fmt: skip
    if case_state(case) is E.USER_APPROVED:
        advance_case(session, case, E.EXTERNAL_SUBMISSION_REQUIRED, actor.user_id, reason="manual")
    _store_user_attestation(session, actor, case, meta, sub, ext, inp)
    session.flush()
    return SubmitResult(sub, ext, True)


def _store_user_attestation(
    session: Session,
    actor: Actor,
    case: Case,
    meta: FilingDraftMeta,
    sub: FilingSubmission,
    ext: FilingSubmissionExt,
    inp: ExternalReceiptInput,
) -> None:
    dest = get_destination_by_id(session, meta.destination_id)
    confirmation = (inp.confirmation_number or "").strip() or None
    response = {"user_reported_text": sanitize_text(inp.response_text)} if inp.response_text else {}
    receipt = add_receipt(
        session, actor, sub, meta, kind="USER_ATTESTED", label=EvidenceLabel.USER_ATTESTED,
        confirmation_number=confirmation, source="USER_SUPPLIED" if confirmation else "NONE",
        submitted_at=inp.filed_at, endpoint=inp.endpoint or (dest.source_url if dest else ""),
        response=response,
    )  # fmt: skip
    ctx = {"external_submission_attested": True, "receipt_id": str(receipt.id)}
    move_submission(sub, ext, SUBMITTED_EXTERNALLY, ctx)
    advance_case(session, case, E.SUBMITTED, actor.user_id, ctx, "user attested external filing")
    audit_submission(
        session, actor, case, "filing.submitted_externally", ext, sub,
        confirmation_supplied=confirmation is not None,
    )  # fmt: skip
    emit_event(session, actor, case, sub, Topic.FILING_SUBMITTED)
