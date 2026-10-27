"""Submit pipeline (PRD 19.7 / 19.8, gates 19.14).

Invariants enforced here, each covered by failure-injection tests:
  * nothing is transmitted without a valid approval of the exact payload digest;
  * the payload handed to the adapter is the approved payload, byte for byte;
  * SUBMITTED only follows a real transport acknowledgment with a reference;
  * AGENCY_ACKNOWLEDGED only follows a confirmation distinct from the transport ack;
  * timeouts / garbage / exceptions become UNKNOWN and are NEVER retried (status lookup only);
  * destinations without a registered documented_api adapter return 422 UNSUPPORTED_DESTINATION
    plus the official manual path and a copyable prepared submission.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from callshield.core.clock import utcnow
from callshield.core.config import Settings
from callshield.core.errors import ConflictError, PolicyBlockedError, UnprocessableError
from callshield.core.idempotency import make_idempotency_key
from callshield.db.models.enforce import Case, FilingDraft, FilingSubmission, SubmissionApproval
from callshield.db.models.filing_ext import (
    FilingApprovalDetail,
    FilingDraftMeta,
    FilingSubmissionExt,
)
from callshield.domain.enums import (
    AdapterMethod,
    EventOrigin,
    EvidenceLabel,
)
from callshield.domain.enums import (
    EnforceCaseState as E,
)
from callshield.domain.enums import (
    SubmissionState as S,
)
from callshield.domain.topics import Topic
from callshield.filing import queries
from callshield.filing.adapters import (
    ApprovedSubmission,
    FilingStatusState,
    RegisteredAdapter,
    SubmissionReceipt,
    TransportOutcome,
    documented_adapter_for,
)
from callshield.filing.approvals import approval_problems, void_approval
from callshield.filing.common import Actor
from callshield.filing.destinations import DestinationView, get_destination_by_id
from callshield.filing.handoff import draft_view, manual_path, prepared_for
from callshield.filing.lifecycle import advance_case, case_state
from callshield.filing.submission import (
    add_receipt,
    audit_submission,
    create_submission,
    move_submission,
    sanitize_response,
)
from callshield.ledger.service import append_event


class TransportRejectedError(Exception):
    """Adapters raise this only for a definitive, unambiguous rejection (nothing received)."""


@dataclass(frozen=True)
class SubmitResult:
    submission: FilingSubmission
    ext: FilingSubmissionExt
    created: bool


def _approved(
    session: Session, actor: Actor, case: Case, draft: FilingDraft, meta: FilingDraftMeta
) -> tuple[SubmissionApproval, FilingApprovalDetail]:
    """Latest approval, verified against the draft and evidence as they are right now."""
    found = queries.latest_approval(session, actor.tenant_id, draft.id)
    if found is None:
        raise ConflictError(
            "an approval of the exact payload digest is required", code="NOT_APPROVED"
        )
    problems = approval_problems(session, case, draft, meta, found[1], found[0])
    if problems:
        void_approval(session, actor, case, draft, problems)
        session.commit()  # persist the voiding before reporting the refusal
        raise ConflictError(
            "approval is no longer valid; review and approve again",
            code="APPROVAL_INVALID",
            details={"problems": problems.codes},
        )
    return found


def _open_manual_submission(
    session: Session,
    actor: Actor,
    case: Case,
    draft: FilingDraft,
    meta: FilingDraftMeta,
    dest: DestinationView,
    approval: SubmissionApproval,
    client_key: str | None,
) -> FilingSubmission:
    """Record that the user must file by hand (EXTERNAL_SUBMISSION_REQUIRED). Sends nothing."""
    sub, ext, _ = create_submission(
        session, actor, case, draft, meta, approval, f"manual_handoff:{dest.slug}",
        S.EXTERNAL_SUBMISSION_REQUIRED.value, "filing.submit", client_key,
    )  # fmt: skip
    if case_state(case) is E.USER_APPROVED:
        advance_case(
            session, case, E.EXTERNAL_SUBMISSION_REQUIRED, actor.user_id,
            reason="no supported documented API",
        )  # fmt: skip
    audit_submission(session, actor, case, "filing.unsupported_destination", ext, sub)
    return sub


def _unsupported(
    session: Session,
    actor: Actor,
    case: Case,
    draft: FilingDraft,
    meta: FilingDraftMeta,
    dest: DestinationView,
    client_key: str | None,
) -> UnprocessableError:
    """Block transmission; record the manual path (state EXTERNAL_SUBMISSION_REQUIRED)."""
    view = draft_view(draft, meta)
    details: dict[str, Any] = {
        "destination": dest.slug,
        "manual_handoff": manual_path(dest),
        "prepared_submission": prepared_for(dest, view),
        "approval_required_before_filing": True,
    }
    found = queries.latest_approval(session, actor.tenant_id, draft.id)
    if found is not None and draft.review_state == "APPROVED":
        if approval_problems(session, case, draft, meta, found[1], found[0]):
            details["approval_status"] = "VOID"
        else:
            sub = _open_manual_submission(
                session, actor, case, draft, meta, dest, found[0], client_key
            )
            details["filing_id"] = str(sub.id)
            details["approval_status"] = "VALID"
    session.commit()  # keep the EXTERNAL_SUBMISSION_REQUIRED state; the response is still 422
    return UnprocessableError(
        "no supported electronic submission exists for this destination; file through the "
        "official manual path",
        code="UNSUPPORTED_DESTINATION",
        details=details,
    )


def _transmit(registered: RegisteredAdapter, approved: ApprovedSubmission) -> SubmissionReceipt:
    """Call the adapter exactly once. Anything but a clean answer is AMBIGUOUS, not FAILED."""
    try:
        receipt = registered.adapter.submit(approved)
    except TransportRejectedError as exc:
        return SubmissionReceipt(
            TransportOutcome.REJECTED, failure_code=str(exc)[:100] or "REJECTED"
        )
    except Exception as exc:  # any other error may already have reached the agency
        return SubmissionReceipt(TransportOutcome.AMBIGUOUS, failure_code=type(exc).__name__)
    if not isinstance(receipt, SubmissionReceipt):
        return SubmissionReceipt(TransportOutcome.AMBIGUOUS, failure_code="UNPARSEABLE_RESPONSE")
    return receipt


def _case_ctx(registered: RegisteredAdapter, settings: Settings) -> dict[str, Any]:
    return {
        "adapter_method": AdapterMethod.DOCUMENTED_API.value,
        "scoped_credential": registered.scoped_credential_ref,
        "approval_valid": True,
        "submission_enabled": settings.filing_submission_enabled,
    }


def _apply_receipt(
    session: Session,
    actor: Actor,
    case: Case,
    meta: FilingDraftMeta,
    sub: FilingSubmission,
    ext: FilingSubmissionExt,
    receipt: SubmissionReceipt,
) -> None:
    """Translate the transport's answer into states. The only place SUBMITTED is set."""
    ref = (receipt.transport_ref or "").strip()
    if receipt.outcome is TransportOutcome.REJECTED:
        move_submission(sub, ext, S.FAILED.value, {})
        sub.failure_code = receipt.failure_code or "REJECTED"
        advance_case(session, case, E.SUBMISSION_FAILED, actor.user_id, reason="rejected")
        emit_event(session, actor, case, sub, Topic.FILING_FAILED)
    elif receipt.outcome is TransportOutcome.ACCEPTED and ref:
        _record_ack(session, actor, case, meta, sub, ext, receipt, ref)
    else:
        move_submission(sub, ext, S.UNKNOWN.value, {})  # ambiguous: lookup only, no resend
        sub.failure_code = receipt.failure_code or "NO_TRANSPORT_ACK"
    audit_submission(session, actor, case, f"filing.{sub.state.lower()}", ext, sub)


def _record_ack(
    session: Session,
    actor: Actor,
    case: Case,
    meta: FilingDraftMeta,
    sub: FilingSubmission,
    ext: FilingSubmissionExt,
    receipt: SubmissionReceipt,
    ref: str,
) -> None:
    ctx = {"transport_ack": True, "transport_ref": ref}
    move_submission(sub, ext, S.SUBMITTED.value, ctx)
    sub.transport_response_ref = ref
    add_receipt(
        session,
        actor,
        sub,
        meta,
        kind="TRANSPORT_ACK",
        label=EvidenceLabel.OBSERVED,
        confirmation_number=None,
        source="TRANSPORT",
        submitted_at=sub.sent_at,
        endpoint=receipt.endpoint,
        response=sanitize_response(receipt.response),
    )
    advance_case(session, case, E.SUBMITTED, actor.user_id, ctx, "transport acknowledged")
    emit_event(session, actor, case, sub, Topic.FILING_SUBMITTED)
    _record_agency_confirmation(session, actor, case, meta, sub, ext, receipt, ref)


def _record_agency_confirmation(
    session: Session,
    actor: Actor,
    case: Case,
    meta: FilingDraftMeta,
    sub: FilingSubmission,
    ext: FilingSubmissionExt,
    receipt: SubmissionReceipt,
    ref: str,
) -> None:
    confirmation = (receipt.agency_confirmation or "").strip()
    if not confirmation or confirmation == ref:
        return  # not distinguishable from the transport ack: stays SUBMITTED
    ctx = {"agency_confirmation_distinct": True, "agency_confirmation_ref": confirmation}
    move_submission(sub, ext, S.AGENCY_ACKNOWLEDGED.value, ctx)
    sub.agency_receipt = confirmation
    add_receipt(
        session,
        actor,
        sub,
        meta,
        kind="AGENCY_CONFIRMATION",
        label=EvidenceLabel.EXTERNALLY_VERIFIED,
        confirmation_number=confirmation,
        source="AGENCY",
        submitted_at=utcnow(),
        endpoint=receipt.endpoint,
        response=sanitize_response(receipt.response),
    )
    advance_case(session, case, E.AGENCY_ACKNOWLEDGED, actor.user_id, ctx, "agency confirmation")
    emit_event(session, actor, case, sub, Topic.FILING_ACKNOWLEDGED)


def emit_event(
    session: Session, actor: Actor, case: Case, sub: FilingSubmission, topic: Topic
) -> None:
    append_event(
        session,
        actor.tenant_id,
        topic,
        {"case_id": str(case.id), "filing_id": str(sub.id), "state": sub.state},
        EventOrigin.USER_ACTION,
        make_idempotency_key(topic.value, sub.id, sub.state),
    )


def _begin_send(
    session: Session,
    settings: Settings,
    actor: Actor,
    case: Case,
    sub: FilingSubmission,
    ext: FilingSubmissionExt,
    registered: RegisteredAdapter,
) -> None:
    """Make PENDING then IN_PROGRESS durable before anything leaves the building."""
    session.commit()
    advance_case(
        session, case, E.SUBMISSION_IN_PROGRESS, actor.user_id,
        _case_ctx(registered, settings), "sending",
    )  # fmt: skip
    move_submission(sub, ext, S.IN_PROGRESS.value, {"approval_valid": True})
    sub.sent_at = utcnow()
    session.commit()  # a crash after this point can never look unsent


def _registered_or_unsupported(
    session: Session,
    settings: Settings,
    actor: Actor,
    case: Case,
    draft: FilingDraft,
    meta: FilingDraftMeta,
    client_key: str | None,
) -> RegisteredAdapter:
    """Find the documented adapter or raise 422 UNSUPPORTED_DESTINATION / 403 when disabled."""
    dest = get_destination_by_id(session, meta.destination_id)
    if dest is None:
        raise ConflictError("destination missing from registry", code="DESTINATION_NOT_FOUND")
    documented = dest.method == AdapterMethod.DOCUMENTED_API.value and dest.active
    registered = documented_adapter_for(dest.slug) if documented else None
    if registered is None:
        raise _unsupported(session, actor, case, draft, meta, dest, client_key)
    if not settings.filing_submission_enabled:
        raise PolicyBlockedError(
            "filing submission is disabled in this deployment", code="FILING_SUBMISSION_DISABLED"
        )
    return registered


def submit_draft(
    session: Session,
    settings: Settings,
    actor: Actor,
    case: Case,
    draft: FilingDraft,
    meta: FilingDraftMeta,
    client_key: str | None,
) -> SubmitResult:
    registered = _registered_or_unsupported(session, settings, actor, case, draft, meta, client_key)
    approval, _detail = _approved(session, actor, case, draft, meta)
    sub, ext, created = create_submission(
        session,
        actor,
        case,
        draft,
        meta,
        approval,
        registered.adapter.id,
        S.PENDING.value,
        "filing.submit",
        client_key,
    )
    if not created:
        return SubmitResult(sub, ext, False)  # replay: never send twice for one approval
    _begin_send(session, settings, actor, case, sub, ext, registered)
    approved = ApprovedSubmission(
        draft_id=draft.id,
        approval_id=approval.id,
        payload=copy.deepcopy(draft.draft_json["payload"]),
        payload_digest=approval.approved_payload_digest,
        idempotency_key=sub.idempotency_key,
        scoped_credential_ref=registered.scoped_credential_ref,
    )
    receipt = _transmit(registered, approved)
    try:
        _apply_receipt(session, actor, case, meta, sub, ext, receipt)
        session.commit()
    except Exception:
        # The call already happened. Never leave it looking unsent or falsely successful.
        session.rollback()
        _mark_unknown_after_local_error(session, sub.id, actor)
    return SubmitResult(sub, ext, True)


def _mark_unknown_after_local_error(session: Session, sub_id: uuid.UUID, actor: Actor) -> None:
    sub, ext = queries.get_submission_with_ext(session, actor.tenant_id, sub_id)
    if sub.state == S.IN_PROGRESS.value:
        move_submission(sub, ext, S.UNKNOWN.value, {})
        sub.failure_code = "LOCAL_PROCESSING_ERROR_AFTER_SEND"
    session.commit()


def refresh_status(
    session: Session,
    actor: Actor,
    case: Case,
    sub: FilingSubmission,
    ext: FilingSubmissionExt,
    meta: FilingDraftMeta,
) -> FilingSubmission:
    """Ask the agency's documented status interface. Never re-sends (PRD 19.7)."""
    registered = documented_adapter_for(ext.destination_slug)
    if registered is None or sub.state not in {S.UNKNOWN.value, S.SUBMITTED.value}:
        raise ConflictError("no status lookup applies to this filing", code="STATUS_NOT_APPLICABLE")
    status = registered.adapter.lookup_status(sub.idempotency_key, ext.payload_digest)
    ext.status_checks += 1
    ref = (status.transport_ref or "").strip()
    receipt = SubmissionReceipt(
        TransportOutcome.ACCEPTED
        if status.state in (FilingStatusState.RECEIVED, FilingStatusState.ACKNOWLEDGED)
        else TransportOutcome.AMBIGUOUS,
        transport_ref=ref or None,
        agency_confirmation=status.agency_confirmation
        if status.state is FilingStatusState.ACKNOWLEDGED
        else None,
        response=status.response,
    )
    if sub.state == S.UNKNOWN.value and receipt.outcome is TransportOutcome.ACCEPTED and ref:
        sub.failure_code = None
        _record_ack(session, actor, case, meta, sub, ext, receipt, ref)
    elif sub.state == S.SUBMITTED.value and receipt.agency_confirmation:
        _record_agency_confirmation(
            session, actor, case, meta, sub, ext, receipt, sub.transport_response_ref or ""
        )
    audit_submission(session, actor, case, "filing.status_checked", ext, sub)
    session.flush()
    return sub
