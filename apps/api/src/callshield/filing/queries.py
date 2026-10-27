"""Tenant-scoped lookups shared by the filing services. Every query filters tenant_id."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import case
from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_get_or_404, scoped_select
from callshield.core.errors import NotFoundError
from callshield.db.models.enforce import FilingDraft, FilingSubmission, SubmissionApproval
from callshield.db.models.filing_ext import (
    FilingApprovalDetail,
    FilingDraftMeta,
    FilingReceipt,
    FilingSubmissionExt,
)


def get_draft_with_meta(
    session: Session, tenant_id: uuid.UUID, draft_id: uuid.UUID
) -> tuple[FilingDraft, FilingDraftMeta]:
    draft = scoped_get_or_404(session, FilingDraft, draft_id, tenant_id)
    meta = session.execute(
        scoped_select(FilingDraftMeta, tenant_id).where(FilingDraftMeta.draft_id == draft.id)
    ).scalar_one_or_none()
    if meta is None:
        raise NotFoundError("FilingDraft not found", code="NOT_FOUND")
    return draft, meta


def get_submission_with_ext(
    session: Session, tenant_id: uuid.UUID, submission_id: uuid.UUID
) -> tuple[FilingSubmission, FilingSubmissionExt]:
    sub = scoped_get_or_404(session, FilingSubmission, submission_id, tenant_id)
    ext = session.execute(
        scoped_select(FilingSubmissionExt, tenant_id).where(
            FilingSubmissionExt.submission_id == sub.id
        )
    ).scalar_one_or_none()
    if ext is None:
        raise NotFoundError("FilingSubmission not found", code="NOT_FOUND")
    return sub, ext


def latest_in_lineage(
    session: Session,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    destination_id: uuid.UUID,
    kind: str,
    supplements_submission_id: uuid.UUID | None = None,
) -> tuple[FilingDraft, FilingDraftMeta] | None:
    parent = (
        FilingDraftMeta.supplements_submission_id.is_(None)
        if supplements_submission_id is None
        else FilingDraftMeta.supplements_submission_id == supplements_submission_id
    )
    stmt = (
        scoped_select(FilingDraft, tenant_id)
        .join(FilingDraftMeta, FilingDraftMeta.draft_id == FilingDraft.id)
        .where(
            FilingDraftMeta.case_id == case_id,
            FilingDraftMeta.destination_id == destination_id,
            FilingDraftMeta.kind == kind,
            parent,
        )
        .order_by(FilingDraft.revision.desc(), FilingDraft.created_at.desc())
        .limit(1)
    )
    draft = session.execute(stmt).scalar_one_or_none()
    if draft is None:
        return None
    return get_draft_with_meta(session, tenant_id, draft.id)


def draft_meta_by_key(session: Session, tenant_id: uuid.UUID, key: str) -> FilingDraftMeta | None:
    stmt = scoped_select(FilingDraftMeta, tenant_id).where(FilingDraftMeta.idempotency_key == key)
    return session.execute(stmt).scalar_one_or_none()


def submission_for_draft(
    session: Session, tenant_id: uuid.UUID, draft_id: uuid.UUID
) -> Sequence[FilingSubmission]:
    stmt = (
        scoped_select(FilingSubmission, tenant_id)
        .where(FilingSubmission.draft_id == draft_id)
        .order_by(FilingSubmission.created_at)
    )
    return session.execute(stmt).scalars().all()


def submission_for_approval(
    session: Session, tenant_id: uuid.UUID, approval_id: uuid.UUID
) -> FilingSubmission | None:
    stmt = scoped_select(FilingSubmission, tenant_id).where(
        FilingSubmission.approval_id == approval_id
    )
    return session.execute(stmt).scalars().first()


def active_ext_by_dedupe(
    session: Session, tenant_id: uuid.UUID, dedupe_key: str
) -> FilingSubmissionExt | None:
    stmt = scoped_select(FilingSubmissionExt, tenant_id).where(
        FilingSubmissionExt.dedupe_key == dedupe_key
    )
    return session.execute(stmt).scalar_one_or_none()


def ext_for_submission_ids(
    session: Session, tenant_id: uuid.UUID, submission_ids: Sequence[uuid.UUID]
) -> Sequence[FilingSubmissionExt]:
    if not submission_ids:
        return []
    stmt = scoped_select(FilingSubmissionExt, tenant_id).where(
        FilingSubmissionExt.submission_id.in_(list(submission_ids))
    )
    return session.execute(stmt).scalars().all()


def latest_approval(
    session: Session, tenant_id: uuid.UUID, draft_id: uuid.UUID
) -> tuple[SubmissionApproval, FilingApprovalDetail] | None:
    stmt = (
        scoped_select(SubmissionApproval, tenant_id)
        .where(SubmissionApproval.draft_id == draft_id)
        .order_by(SubmissionApproval.authorized_at.desc(), SubmissionApproval.id)
        .limit(1)
    )
    approval = session.execute(stmt).scalar_one_or_none()
    if approval is None:
        return None
    detail = session.execute(
        scoped_select(FilingApprovalDetail, tenant_id).where(
            FilingApprovalDetail.approval_id == approval.id
        )
    ).scalar_one_or_none()
    if detail is None:
        return None
    return approval, detail


def approval_by_key(
    session: Session, tenant_id: uuid.UUID, key: str
) -> FilingApprovalDetail | None:
    stmt = scoped_select(FilingApprovalDetail, tenant_id).where(
        FilingApprovalDetail.idempotency_key == key
    )
    return session.execute(stmt).scalar_one_or_none()


def receipts_for(
    session: Session, tenant_id: uuid.UUID, submission_id: uuid.UUID
) -> Sequence[FilingReceipt]:
    stmt = (
        scoped_select(FilingReceipt, tenant_id)
        .where(FilingReceipt.submission_id == submission_id)
        .order_by(
            FilingReceipt.created_at,
            # transport acknowledgment always precedes agency confirmation on timestamp ties
            case((FilingReceipt.receipt_kind == "TRANSPORT_ACK", 0), else_=1),
            FilingReceipt.id,
        )
    )
    return session.execute(stmt).scalars().all()
