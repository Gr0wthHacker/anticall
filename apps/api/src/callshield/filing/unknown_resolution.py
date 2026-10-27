"""User-driven exit from an UNKNOWN submission.

An UNKNOWN filing (ambiguous transport outcome) is normally settled by a status lookup. When
that never resolves, the USER may record what they verified themselves. We never resend,
never infer an outcome, and never invent a receipt: a confirmation number is stored only if
the user typed one, labeled USER_ATTESTED, and is never presented as an agency acknowledgment.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from callshield.core.clock import utcnow
from callshield.core.errors import ConflictError
from callshield.db.models.enforce import Case, FilingSubmission
from callshield.db.models.filing_ext import FilingDraftMeta, FilingSubmissionExt
from callshield.domain.enums import EnforceCaseState as E
from callshield.domain.enums import EvidenceLabel
from callshield.domain.enums import SubmissionState as S
from callshield.domain.topics import Topic
from callshield.filing.common import Actor
from callshield.filing.lifecycle import advance_case
from callshield.filing.states import SUBMITTED_EXTERNALLY
from callshield.filing.submission import (
    add_receipt,
    audit_submission,
    move_submission,
    sanitize_text,
)
from callshield.filing.submit import emit_event


class UnknownResolution(StrEnum):
    VERIFIED_SUBMITTED = "verified_submitted"
    VERIFIED_NOT_SUBMITTED = "verified_not_submitted"
    ABANDON = "abandon"


class ResolveUnknownInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: UnknownResolution
    confirmation_number: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _confirmation_only_when_submitted(self) -> Self:
        if self.confirmation_number and self.resolution is not UnknownResolution.VERIFIED_SUBMITTED:
            raise ValueError("a confirmation number only applies to verified_submitted")
        return self


_FAILURE_CODES = {
    UnknownResolution.VERIFIED_NOT_SUBMITTED: "USER_VERIFIED_NOT_SUBMITTED",
    UnknownResolution.ABANDON: "USER_ABANDONED",
}


def resolve_unknown(
    session: Session,
    actor: Actor,
    case: Case,
    sub: FilingSubmission,
    ext: FilingSubmissionExt,
    meta: FilingDraftMeta,
    inp: ResolveUnknownInput,
) -> bool:
    """Apply the user's decision. Returns False for an idempotent replay of the same one."""
    if ext.user_resolution is not None:
        if ext.user_resolution == inp.resolution.value:
            return False
        raise ConflictError(
            "this filing was already resolved differently by the user",
            code="ALREADY_RESOLVED",
            details={"resolution": ext.user_resolution},
        )
    if sub.state != S.UNKNOWN.value:
        raise ConflictError("only an UNKNOWN filing can be resolved here", code="NOT_UNKNOWN")
    if inp.resolution is UnknownResolution.VERIFIED_SUBMITTED:
        _apply_submitted(session, actor, case, sub, ext, meta, inp)
    else:
        _apply_not_submitted(session, actor, case, sub, ext, inp.resolution)
    ext.user_resolution = inp.resolution.value
    ext.user_resolved_at = utcnow()
    audit_submission(
        session, actor, case, "filing.unknown_resolved_by_user", ext, sub,
        resolution=inp.resolution.value,
        confirmation_supplied=bool((inp.confirmation_number or "").strip()),
        evidence_label=EvidenceLabel.USER_ATTESTED.value,
        note_supplied=bool(inp.note),
    )  # fmt: skip
    session.flush()
    return True


def _apply_submitted(
    session: Session,
    actor: Actor,
    case: Case,
    sub: FilingSubmission,
    ext: FilingSubmissionExt,
    meta: FilingDraftMeta,
    inp: ResolveUnknownInput,
) -> None:
    confirmation = (inp.confirmation_number or "").strip() or None
    response: dict[str, Any] = {"user_verified_after_unknown": True}
    if inp.note:
        response["user_reported_text"] = sanitize_text(inp.note)
    receipt = add_receipt(
        session, actor, sub, meta, kind="USER_ATTESTED", label=EvidenceLabel.USER_ATTESTED,
        confirmation_number=confirmation, source="USER_SUPPLIED" if confirmation else "NONE",
        submitted_at=None, endpoint="", response=response,
    )  # fmt: skip
    ctx = {
        "user_resolution": True,
        "external_submission_attested": True,
        "receipt_id": str(receipt.id),
    }
    move_submission(sub, ext, SUBMITTED_EXTERNALLY, ctx)
    advance_case(session, case, E.SUBMITTED, actor.user_id, ctx, "user verified filing was made")
    emit_event(session, actor, case, sub, Topic.FILING_SUBMITTED)


def _apply_not_submitted(
    session: Session,
    actor: Actor,
    case: Case,
    sub: FilingSubmission,
    ext: FilingSubmissionExt,
    resolution: UnknownResolution,
) -> None:
    move_submission(sub, ext, S.FAILED.value, {"user_resolution": True})
    sub.failure_code = _FAILURE_CODES[resolution]
    advance_case(session, case, E.SUBMISSION_FAILED, actor.user_id, reason=resolution.value)
    emit_event(session, actor, case, sub, Topic.FILING_FAILED)
