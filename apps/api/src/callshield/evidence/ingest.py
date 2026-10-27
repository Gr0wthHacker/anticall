"""Artifact ingest (ENF-005): digest, authorization, retention, provenance."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from callshield.audit import record_case_audit
from callshield.auth.scoping import scoped_get_or_404
from callshield.core.clock import utcnow
from callshield.core.config import Settings
from callshield.core.errors import PolicyBlockedError, UnprocessableError
from callshield.core.hashing import sha256_hex
from callshield.core.ids import new_id
from callshield.db.models.defend import Incident
from callshield.db.models.enforce import Case, EvidenceArtifact
from callshield.domain.enums import (
    AuthorizationState,
    EventOrigin,
    EvidenceLabel,
    EvidenceOrigin,
    EvidenceType,
    RedactionState,
)
from callshield.domain.topics import Topic
from callshield.evidence.access import actor_type_for, is_processing_withdrawn
from callshield.evidence.store import ArtifactStore
from callshield.evidence.taxonomy import validate_taxonomy
from callshield.ledger import append_event

MAX_ARTIFACT_BYTES = 25 * 1024 * 1024

# Content that needs an explicit PERMITTED processing authorization before it is persisted.
PERSISTENCE_GATED_TYPES = frozenset({EvidenceType.AUDIO_RECORDING, EvidenceType.AI_TRANSCRIPT})
# Lawful signaling metadata is kept even when speech capture is not authorized (ENF-008).
METADATA_TYPES = frozenset({EvidenceType.SIGNALING_METADATA, EvidenceType.OBSERVED_CLI})


@dataclass
class ArtifactInput:
    data: bytes
    source_type: EvidenceType
    origin: EvidenceOrigin
    evidence_label: EvidenceLabel
    media_type: str = "application/octet-stream"
    collection_method: str = ""
    collected_at: datetime | None = None
    processing_authorization: AuthorizationState = AuthorizationState.NOT_EVALUATED
    sharing_authorization: AuthorizationState = AuthorizationState.NOT_EVALUATED
    provenance: dict[str, Any] = field(default_factory=dict)
    case_id: uuid.UUID | None = None
    incident_id: uuid.UUID | None = None
    consent_id: uuid.UUID | None = None
    retention_days: int | None = None
    expires_at: datetime | None = None
    derived_from_id: uuid.UUID | None = None
    supersedes_id: uuid.UUID | None = None
    version: int = 1
    redaction_state: RedactionState = RedactionState.NONE


def retention_days_for(settings: Settings, source_type: EvidenceType) -> int:
    """Design-default retention (PRD 11.3). Not legal advice; operators must adjust."""
    if source_type is EvidenceType.AUDIO_RECORDING:
        return settings.retention_recording_days
    if source_type is EvidenceType.AI_TRANSCRIPT:
        return settings.retention_transcript_days
    if source_type in METADATA_TYPES:
        return settings.retention_metadata_days
    return settings.retention_case_days


def _enforce_persistence_policy(
    session: Session, tenant_id: uuid.UUID, spec: ArtifactInput
) -> None:
    if spec.source_type in PERSISTENCE_GATED_TYPES and (
        spec.processing_authorization is not AuthorizationState.PERMITTED
    ):
        raise PolicyBlockedError(
            "processing is not authorized for this content; nothing was stored",
            code="PERSISTENCE_NOT_AUTHORIZED",
        )
    if spec.source_type not in METADATA_TYPES and is_processing_withdrawn(session, tenant_id):
        raise PolicyBlockedError(
            "processing was withdrawn for this account; nothing was stored",
            code="PROCESSING_WITHDRAWN",
        )
    if len(spec.data) > MAX_ARTIFACT_BYTES:
        raise UnprocessableError("artifact too large", code="ARTIFACT_TOO_LARGE")
    validate_taxonomy(spec.origin, spec.evidence_label.value, spec.provenance)


def _check_links(session: Session, tenant_id: uuid.UUID, spec: ArtifactInput) -> None:
    if spec.case_id is not None:
        scoped_get_or_404(session, Case, spec.case_id, tenant_id)
    if spec.incident_id is not None:
        scoped_get_or_404(session, Incident, spec.incident_id, tenant_id)


def _build_row(
    tenant_id: uuid.UUID, settings: Settings, spec: ArtifactInput, artifact_id: uuid.UUID
) -> EvidenceArtifact:
    collected = spec.collected_at or utcnow()
    expires = spec.expires_at or collected + timedelta(
        days=spec.retention_days or retention_days_for(settings, spec.source_type)
    )
    return EvidenceArtifact(
        id=artifact_id,
        tenant_id=tenant_id,
        case_id=spec.case_id,
        incident_id=spec.incident_id,
        source_type=spec.source_type.value,
        media_type=spec.media_type,
        storage_key=f"{tenant_id}/{artifact_id}",
        sha256=sha256_hex(spec.data),
        byte_length=len(spec.data),
        collected_at=collected,
        collection_method=spec.collection_method,
        evidence_label=spec.evidence_label.value,
        origin=spec.origin.value,
        processing_authorization=spec.processing_authorization.value,
        sharing_authorization=spec.sharing_authorization.value,
        consent_id=spec.consent_id,
        redaction_state=spec.redaction_state.value,
        derived_from_id=spec.derived_from_id,
        supersedes_id=spec.supersedes_id,
        version=spec.version,
        provenance=spec.provenance,
        expires_at=expires,
    )


def ingest_artifact(
    session: Session,
    store: ArtifactStore,
    settings: Settings,
    tenant_id: uuid.UUID,
    spec: ArtifactInput,
    actor: str = "system",
) -> EvidenceArtifact:
    """Persist content + inventory row. Refuses gated content without authorization.

    The object is written first and removed again if the row cannot be flushed.
    The caller owns commit.
    """
    _enforce_persistence_policy(session, tenant_id, spec)
    _check_links(session, tenant_id, spec)
    row = _build_row(tenant_id, settings, spec, new_id())
    assert row.storage_key is not None
    store.put(row.storage_key, spec.data)
    try:
        session.add(row)
        session.flush()
        _record_ingest(session, tenant_id, row, actor)
    except Exception:
        store.delete(row.storage_key)
        raise
    return row


def _record_ingest(
    session: Session, tenant_id: uuid.UUID, row: EvidenceArtifact, actor: str
) -> None:
    payload = {
        "artifact_id": str(row.id),
        "case_id": str(row.case_id) if row.case_id else None,
        "source_type": row.source_type,
        "origin": row.origin,
        "sha256": row.sha256,
    }
    origin = EventOrigin.USER_ACTION if actor.startswith("user:") else EventOrigin.SYSTEM
    append_event(
        session, tenant_id, Topic.EVIDENCE_CREATED, payload, origin, f"evidence-created:{row.id}"
    )
    if row.case_id is not None:
        record_case_audit(
            session,
            tenant_id,
            row.case_id,
            actor_type_for(actor),
            "evidence.ingested",
            {"artifact_id": str(row.id), "sha256": row.sha256, "origin": row.origin},
            actor_id=actor,
        )
