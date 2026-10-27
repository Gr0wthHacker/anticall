"""Automatic case-building pipeline (PRD 19.12) for job kind `incident.created`.

carrier_event -> (Defend persists the call observation) -> check_recording_processing_policy
-> create_or_match_candidate_incident -> attach_to_case_if_confident_else_queue_review
-> recompute_optout_timeline -> refresh_case_dossier_snapshot_metadata
-> alert_user_only_for_material_changes.

`recompute_ruleset_assessment` belongs to the assessment module and is not run here.
Every step is idempotent: re-running for the same incident creates no duplicate
case, link, artifact, review item or alert. No LLM is involved in any decision.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_get, scoped_get_or_404, scoped_select
from callshield.casebuilder.config import CaseBuilderConfig, config_for_tenant
from callshield.casebuilder.lifecycle import transition_case
from callshield.casebuilder.linking import append_link, current_links, effective_label
from callshield.casebuilder.matching import MatchResult, claimed_entity_for, match_incident
from callshield.casebuilder.policy import RecordingPolicy, check_recording_policy
from callshield.casebuilder.recompute import recompute_case
from callshield.casebuilder.service import open_case
from callshield.core.clock import utcnow
from callshield.core.config import Settings, get_settings
from callshield.core.phone import mask_phone
from callshield.db.models.defend import CallSession, Incident
from callshield.db.models.enforce import Case, CaseIncident, EvidenceArtifact
from callshield.db.models.enforce_ext import CaseReviewItem
from callshield.domain.enums import (
    AuthorizationState,
    EnforceCaseState,
    EventOrigin,
    EvidenceLabel,
    EvidenceOrigin,
    EvidenceType,
    LinkageType,
    RecordingAuthorization,
)
from callshield.evidence.ingest import ArtifactInput, ingest_artifact
from callshield.evidence.store import ArtifactStore, get_artifact_store

SYSTEM = "system"
_RANK = {
    RecordingAuthorization.DENIED.value: 3,
    RecordingAuthorization.NOT_EVALUATED.value: 2,
    RecordingAuthorization.PERMITTED.value: 1,
}


@dataclass
class PipelineResult:
    incident_id: uuid.UUID
    action: str
    case_id: uuid.UUID | None = None
    review_item_id: uuid.UUID | None = None
    case_created: bool = False
    recording_authorization: str = RecordingAuthorization.NOT_EVALUATED.value
    audio_persisted: bool = False
    snapshot_changed: bool = False
    alerted: bool = False


def _existing_link_state(
    session: Session, tenant_id: uuid.UUID, incident_id: uuid.UUID
) -> tuple[uuid.UUID | None, bool]:
    """(case currently linked to the incident, whether any link row exists at all)."""
    rows = list(
        session.execute(
            scoped_select(CaseIncident, tenant_id).where(CaseIncident.incident_id == incident_id)
        ).scalars()
    )
    cases = {r.case_id for r in rows}
    for case_id in sorted(cases, key=str):
        row = current_links(session, tenant_id, case_id).get(incident_id)
        if row is not None and row.linkage_type in (
            LinkageType.AUTO_MATCH.value,
            LinkageType.USER_LINK.value,
            LinkageType.REVIEWER_LINK.value,
        ):
            return case_id, True
    return None, bool(rows)


def _queue_review(
    session: Session, tenant_id: uuid.UUID, incident: Incident, match: MatchResult, version: str
) -> CaseReviewItem:
    existing = (
        session.execute(
            scoped_select(CaseReviewItem, tenant_id).where(
                CaseReviewItem.incident_id == incident.id, CaseReviewItem.status == "OPEN"
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing
    item = CaseReviewItem(
        tenant_id=tenant_id,
        incident_id=incident.id,
        reason=match.reason,
        candidate_case_ids=[str(c) for c in match.candidate_case_ids],
        match_ruleset_version=version,
    )
    session.add(item)
    session.flush()
    return item


def resolve_review_items(
    session: Session, tenant_id: uuid.UUID, incident_id: uuid.UUID, resolution: str
) -> int:
    """Close open review items once a human has linked/unlinked the incident."""
    items = (
        session.execute(
            scoped_select(CaseReviewItem, tenant_id).where(
                CaseReviewItem.incident_id == incident_id, CaseReviewItem.status == "OPEN"
            )
        )
        .scalars()
        .all()
    )
    for item in items:
        item.status = "RESOLVED"
        item.resolution = resolution
        item.resolved_at = utcnow()
    return len(items)


def _merge_authorization(current: str, new: str, first_incident: bool) -> str:
    if first_incident:
        return new
    return max(current, new, key=lambda v: _RANK[v])


def _signaling_metadata(incident: Incident, call: CallSession | None) -> dict[str, object]:
    def iso(value: datetime | None) -> str | None:
        return value.astimezone(UTC).isoformat() if value else None

    return {
        "incident_id": str(incident.id),
        "call_session_id": str(call.id) if call else None,
        "provider": call.provider if call else None,
        "provider_call_id": call.provider_call_id if call else None,
        "direction": call.direction if call else None,
        "started_at": iso(call.started_at if call else None),
        "ended_at": iso(call.ended_at if call else None),
        "duration_seconds": call.duration_seconds if call else None,
        "disposition": call.disposition if call else None,
        "displayed_caller_id": incident.displayed_caller_id,
        "displayed_caller_id_note": "observed number; not a verified calling party",
    }


def persist_signaling_metadata(
    session: Session,
    store: ArtifactStore,
    settings: Settings,
    tenant_id: uuid.UUID,
    case: Case,
    incident: Incident,
    call: CallSession | None,
    policy: RecordingPolicy,
) -> EvidenceArtifact | None:
    """ENF-008: keep lawful signaling metadata even without any speech capture."""
    existing = (
        session.execute(
            scoped_select(EvidenceArtifact, tenant_id).where(
                EvidenceArtifact.incident_id == incident.id,
                EvidenceArtifact.source_type == EvidenceType.SIGNALING_METADATA.value,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return None
    body = json.dumps(_signaling_metadata(incident, call), sort_keys=True).encode("utf-8")
    return ingest_artifact(
        session,
        store,
        settings,
        tenant_id,
        ArtifactInput(
            data=body,
            source_type=EvidenceType.SIGNALING_METADATA,
            origin=EvidenceOrigin.ORIGINAL,
            evidence_label=EvidenceLabel.OBSERVED,
            media_type="application/json",
            collection_method="provider_webhook",
            processing_authorization=AuthorizationState.PERMITTED,
            provenance={
                "source": call.provider if call else "unknown",
                "audio_persisted": False,
                "recording_policy": policy.reason,
            },
            case_id=case.id,
            incident_id=incident.id,
            consent_id=policy.consent_id,
        ),
        SYSTEM,
    )


def persist_call_audio(
    session: Session,
    store: ArtifactStore,
    settings: Settings,
    tenant_id: uuid.UUID,
    incident_id: uuid.UUID,
    audio: bytes,
    media_type: str,
) -> EvidenceArtifact | None:
    """Persist call audio only when consent permits; otherwise store nothing (returns None)."""
    incident = scoped_get_or_404(session, Incident, incident_id, tenant_id)
    policy = check_recording_policy(session, tenant_id, incident.line_id, incident.call_session_id)
    if not policy.persist_audio:
        return None
    case_id, _ = _existing_link_state(session, tenant_id, incident.id)
    return ingest_artifact(
        session,
        store,
        settings,
        tenant_id,
        ArtifactInput(
            data=audio,
            source_type=EvidenceType.AUDIO_RECORDING,
            origin=EvidenceOrigin.ORIGINAL,
            evidence_label=EvidenceLabel.OBSERVED,
            media_type=media_type,
            collection_method="provider_recording",
            processing_authorization=AuthorizationState.PERMITTED,
            provenance={"recording_policy": policy.reason},
            case_id=case_id,
            incident_id=incident.id,
            consent_id=policy.consent_id,
        ),
        SYSTEM,
    )


def _case_title(incident: Incident, entity: str) -> str:
    who = entity or mask_phone(incident.displayed_caller_id)
    return f"Unwanted calls: {who}"


def _attach(
    session: Session,
    tenant_id: uuid.UUID,
    incident: Incident,
    match: MatchResult,
    entity: str,
    config: CaseBuilderConfig,
) -> tuple[Case, bool]:
    """Return (case, created). Opens a CANDIDATE case when the auto-open rule applies."""
    if match.case_id is not None:
        return scoped_get_or_404(session, Case, match.case_id, tenant_id), False
    assert incident.line_id is not None
    case, created = open_case(
        session,
        tenant_id,
        incident.line_id,
        _case_title(incident, entity),
        actor=SYSTEM,
        idempotency_key=f"auto:{config.match_ruleset_version}:{incident.id}",
        origin=EventOrigin.WORKER,
    )
    return case, created


def run_incident_pipeline(
    session: Session,
    tenant_id: uuid.UUID,
    incident_id: uuid.UUID,
    *,
    store: ArtifactStore | None = None,
    settings: Settings | None = None,
    config: CaseBuilderConfig | None = None,
) -> PipelineResult:
    """Run the 19.12 pipeline for one incident. Caller owns the commit."""
    config = config or config_for_tenant(tenant_id)
    settings = settings or get_settings()
    store = store or get_artifact_store()
    incident = scoped_get_or_404(session, Incident, incident_id, tenant_id)
    if incident.line_id is None:
        return PipelineResult(incident.id, "SKIPPED_NO_LINE")
    call = (
        scoped_get(session, CallSession, incident.call_session_id, tenant_id)
        if (incident.call_session_id)
        else None
    )
    policy = check_recording_policy(session, tenant_id, incident.line_id, incident.call_session_id)
    result = PipelineResult(
        incident.id, "NO_CASE", recording_authorization=policy.authorization.value
    )
    case = _resolve_case(session, tenant_id, incident, config, result)
    if case is None:
        return result
    _finish_case(session, tenant_id, case, incident, call, policy, store, settings, config, result)
    return result


def _resolve_case(
    session: Session,
    tenant_id: uuid.UUID,
    incident: Incident,
    config: CaseBuilderConfig,
    result: PipelineResult,
) -> Case | None:
    linked_case_id, had_rows = _existing_link_state(session, tenant_id, incident.id)
    if linked_case_id is not None:
        result.action, result.case_id = "ALREADY_LINKED", linked_case_id
        return scoped_get_or_404(session, Case, linked_case_id, tenant_id)
    if had_rows:
        result.action = "UNLINK_RESPECTED"  # a human severed the link; never re-link
        return None
    if effective_label(session, tenant_id, incident) not in config.unwanted_labels:
        return None
    entity = claimed_entity_for(session, tenant_id, incident.call_session_id)
    match = match_incident(session, tenant_id, incident, entity, config)
    if match.kind == "REVIEW":
        item = _queue_review(session, tenant_id, incident, match, config.match_ruleset_version)
        result.action, result.review_item_id = "REVIEW_QUEUED", item.id
        return None
    if match.kind == "NONE" and not config.auto_open_case_on_first_unwanted_call:
        return None
    case, created = _attach(session, tenant_id, incident, match, entity, config)
    basis = {**match.basis, "rule": match.reason, "new_case": created}
    append_link(session, tenant_id, case, incident, LinkageType.AUTO_MATCH, basis, SYSTEM)
    result.action = "CASE_OPENED" if created else "LINKED"
    result.case_id, result.case_created = case.id, created
    return case


def _finish_case(
    session: Session,
    tenant_id: uuid.UUID,
    case: Case,
    incident: Incident,
    call: CallSession | None,
    policy: RecordingPolicy,
    store: ArtifactStore,
    settings: Settings,
    config: CaseBuilderConfig,
    result: PipelineResult,
) -> None:
    count = len(current_links(session, tenant_id, case.id))
    case.recording_authorization = _merge_authorization(
        case.recording_authorization, policy.authorization.value, count <= 1
    )
    persist_signaling_metadata(session, store, settings, tenant_id, case, incident, call, policy)
    if case.state == EnforceCaseState.OPENED.value:
        transition_case(session, tenant_id, case.id, EnforceCaseState.INTAKE, SYSTEM)
    refresh = recompute_case(session, store, tenant_id, case.id, config)
    result.snapshot_changed, result.alerted = refresh.changed, refresh.alerted
