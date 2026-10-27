"""Attestation and approval (ENF-014, PRD 21.3).

An approval binds ONE user attestation to ONE exact payload digest and evidence snapshot.
It is per submission, never reused across drafts or supplements, and becomes void the
moment the payload or the evidence snapshot changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from callshield.audit.service import record_case_audit
from callshield.core.clock import utcnow
from callshield.core.errors import ConflictError, UnprocessableError
from callshield.core.idempotency import make_idempotency_key
from callshield.core.ids import new_id
from callshield.db.models.enforce import Case, FilingDraft, SubmissionApproval
from callshield.db.models.filing_ext import FilingApprovalDetail, FilingDraftMeta
from callshield.domain.enums import (
    ActorType,
    EventOrigin,
    FilingDraftReviewState,
    Role,
)
from callshield.domain.enums import (
    EnforceCaseState as E,
)
from callshield.domain.topics import Topic
from callshield.filing import queries
from callshield.filing.attestation import (
    SUPPORTED_VERSIONS,
    required_display_items,
    required_statement_ids,
)
from callshield.filing.common import Actor, command_audit_data
from callshield.filing.destinations import get_destination_by_id
from callshield.filing.drafts import payload_matches_digest
from callshield.filing.lifecycle import advance_case, case_state
from callshield.filing.snapshot import load_case_snapshot
from callshield.ledger.service import append_event

_LINE_ADMIN_ROLES = frozenset({Role.HOUSEHOLD_ADMIN})


class ApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_digest: str = Field(min_length=64, max_length=64)
    evidence_snapshot_digest: str = Field(min_length=64, max_length=64)
    attestation_version: str
    accepted_statement_ids: list[str]
    displayed_items: list[str]
    sharing_confirmed: bool
    idempotency_key: str | None = Field(default=None, max_length=120)


@dataclass(frozen=True)
class ApprovalResult:
    approval: SubmissionApproval
    detail: FilingApprovalDetail
    created: bool


@dataclass
class ApprovalProblems:
    codes: list[str] = field(default_factory=list)

    def add(self, code: str) -> None:
        self.codes.append(code)

    def __bool__(self) -> bool:
        return bool(self.codes)


def _attestation_problems(inp: ApprovalInput) -> list[str]:
    problems: list[str] = []
    if inp.attestation_version not in SUPPORTED_VERSIONS:
        problems.append("UNSUPPORTED_ATTESTATION_VERSION")
    if not set(required_statement_ids()) <= set(inp.accepted_statement_ids):
        problems.append("STATEMENTS_NOT_ALL_ACCEPTED")
    if not set(required_display_items()) <= set(inp.displayed_items):
        problems.append("REQUIRED_ITEMS_NOT_DISPLAYED")
    if not inp.sharing_confirmed:
        problems.append("SHARING_NOT_CONFIRMED")
    return problems


def line_authorized(session: Session, actor: Actor, case: Case) -> bool:
    """The protected line is verified and this user owns it (or is the household admin)."""
    snapshot = load_case_snapshot(session, case)
    line = snapshot.line
    if line is None or line.verification_status != "VERIFIED":
        return False
    return line.owner_user_id in (None, actor.user_id) or actor.role in _LINE_ADMIN_ROLES


def destination_validated(session: Session, meta: FilingDraftMeta) -> bool:
    dest = get_destination_by_id(session, meta.destination_id)
    return bool(dest and dest.validated and dest.active)


def approval_problems(
    session: Session,
    case: Case,
    draft: FilingDraft,
    meta: FilingDraftMeta,
    detail: FilingApprovalDetail,
    approval: SubmissionApproval,
) -> ApprovalProblems:
    """Is this approval still valid for the draft as it is NOW? (Used before any send.)"""
    problems = ApprovalProblems()
    if draft.review_state != FilingDraftReviewState.APPROVED.value:
        problems.add("DRAFT_NOT_APPROVED_OR_SUPERSEDED")
    if not payload_matches_digest(draft, meta):
        problems.add("PAYLOAD_TAMPERED")
    if approval.approved_payload_digest != meta.payload_digest:
        problems.add("PAYLOAD_DIGEST_CHANGED")
    if approval.attestation_version not in SUPPORTED_VERSIONS:
        problems.add("UNSUPPORTED_ATTESTATION_VERSION")
    if load_case_snapshot(session, case).digest != detail.evidence_snapshot_digest:
        problems.add("EVIDENCE_SNAPSHOT_CHANGED")
    return problems


def _validate_for_approval(
    session: Session,
    actor: Actor,
    case: Case,
    draft: FilingDraft,
    meta: FilingDraftMeta,
    inp: ApprovalInput,
) -> str:
    """Raise unless every precondition holds. Returns the current snapshot digest."""
    if not meta.completeness.get("complete"):
        raise UnprocessableError(
            "required fields are missing",
            code="INCOMPLETE_DRAFT",
            details={"missing": meta.completeness.get("missing", [])},
        )
    if draft.review_state not in {
        FilingDraftReviewState.NEEDS_REVIEW.value,
        FilingDraftReviewState.REVIEWED.value,
    }:
        raise ConflictError(
            f"draft cannot be approved in review_state {draft.review_state}",
            code="DRAFT_NOT_APPROVABLE",
        )
    if not payload_matches_digest(draft, meta):
        raise ConflictError("stored payload no longer matches its digest", code="PAYLOAD_TAMPERED")
    if inp.payload_digest != meta.payload_digest:
        raise ConflictError(
            "approval must name the exact payload digest shown to the user", code="DIGEST_MISMATCH"
        )
    bad = _attestation_problems(inp)
    if bad:
        raise UnprocessableError(
            "attestation incomplete", code="ATTESTATION_INCOMPLETE", details={"problems": bad}
        )
    current = load_case_snapshot(session, case).digest
    if inp.evidence_snapshot_digest != current or current != draft.evidence_snapshot_digest:
        raise ConflictError(
            "evidence changed since this draft was built; regenerate the draft",
            code="SNAPSHOT_CHANGED",
        )
    return current


def approve_draft(
    session: Session,
    actor: Actor,
    case: Case,
    draft: FilingDraft,
    meta: FilingDraftMeta,
    inp: ApprovalInput,
) -> ApprovalResult:
    key = (
        make_idempotency_key("approve", draft.id, inp.idempotency_key)
        if inp.idempotency_key
        else None
    )
    if key and (hit := queries.approval_by_key(session, actor.tenant_id, key)) is not None:
        approval = session.get(SubmissionApproval, hit.approval_id)
        if approval is not None and approval.tenant_id == actor.tenant_id:
            return ApprovalResult(approval, hit, False)
    existing = queries.latest_approval(session, actor.tenant_id, draft.id)
    if existing and draft.review_state == FilingDraftReviewState.APPROVED.value:
        return ApprovalResult(existing[0], existing[1], False)
    snapshot_digest = _validate_for_approval(session, actor, case, draft, meta, inp)
    authorized = line_authorized(session, actor, case)
    dest_ok = destination_validated(session, meta)
    context = {
        "line_authorized": authorized,
        "evidence_snapshot_digest": snapshot_digest,
        "required_fields_complete": True,
        "attestation_completed": True,
        "sharing_confirmed": inp.sharing_confirmed,
        "destination_validated": dest_ok,
        "approved_payload_digest": inp.payload_digest,
        "payload_digest_matches": True,
        "snapshot_digest_current": True,
    }
    if case_state(case) is not E.DRAFT_READY:
        raise ConflictError(
            f"case must be DRAFT_READY to approve (is {case.state})", code="CASE_NOT_DRAFT_READY"
        )
    advance_case(session, case, E.USER_APPROVED, actor.user_id, context, "filing approved")
    return _store_approval(
        session, actor, case, draft, meta, inp, snapshot_digest, authorized, dest_ok, key
    )


def _store_approval(
    session: Session,
    actor: Actor,
    case: Case,
    draft: FilingDraft,
    meta: FilingDraftMeta,
    inp: ApprovalInput,
    snapshot_digest: str,
    authorized: bool,
    dest_ok: bool,
    key: str | None,
) -> ApprovalResult:
    approval = SubmissionApproval(
        id=new_id(),
        tenant_id=actor.tenant_id,
        draft_id=draft.id,
        user_id=actor.user_id,
        approved_payload_digest=inp.payload_digest,
        attestation_version=inp.attestation_version,
        authorized_at=utcnow(),
    )
    detail = FilingApprovalDetail(
        id=new_id(),
        tenant_id=actor.tenant_id,
        approval_id=approval.id,
        draft_id=draft.id,
        statement_ids=sorted(inp.accepted_statement_ids),
        displayed_items=sorted(inp.displayed_items),
        completed_fields=[c["field"] for c in meta.completeness.get("checked", [])],
        sharing_confirmed=inp.sharing_confirmed,
        destination_validated=dest_ok,
        line_authorized=authorized,
        evidence_snapshot_digest=snapshot_digest,
        scope="filing.approve",
        authorization_basis="account holder attested to the exact payload digest",
        idempotency_key=key,
    )
    session.add_all([approval, detail])
    draft.review_state = FilingDraftReviewState.APPROVED.value
    _record_approval(session, actor, case, draft, approval, detail, key)
    session.flush()
    return ApprovalResult(approval, detail, True)


def _record_approval(
    session: Session,
    actor: Actor,
    case: Case,
    draft: FilingDraft,
    approval: SubmissionApproval,
    detail: FilingApprovalDetail,
    key: str | None,
) -> None:
    data = command_audit_data(
        "filing.approve",
        detail.authorization_basis,
        key,
        draft_id=str(draft.id),
        approved_payload_digest=approval.approved_payload_digest,
        attestation_version=approval.attestation_version,
    )
    record_case_audit(
        session, actor.tenant_id, case.id, ActorType.USER, "filing.approved", data, actor.label
    )
    append_event(
        session,
        actor.tenant_id,
        Topic.FILING_APPROVED,
        {
            "case_id": str(case.id),
            "draft_id": str(draft.id),
            "digest": approval.approved_payload_digest,
        },
        EventOrigin.USER_ACTION,
        make_idempotency_key("filing-approved", approval.id),
    )


def void_approval(
    session: Session, actor: Actor, case: Case, draft: FilingDraft, problems: ApprovalProblems
) -> None:
    """Approval no longer matches: send the draft and case back for re-review."""
    if draft.review_state == FilingDraftReviewState.APPROVED.value:
        draft.review_state = FilingDraftReviewState.NEEDS_REVIEW.value
    if case_state(case) in {E.USER_APPROVED, E.EXTERNAL_SUBMISSION_REQUIRED}:
        advance_case(session, case, E.DRAFT_READY, actor.user_id, reason="approval void")
    record_case_audit(
        session,
        actor.tenant_id,
        case.id,
        ActorType.USER,
        "filing.approval_voided",
        {"draft_id": str(draft.id), "problems": problems.codes},
        actor.label,
    )
