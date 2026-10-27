"""Submission rows: creation with dedupe (ENF-018), guarded state moves, receipts (ENF-017)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from callshield.audit.service import record_case_audit
from callshield.core.clock import utcnow
from callshield.core.errors import ConflictError
from callshield.core.idempotency import make_idempotency_key
from callshield.core.ids import new_id
from callshield.core.redaction import redact_structure, redact_text
from callshield.db.models.enforce import Case, FilingDraft, FilingSubmission, SubmissionApproval
from callshield.db.models.filing_ext import FilingDraftMeta, FilingReceipt, FilingSubmissionExt
from callshield.domain.enums import ActorType, EvidenceLabel
from callshield.filing import queries
from callshield.filing.common import Actor, command_audit_data
from callshield.filing.states import ACTIVE_STATES, submission_transition_allowed

MAX_RESPONSE_CHARS = 2000


def dedupe_key_for(case: Case, agency: str, payload_digest: str) -> str:
    """One active submission per case + agency + payload digest."""
    return f"{case.id}:{agency}:{payload_digest}"


def check_duplicate(
    session: Session, actor: Actor, case: Case, draft: FilingDraft, meta: FilingDraftMeta
) -> None:
    """Block a second active submission of the same content (different draft rows)."""
    key = dedupe_key_for(case, draft.agency, meta.payload_digest)
    other = queries.active_ext_by_dedupe(session, actor.tenant_id, key)
    if other is not None and other.submission_id not in {
        s.id for s in queries.submission_for_draft(session, actor.tenant_id, draft.id)
    }:
        raise ConflictError(
            "an active submission of identical content already exists for this case",
            code="DUPLICATE_SUBMISSION",
            details={"filing_id": str(other.submission_id)},
        )


def _extension_row(
    actor: Actor,
    case: Case,
    draft: FilingDraft,
    meta: FilingDraftMeta,
    sub: FilingSubmission,
    scope: str,
    client_key: str | None,
) -> FilingSubmissionExt:
    return FilingSubmissionExt(
        id=new_id(),
        tenant_id=actor.tenant_id,
        submission_id=sub.id,
        case_id=case.id,
        approval_id=sub.approval_id,
        agency=draft.agency,
        destination_slug=meta.destination_slug,
        payload_digest=meta.payload_digest,
        dedupe_key=dedupe_key_for(case, draft.agency, meta.payload_digest),
        kind=meta.kind,
        supplements_submission_id=meta.supplements_submission_id,
        actor=actor.label,
        scope=scope,
        authorization_basis="per-submission approval of the exact payload digest",
        client_idempotency_key=client_key,
    )


def create_submission(
    session: Session,
    actor: Actor,
    case: Case,
    draft: FilingDraft,
    meta: FilingDraftMeta,
    approval: SubmissionApproval,
    adapter_id: str,
    state: str,
    scope: str,
    client_key: str | None,
) -> tuple[FilingSubmission, FilingSubmissionExt, bool]:
    """Insert the submission (+ext). Unique approval/idempotency/dedupe make replays safe."""
    existing = queries.submission_for_approval(session, actor.tenant_id, approval.id)
    if existing is not None:
        return (
            existing,
            queries.get_submission_with_ext(session, actor.tenant_id, existing.id)[1],
            False,
        )
    check_duplicate(session, actor, case, draft, meta)
    sub = FilingSubmission(
        id=new_id(),
        tenant_id=actor.tenant_id,
        draft_id=draft.id,
        approval_id=approval.id,
        adapter=adapter_id,
        idempotency_key=make_idempotency_key("filing", actor.tenant_id, approval.id),
        state=state,
    )
    ext = _extension_row(actor, case, draft, meta, sub, scope, client_key)
    try:
        with session.begin_nested():
            session.add(sub)
            session.flush()
            session.add(ext)
            session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "a submission for this approval or content already exists",
            code="DUPLICATE_SUBMISSION",
        ) from exc
    return sub, ext, True


def move_submission(
    sub: FilingSubmission, ext: FilingSubmissionExt, to_state: str, ctx: Mapping[str, Any]
) -> None:
    """Change state only through the submission state machine; fails closed."""
    decision = submission_transition_allowed(sub.state, to_state, ctx)
    if not decision.ok:
        raise ConflictError(
            f"submission cannot move {sub.state} -> {to_state}: {decision.reason}",
            code="INVALID_SUBMISSION_TRANSITION",
        )
    sub.state = to_state
    if to_state == "UNKNOWN":
        ext.unknown_since = utcnow()
    if to_state not in ACTIVE_STATES:
        ext.dedupe_key = None  # a failed attempt no longer blocks a fresh approved one


def sanitize_response(response: Mapping[str, Any]) -> dict[str, Any]:
    """Receipts keep only a sanitized response: redacted, size-limited (ENF-017)."""
    cleaned = redact_structure(dict(response))
    text = str(cleaned)
    if len(text) > MAX_RESPONSE_CHARS:
        return {"truncated": True, "preview": text[:MAX_RESPONSE_CHARS]}
    return dict(cleaned)


def sanitize_text(text: str) -> str:
    return redact_text(text)[:MAX_RESPONSE_CHARS]


def add_receipt(
    session: Session,
    actor: Actor,
    sub: FilingSubmission,
    meta: FilingDraftMeta,
    *,
    kind: str,
    label: EvidenceLabel,
    confirmation_number: str | None,
    source: str,
    submitted_at: datetime | None,
    endpoint: str,
    response: dict[str, Any],
) -> FilingReceipt:
    receipt = FilingReceipt(
        id=new_id(),
        tenant_id=actor.tenant_id,
        submission_id=sub.id,
        receipt_kind=kind,
        evidence_label=label.value,
        confirmation_number=confirmation_number,
        confirmation_source=source,
        submitted_at=submitted_at,
        endpoint=endpoint[:1000],
        sanitized_response=response,
        payload_digest=meta.payload_digest,
        created_at=utcnow(),
    )
    session.add(receipt)
    session.flush()
    return receipt


def audit_submission(
    session: Session,
    actor: Actor,
    case: Case,
    action: str,
    ext: FilingSubmissionExt,
    sub: FilingSubmission,
    **extra: object,
) -> None:
    data = command_audit_data(
        ext.scope,
        ext.authorization_basis,
        ext.client_idempotency_key or sub.idempotency_key,
        filing_id=str(sub.id),
        state=sub.state,
        payload_digest=ext.payload_digest,
        **extra,
    )
    record_case_audit(session, actor.tenant_id, case.id, ActorType.USER, action, data, actor.label)
