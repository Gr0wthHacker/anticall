"""Derived artifacts: redactions and transcript revisions.

Originals are never touched. Each derivative is a new artifact row + new object that
references its source (`derived_from_id`) and, for revisions, the immediate prior
version (`supersedes_id`, 19.13-5).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from callshield.audit import record_case_audit
from callshield.auth.scoping import scoped_select
from callshield.core.config import Settings
from callshield.core.errors import ConflictError, UnprocessableError
from callshield.core.hashing import sha256_hex
from callshield.db.models.enforce import EvidenceArtifact
from callshield.domain.enums import (
    AuthorizationState,
    EventOrigin,
    EvidenceLabel,
    EvidenceOrigin,
    EvidenceType,
    RedactionState,
)
from callshield.domain.topics import Topic
from callshield.evidence.access import actor_type_for, get_artifact, require_content_access
from callshield.evidence.ingest import ArtifactInput, ingest_artifact
from callshield.evidence.store import ArtifactStore
from callshield.ledger import append_event

REDACTION_MARK = "[REDACTED]"
PHONE_MARK = "[REDACTED-PHONE]"
_PHONE = re.compile(r"\+?\d[\d\-\s().]{8,}\d")
_TEXT_TYPES = ("text/", "application/json")


@dataclass
class RedactionSpec:
    terms: list[str] = field(default_factory=list)
    redact_phone_numbers: bool = True
    replacement_content: bytes | None = None  # externally redacted binary (image/audio)


def _is_text(media_type: str) -> bool:
    return media_type.startswith(_TEXT_TYPES)


def _apply_text_redactions(text: str, spec: RedactionSpec) -> tuple[str, dict[str, Any]]:
    report: dict[str, Any] = {"method": "text_replace_v1", "terms": [], "phone_numbers": 0}
    for term in sorted({t for t in spec.terms if t}, key=len, reverse=True):
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        text, count = pattern.subn(REDACTION_MARK, text)
        # Terms may be personal data: record only a digest and counts, never the term.
        report["terms"].append(
            {"sha256": sha256_hex(term.lower()), "length": len(term), "occurrences": count}
        )
    if spec.redact_phone_numbers:
        text, report["phone_numbers"] = _PHONE.subn(PHONE_MARK, text)
    return text, report


def _redacted_bytes(
    original: EvidenceArtifact, content: bytes, spec: RedactionSpec
) -> tuple[bytes, dict[str, Any]]:
    if spec.replacement_content is not None:
        return spec.replacement_content, {"method": "supplied_replacement"}
    if not _is_text(original.media_type):
        raise UnprocessableError(
            "binary media cannot be redacted in-process; supply replacement_content",
            code="REDACTION_UNSUPPORTED_MEDIA",
        )
    text, report = _apply_text_redactions(content.decode("utf-8", errors="replace"), spec)
    return text.encode("utf-8"), report


def redact_artifact(
    session: Session,
    store: ArtifactStore,
    settings: Settings,
    tenant_id: uuid.UUID,
    artifact_id: uuid.UUID,
    spec: RedactionSpec,
    actor: str,
) -> EvidenceArtifact:
    """Create a derived, redacted artifact; the original stays as it is."""
    original = get_artifact(session, tenant_id, artifact_id)
    require_content_access(original)
    assert original.storage_key is not None
    data, report = _redacted_bytes(original, store.get(original.storage_key), spec)
    derived = ingest_artifact(
        session,
        store,
        settings,
        tenant_id,
        ArtifactInput(
            data=data,
            source_type=EvidenceType(original.source_type),
            origin=EvidenceOrigin.DERIVED,
            evidence_label=EvidenceLabel(original.evidence_label),
            media_type=original.media_type,
            collection_method="redaction",
            processing_authorization=AuthorizationState(original.processing_authorization),
            sharing_authorization=AuthorizationState(original.sharing_authorization),
            provenance={
                "derived_from": {"artifact_id": str(original.id), "sha256": original.sha256},
                "redaction": report,
                "redacted_by": actor,
            },
            case_id=original.case_id,
            incident_id=original.incident_id,
            consent_id=original.consent_id,
            expires_at=original.expires_at,
            derived_from_id=original.id,
            redaction_state=RedactionState.REDACTED_DERIVATIVE,
        ),
        actor,
    )
    _record_derivation(session, tenant_id, derived, Topic.EVIDENCE_REDACTED, "redacted", actor)
    return derived


def _record_derivation(
    session: Session,
    tenant_id: uuid.UUID,
    derived: EvidenceArtifact,
    topic: Topic,
    verb: str,
    actor: str,
) -> None:
    payload = {
        "artifact_id": str(derived.id),
        "derived_from_id": str(derived.derived_from_id) if derived.derived_from_id else None,
        "supersedes_id": str(derived.supersedes_id) if derived.supersedes_id else None,
        "version": derived.version,
        "case_id": str(derived.case_id) if derived.case_id else None,
    }
    origin = EventOrigin.USER_ACTION if actor.startswith("user:") else EventOrigin.SYSTEM
    append_event(session, tenant_id, topic, payload, origin, f"{topic.value}:{derived.id}")
    if derived.case_id is not None:
        record_case_audit(
            session,
            tenant_id,
            derived.case_id,
            actor_type_for(actor),
            f"evidence.{verb}",
            payload,
            actor_id=actor,
        )


def _revision_input(
    prior: EvidenceArtifact, corrected_text: str, reason: str, actor: str
) -> ArtifactInput:
    """Next transcript version: immutable references to the prior and the root source."""
    correction = {
        "reason": reason,
        "corrected_by": actor,
        "prior_artifact_id": str(prior.id),
        "prior_sha256": prior.sha256,
        "prior_version": prior.version,
    }
    return ArtifactInput(
        data=corrected_text.encode("utf-8"),
        source_type=EvidenceType.AI_TRANSCRIPT,
        origin=EvidenceOrigin.DERIVED,
        evidence_label=EvidenceLabel(prior.evidence_label),
        media_type="text/plain",
        collection_method="transcript_correction",
        processing_authorization=AuthorizationState(prior.processing_authorization),
        sharing_authorization=AuthorizationState(prior.sharing_authorization),
        provenance={"correction": correction},
        case_id=prior.case_id,
        incident_id=prior.incident_id,
        consent_id=prior.consent_id,
        expires_at=prior.expires_at,
        derived_from_id=prior.derived_from_id or prior.id,
        supersedes_id=prior.id,
        version=prior.version + 1,
    )


def revise_transcript(
    session: Session,
    store: ArtifactStore,
    settings: Settings,
    tenant_id: uuid.UUID,
    artifact_id: uuid.UUID,
    corrected_text: str,
    reason: str,
    actor: str,
) -> EvidenceArtifact:
    """Append a corrected transcript version that points at the exact prior version."""
    prior = get_artifact(session, tenant_id, artifact_id)
    if prior.source_type != EvidenceType.AI_TRANSCRIPT.value:
        raise UnprocessableError(
            "only transcript artifacts can be revised", code="NOT_A_TRANSCRIPT"
        )
    require_content_access(prior)
    existing = session.execute(
        scoped_select(EvidenceArtifact, tenant_id).where(EvidenceArtifact.supersedes_id == prior.id)
    ).first()
    if existing is not None:
        raise ConflictError("transcript already revised; revise the latest version")
    spec = _revision_input(prior, corrected_text, reason, actor)
    revision = ingest_artifact(session, store, settings, tenant_id, spec, actor)
    _record_derivation(
        session, tenant_id, revision, Topic.CORRECTION_RECORDED, "transcript_revised", actor
    )
    return revision


def transcript_versions(
    session: Session, tenant_id: uuid.UUID, artifact_id: uuid.UUID
) -> list[EvidenceArtifact]:
    """All versions in the revision chain containing `artifact_id`, oldest first."""
    node = get_artifact(session, tenant_id, artifact_id)
    while node.supersedes_id is not None:
        node = get_artifact(session, tenant_id, node.supersedes_id)
    chain = [node]
    while True:
        stmt = scoped_select(EvidenceArtifact, tenant_id).where(
            EvidenceArtifact.supersedes_id == chain[-1].id
        )
        nxt = session.execute(stmt).scalars().first()
        if nxt is None:
            return chain
        chain.append(nxt)


def list_derivatives(
    session: Session, tenant_id: uuid.UUID, artifact_id: uuid.UUID
) -> list[EvidenceArtifact]:
    stmt = scoped_select(EvidenceArtifact, tenant_id).where(
        EvidenceArtifact.derived_from_id == artifact_id
    )
    return list(session.execute(stmt.order_by(EvidenceArtifact.created_at)).scalars().all())


__all__ = [
    "RedactionSpec",
    "list_derivatives",
    "redact_artifact",
    "revise_transcript",
    "transcript_versions",
]
