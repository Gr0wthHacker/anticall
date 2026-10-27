"""API schemas for filings. No field exists for liability, guilt, or money."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from callshield.db.models.enforce import FilingDraft, FilingSubmission, SubmissionApproval
from callshield.db.models.filing_ext import (
    FilingApprovalDetail,
    FilingDraftMeta,
    FilingReceipt,
    FilingSubmissionExt,
)
from callshield.filing.adapters import registered_slugs
from callshield.filing.attestation import ATTESTATION_V1, ATTESTATION_VERSION
from callshield.filing.destinations import DestinationView
from callshield.filing.selection import ExportScope

RECEIPT_DISCLAIMER = (
    "Receipts are shown exactly as recorded. A user-attested entry is your own statement, "
    "not an agency acknowledgment. A receipt is not adjudication."
)


class DraftCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: str = Field(min_length=1, max_length=64)
    fields: dict[str, Any] = Field(default_factory=dict)
    scope: ExportScope = Field(default_factory=ExportScope)
    idempotency_key: str | None = Field(default=None, max_length=120)


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str | None = Field(default=None, max_length=120)


class DraftOut(BaseModel):
    draft_id: uuid.UUID
    case_id: uuid.UUID
    revision: int
    kind: str
    agency: str
    destination_slug: str
    template_version: str
    review_state: str
    payload: dict[str, Any]
    payload_digest: str
    sources: list[dict[str, Any]]
    exclusions: list[dict[str, Any]]
    completeness: dict[str, Any]
    evidence_snapshot_digest: str
    export_scope: dict[str, Any]
    supersedes_draft_id: uuid.UUID | None
    supplements_filing_id: uuid.UUID | None
    notices: list[str]
    attestation: dict[str, Any]
    prepared_submission: dict[str, Any] | None = None
    created_at: datetime
    created: bool = True


class ApprovalOut(BaseModel):
    approval_id: uuid.UUID
    draft_id: uuid.UUID
    approved_payload_digest: str
    attestation_version: str
    authorized_at: datetime
    evidence_snapshot_digest: str
    user_id: uuid.UUID
    created: bool = True


class ReceiptOut(BaseModel):
    receipt_id: uuid.UUID
    receipt_kind: str
    evidence_label: str
    confirmation_number: str | None
    confirmation_source: str
    is_agency_acknowledgment: bool
    submitted_at: datetime | None
    endpoint: str
    sanitized_response: dict[str, Any]
    payload_digest: str
    created_at: datetime


class FilingOut(BaseModel):
    filing_id: uuid.UUID
    draft_id: uuid.UUID
    case_id: uuid.UUID
    agency: str
    destination_slug: str
    adapter: str
    state: str
    kind: str
    payload_digest: str
    sent_at: datetime | None
    transport_response_ref: str | None
    failure_code: str | None
    supplements_filing_id: uuid.UUID | None
    receipts: list[ReceiptOut]
    receipt_disclaimer: str = RECEIPT_DISCLAIMER
    next_step: str
    created: bool = True


class DestinationOut(BaseModel):
    slug: str
    agency: str
    name: str
    method: str
    capability_status: str
    electronic_submission_supported: bool
    source_url: str
    capability_evidence: str
    last_reviewed_at: datetime | None
    validated: bool
    active: bool
    supported_issue_types: list[Any]
    supported_fields: list[Any]
    authorization_requirements: str
    attachment_constraints: dict[str, Any]
    retention: str
    sunset_procedure: str
    manual_handoff: dict[str, Any]
    notices: list[Any]


def destination_out(dest: DestinationView) -> DestinationOut:
    electronic = dest.method == "documented_api" and dest.slug in registered_slugs() and dest.active
    if not dest.validated:
        status = "NOT_VALIDATED"
    else:
        status = "DOCUMENTED_API" if electronic else "MANUAL_HANDOFF"
    return DestinationOut(
        slug=dest.slug,
        agency=dest.agency,
        name=dest.name,
        method=dest.method,
        capability_status=status,
        electronic_submission_supported=electronic,
        source_url=dest.source_url,
        capability_evidence=dest.capability_evidence,
        last_reviewed_at=dest.last_reviewed_at,
        validated=dest.validated,
        active=dest.active,
        supported_issue_types=dest.supported_issue_types,
        supported_fields=dest.supported_fields,
        authorization_requirements=dest.authorization_requirements,
        attachment_constraints=dest.attachment_constraints,
        retention=dest.retention,
        sunset_procedure=dest.sunset_procedure,
        manual_handoff=dest.manual_handoff,
        notices=dest.notices,
    )


def draft_out(
    draft: FilingDraft,
    meta: FilingDraftMeta,
    created: bool = True,
    prepared: dict[str, Any] | None = None,
) -> DraftOut:
    doc = draft.draft_json
    return DraftOut(
        draft_id=draft.id,
        case_id=draft.case_id,
        revision=draft.revision,
        kind=meta.kind,
        agency=draft.agency,
        destination_slug=meta.destination_slug,
        template_version=draft.template_version,
        review_state=draft.review_state,
        payload=doc["payload"],
        payload_digest=meta.payload_digest,
        sources=doc.get("sources", []),
        exclusions=doc.get("exclusions", []),
        completeness=doc.get("completeness", {}),
        evidence_snapshot_digest=draft.evidence_snapshot_digest,
        export_scope=doc.get("export_scope", {}),
        supersedes_draft_id=meta.supersedes_draft_id,
        supplements_filing_id=meta.supplements_submission_id,
        notices=doc.get("notices", []),
        attestation={
            "version": ATTESTATION_VERSION,
            "must_display_before_accept": ATTESTATION_V1["must_display_before_accept"],
            "statements": ATTESTATION_V1["statements"],
            "status": ATTESTATION_V1["status"],
            "notice": ATTESTATION_V1["notice"],
        },
        prepared_submission=prepared,
        created_at=draft.created_at,
        created=created,
    )


def approval_out(
    approval: SubmissionApproval, detail: FilingApprovalDetail, created: bool
) -> ApprovalOut:
    return ApprovalOut(
        approval_id=approval.id,
        draft_id=approval.draft_id,
        approved_payload_digest=approval.approved_payload_digest,
        attestation_version=approval.attestation_version,
        authorized_at=approval.authorized_at,
        evidence_snapshot_digest=detail.evidence_snapshot_digest,
        user_id=approval.user_id,
        created=created,
    )


def receipt_out(r: FilingReceipt) -> ReceiptOut:
    return ReceiptOut(
        receipt_id=r.id,
        receipt_kind=r.receipt_kind,
        evidence_label=r.evidence_label,
        confirmation_number=r.confirmation_number,
        confirmation_source=r.confirmation_source,
        is_agency_acknowledgment=r.receipt_kind == "AGENCY_CONFIRMATION",
        submitted_at=r.submitted_at,
        endpoint=r.endpoint,
        sanitized_response=r.sanitized_response,
        payload_digest=r.payload_digest,
        created_at=r.created_at,
    )


_NEXT_STEPS = {
    "EXTERNAL_SUBMISSION_REQUIRED": "File through the official site, then record that you did.",
    "SUBMITTED_EXTERNALLY": "You attested to filing externally; no agency receipt recorded.",
    "PENDING": "Prepared; not sent yet.",
    "IN_PROGRESS": "Sending. Do not file again.",
    "UNKNOWN": "The outcome is unknown. Check the status; do not re-submit.",
    "SUBMITTED": "Transport acknowledged. The receiving body has not confirmed.",
    "AGENCY_ACKNOWLEDGED": "The receiving body confirmed receipt. That is not a finding.",
    "FAILED": "Not accepted. You must review and approve again to retry.",
}


def filing_out(
    sub: FilingSubmission,
    ext: FilingSubmissionExt,
    case_id: uuid.UUID,
    receipts: list[FilingReceipt],
    created: bool = True,
) -> FilingOut:
    return FilingOut(
        filing_id=sub.id,
        draft_id=sub.draft_id,
        case_id=case_id,
        agency=ext.agency,
        destination_slug=ext.destination_slug,
        adapter=sub.adapter,
        state=sub.state,
        kind=ext.kind,
        payload_digest=ext.payload_digest,
        sent_at=sub.sent_at,
        transport_response_ref=sub.transport_response_ref,
        failure_code=sub.failure_code,
        supplements_filing_id=ext.supplements_submission_id,
        receipts=[receipt_out(r) for r in receipts],
        next_step=_NEXT_STEPS.get(sub.state, ""),
        created=created,
    )
