"""Draft generation (ENF-013, PRD 19.10): exact payload, source list, immutable revisions.

A draft is a proposal for the user to review. Generating or viewing one never changes a
case toward SUBMITTED. Payload digest = SHA-256 over canonical JSON of the payload.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from callshield.assessment.safety import assert_clean
from callshield.audit.service import record_case_audit
from callshield.core.errors import ConflictError, NotFoundError, UnprocessableError
from callshield.core.hashing import sha256_json
from callshield.core.idempotency import make_idempotency_key
from callshield.core.ids import new_id
from callshield.db.models.enforce import Case, FilingDraft, FilingSubmission
from callshield.db.models.filing_ext import FilingDraftMeta, FilingSubmissionExt
from callshield.domain.enums import (
    ActorType,
    EventOrigin,
    FilingDraftReviewState,
)
from callshield.domain.enums import (
    EnforceCaseState as E,
)
from callshield.domain.topics import Topic
from callshield.filing import queries
from callshield.filing.attestation import ATTESTATION_VERSION
from callshield.filing.common import Actor, command_audit_data
from callshield.filing.destinations import DestinationView, get_destination
from callshield.filing.handoff import manual_path
from callshield.filing.lifecycle import advance_path, case_state
from callshield.filing.selection import ExportScope, Selection, select_for_draft
from callshield.filing.snapshot import CaseSnapshot, load_case_snapshot
from callshield.filing.states import FILED_STATES
from callshield.filing.templates import (
    LIMITATIONS,
    TemplateSpec,
    build_complaint_text,
    check_completeness,
    get_template,
    validate_user_fields,
)
from callshield.ledger.service import append_event

ORIGINAL_STATES = frozenset(
    {
        E.ELIGIBLE_TO_DRAFT,
        E.DRAFT_READY,
        E.USER_APPROVED,
        E.EXTERNAL_SUBMISSION_REQUIRED,
        E.SUBMISSION_FAILED,
    }
)
SUPPLEMENT_STATES = frozenset(
    {
        E.SUBMITTED,
        E.AGENCY_ACKNOWLEDGED,
        E.MONITORING,
        E.SUPPLEMENT_PENDING,
        E.REVIEW_REQUIRED,
        E.ELIGIBLE_TO_DRAFT,
        E.DRAFT_READY,
    }
)
_REOPEN_TO_DRAFT = frozenset({E.USER_APPROVED, E.EXTERNAL_SUBMISSION_REQUIRED, E.SUBMISSION_FAILED})
_SUPPLEMENT_PATH = (
    E.MONITORING,
    E.SUPPLEMENT_PENDING,
    E.REVIEW_REQUIRED,
    E.ELIGIBLE_TO_DRAFT,
    E.DRAFT_READY,
)
_SUPPLEMENT_ENTRY = {
    E.SUBMITTED: 0,
    E.AGENCY_ACKNOWLEDGED: 0,
    E.MONITORING: 1,
    E.SUPPLEMENT_PENDING: 2,
    E.REVIEW_REQUIRED: 3,
    E.ELIGIBLE_TO_DRAFT: 4,
}


@dataclass(frozen=True)
class DraftResult:
    draft: FilingDraft
    meta: FilingDraftMeta
    created: bool


@dataclass(frozen=True)
class SupplementParent:
    submission: FilingSubmission
    ext: FilingSubmissionExt
    original_snapshot_digest: str
    original_payload_digest: str


def require_destination(session: Session, slug: str) -> tuple[DestinationView, TemplateSpec]:
    """A usable destination has a template, is validated and active; else 422 with the path."""
    dest = get_destination(session, slug)
    if dest is None:
        raise NotFoundError("destination not found", code="DESTINATION_NOT_FOUND")
    spec = get_template(dest.template_key)
    if not dest.validated or spec is None:
        raise UnprocessableError(
            "this destination is not validated; no draft is generated",
            code="DESTINATION_NOT_VALIDATED",
            details={"manual_handoff": manual_path(dest)},
        )
    if not dest.active:
        raise UnprocessableError(
            "this destination has been retired",
            code="UNSUPPORTED_DESTINATION",
            details={"manual_handoff": manual_path(dest)},
        )
    return dest, spec


def _check_case_state(case: Case, allowed: frozenset[E], kind: str) -> None:
    if case_state(case) not in allowed:
        raise ConflictError(
            f"case is not eligible for a {kind.lower()} filing draft in state {case.state}",
            code="CASE_NOT_ELIGIBLE",
            details={"state": case.state, "allowed": sorted(s.value for s in allowed)},
        )


def _recipient_and_channel(dest: DestinationView) -> dict[str, Any]:
    manual = dest.method == "manual_handoff"
    return {
        "recipient": {
            "agency": dest.agency,
            "destination": dest.name,
            "destination_slug": dest.slug,
            "official_url": dest.manual_handoff.get("official_url", dest.source_url),
        },
        "submission_channel": {
            "method": dest.method,
            "description": "Prepared for you to copy into the official site; CallShield does "
            "not transmit it."
            if manual
            else "Documented API adapter.",
        },
    }


def _call_rows(selection: Selection) -> list[dict[str, Any]]:
    return [
        {
            "incident_id": str(c.incident_id),
            "occurred_at": c.occurred_at.isoformat() if c.occurred_at else None,
            "displayed_number": c.displayed_caller_id,
            "evidence_label": c.evidence_label,
            "review_status": "CONFIRMED" if c.confirmed else "SUSPECTED",
        }
        for c in selection.calls
    ]


def _attachment_rows(selection: Selection) -> list[dict[str, Any]]:
    return [
        {
            "artifact_id": str(a.artifact_id),
            "sha256": a.sha256,
            "media_type": a.media_type,
            "evidence_label": a.evidence_label,
        }
        for a in selection.attachments
    ]


def build_payload(
    dest: DestinationView,
    spec: TemplateSpec,
    snapshot: CaseSnapshot,
    selection: Selection,
    fields: dict[str, str],
    supplement: dict[str, Any] | None,
) -> dict[str, Any]:
    line = snapshot.line.e164 if snapshot.line else ""
    payload: dict[str, Any] = {
        "template_version": spec.key,
        **_recipient_and_channel(dest),
        "complainant_line": line,
        "calls": _call_rows(selection),
        "claimed_caller_names": [
            {"name": n, "label": "CLAIMED", "verified": False}
            for n in sorted(set(selection.claimed_names))
        ],
        "opt_out_requests_documented": selection.opt_out_count,
        "fields": fields,
        "attachments": _attachment_rows(selection),
        "limitations": list(LIMITATIONS),
        "complaint_text": build_complaint_text(
            spec, line, selection.calls, selection.claimed_names, selection.opt_out_count, fields
        ),
    }
    if supplement:
        payload["supplement"] = supplement
    assert_clean(payload, "filing draft payload")
    return payload


def _sources(
    snapshot: CaseSnapshot, selection: Selection, fields: dict[str, str]
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = [
        {
            "kind": "incident",
            "id": str(c.incident_id),
            "evidence_label": c.evidence_label,
            "linkage_type": c.linkage_type,
        }
        for c in selection.calls
    ]
    sources += [{"kind": "ledger_event", "id": str(e)} for e in snapshot.ledger_event_ids]
    sources += [
        {"kind": "artifact", "id": str(a.artifact_id), "sha256": a.sha256}
        for a in selection.attachments
    ]
    sources.append({"kind": "user_input", "label": "USER_ATTESTED", "fields": sorted(fields)})
    return sources


def _supplement_block(parent: SupplementParent, snapshot: CaseSnapshot) -> dict[str, Any]:
    return {
        "of_filing_id": str(parent.submission.id),
        "original_payload_digest": parent.original_payload_digest,
        "note": "Supplemental information. The earlier filing is unchanged.",
        "incident_count_now": len(snapshot.incidents),
    }


def _persist(
    session: Session,
    actor: Actor,
    case: Case,
    dest: DestinationView,
    spec: TemplateSpec,
    document: dict[str, Any],
    previous: tuple[FilingDraft, FilingDraftMeta] | None,
    parent: SupplementParent | None,
    idempotency_key: str | None,
) -> tuple[FilingDraft, FilingDraftMeta]:
    draft = FilingDraft(
        id=new_id(),
        tenant_id=actor.tenant_id,
        case_id=case.id,
        agency=dest.agency,
        template_version=spec.key,
        revision=(previous[0].revision + 1) if previous else 1,
        draft_json=document,
        evidence_snapshot_digest=document["evidence_snapshot_digest"],
        review_state=(
            FilingDraftReviewState.NEEDS_REVIEW.value
            if document["completeness"]["complete"]
            else FilingDraftReviewState.DRAFT.value
        ),
    )
    meta = FilingDraftMeta(
        id=new_id(),
        tenant_id=actor.tenant_id,
        draft_id=draft.id,
        case_id=case.id,
        lineage_id=previous[1].lineage_id if previous else new_id(),
        destination_id=dest.id,
        destination_slug=dest.slug,
        kind="SUPPLEMENT" if parent else "ORIGINAL",
        supersedes_draft_id=previous[0].id if previous else None,
        supplements_submission_id=parent.submission.id if parent else None,
        payload_digest=document["payload_digest"],
        completeness=document["completeness"],
        created_by_user_id=actor.user_id,
        idempotency_key=idempotency_key,
        authorization_basis="authenticated account holder building a draft",
    )
    session.add_all([draft, meta])
    session.flush()
    return draft, meta


def _supersede(session: Session, previous: tuple[FilingDraft, FilingDraftMeta]) -> None:
    """Old revision stays byte-identical; only its review_state marks it superseded."""
    old_draft = previous[0]
    old_draft.review_state = FilingDraftReviewState.SUPERSEDED.value
    for sub in queries.submission_for_draft(session, old_draft.tenant_id, old_draft.id):
        ext = queries.get_submission_with_ext(session, old_draft.tenant_id, sub.id)[1]
        if sub.state == "EXTERNAL_SUBMISSION_REQUIRED":
            sub.state = "FAILED"
            sub.failure_code = "SUPERSEDED_BEFORE_FILING"
            ext.dedupe_key = None


def _guard_existing(
    session: Session, previous: tuple[FilingDraft, FilingDraftMeta] | None, digest: str, kind: str
) -> None:
    """ENF-018: never re-draft something already filed; edits after filing are supplements."""
    if previous is None:
        return
    for sub in queries.submission_for_draft(session, previous[0].tenant_id, previous[0].id):
        if sub.state in FILED_STATES or sub.state in {"IN_PROGRESS", "UNKNOWN", "PENDING"}:
            code = "DUPLICATE_FILING" if previous[1].payload_digest == digest else "ALREADY_FILED"
            raise ConflictError(
                "this draft was already filed or is in flight; use a supplement for new material"
                if code == "ALREADY_FILED"
                else "an identical filing already exists",
                code=code,
                details={"filing_id": str(sub.id), "state": sub.state, "kind": kind},
            )


def _advance_after_draft(
    session: Session, actor: Actor, case: Case, complete: bool, parent: SupplementParent | None
) -> None:
    state = case_state(case)
    if parent is not None:
        start = _SUPPLEMENT_ENTRY.get(state)
        if start is not None:
            path = list(_SUPPLEMENT_PATH[start:])
            steps = path if complete else path[:-1]
            advance_path(session, case, steps, actor.user_id, "supplement draft")
        return
    if state in _REOPEN_TO_DRAFT or (state is E.ELIGIBLE_TO_DRAFT and complete):
        advance_path(session, case, [E.DRAFT_READY], actor.user_id, "draft generated")


def _document(
    dest: DestinationView,
    snapshot: CaseSnapshot,
    selection: Selection,
    fields: dict[str, str],
    scope: ExportScope,
    payload: dict[str, Any],
    digest: str,
    completeness: dict[str, Any],
) -> dict[str, Any]:
    """Everything stored in filing_drafts.draft_json; only `payload` is hashed and sent."""
    return {
        "payload": payload,
        "payload_digest": digest,
        "sources": _sources(snapshot, selection, fields),
        "exclusions": selection.exclusions,
        "completeness": completeness,
        "export_scope": scope.model_dump(mode="json"),
        "evidence_snapshot_digest": snapshot.digest,
        "attestation_version": ATTESTATION_VERSION,
        "notices": list(dest.notices),
    }


def create_draft(
    session: Session,
    actor: Actor,
    case: Case,
    destination_slug: str,
    raw_fields: dict[str, Any],
    scope: ExportScope,
    idempotency_key: str | None,
    parent: SupplementParent | None = None,
) -> DraftResult:
    """Build (or re-use) a draft revision. Never transmits, never marks anything SUBMITTED."""
    key = make_idempotency_key("draft", case.id, idempotency_key) if idempotency_key else None
    if key and (hit := queries.draft_meta_by_key(session, actor.tenant_id, key)) is not None:
        draft, meta = queries.get_draft_with_meta(session, actor.tenant_id, hit.draft_id)
        return DraftResult(draft, meta, False)
    dest, spec = require_destination(session, destination_slug)
    kind = "SUPPLEMENT" if parent else "ORIGINAL"
    _check_case_state(case, SUPPLEMENT_STATES if parent else ORIGINAL_STATES, kind)
    fields = validate_user_fields(spec, raw_fields)
    snapshot = load_case_snapshot(session, case)
    selection = select_for_draft(snapshot, scope, dest.attachment_constraints)
    if parent is not None and snapshot.digest == parent.original_snapshot_digest:
        raise ConflictError(
            "no new evidence since the filing was approved; nothing to supplement",
            code="NO_NEW_EVIDENCE",
        )
    supplement = _supplement_block(parent, snapshot) if parent else None
    payload = build_payload(dest, spec, snapshot, selection, fields, supplement)
    digest = sha256_json(payload)
    previous = queries.latest_in_lineage(
        session, actor.tenant_id, case.id, dest.id, kind, parent.submission.id if parent else None
    )
    if previous and previous[1].payload_digest == digest:
        _guard_existing(session, previous, digest, kind)
        return DraftResult(previous[0], previous[1], False)
    _guard_existing(session, previous, digest, kind)
    completeness = check_completeness(spec, snapshot, selection.calls, fields)
    document = _document(dest, snapshot, selection, fields, scope, payload, digest, completeness)
    if previous:
        _supersede(session, previous)
    draft, meta = _persist(session, actor, case, dest, spec, document, previous, parent, key)
    _record(session, actor, case, draft, meta, key)
    _advance_after_draft(session, actor, case, completeness["complete"], parent)
    return DraftResult(draft, meta, True)


def _record(
    session: Session,
    actor: Actor,
    case: Case,
    draft: FilingDraft,
    meta: FilingDraftMeta,
    key: str | None,
) -> None:
    data = command_audit_data(
        "filing.draft",
        meta.authorization_basis,
        key,
        draft_id=str(draft.id),
        revision=draft.revision,
        payload_digest=meta.payload_digest,
        kind=meta.kind,
    )
    record_case_audit(
        session, actor.tenant_id, case.id, ActorType.USER, "filing.drafted", data, actor.label
    )
    append_event(
        session,
        actor.tenant_id,
        Topic.FILING_DRAFTED,
        {"case_id": str(case.id), "draft_id": str(draft.id), "payload_digest": meta.payload_digest},
        EventOrigin.USER_ACTION,
        make_idempotency_key("filing-drafted", draft.id),
    )


def draft_document(draft: FilingDraft) -> dict[str, Any]:
    doc: dict[str, Any] = draft.draft_json
    return doc


def payload_matches_digest(draft: FilingDraft, meta: FilingDraftMeta) -> bool:
    """Tamper check: stored payload must still hash to the recorded digest."""
    payload = draft.draft_json.get("payload", {})
    return bool(
        sha256_json(payload) == meta.payload_digest == draft.draft_json.get("payload_digest")
    )


def parent_for_supplement(session: Session, actor: Actor, filing_id: uuid.UUID) -> SupplementParent:
    sub, ext = queries.get_submission_with_ext(session, actor.tenant_id, filing_id)
    if sub.state not in FILED_STATES:
        raise ConflictError(
            "only a filed submission can be supplemented",
            code="NOT_FILED",
            details={"state": sub.state},
        )
    approval = queries.latest_approval(session, actor.tenant_id, sub.draft_id)
    draft, meta = queries.get_draft_with_meta(session, actor.tenant_id, sub.draft_id)
    snapshot_digest = (
        approval[1].evidence_snapshot_digest if approval else draft.evidence_snapshot_digest
    )
    return SupplementParent(sub, ext, snapshot_digest, meta.payload_digest)
