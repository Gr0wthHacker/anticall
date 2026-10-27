"""Artifact authorization checks: tenant scoping, processing and sharing gates."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.audit import record_audit
from callshield.auth.scoping import scoped_get_or_404, scoped_select
from callshield.core.clock import utcnow
from callshield.core.errors import ApiError, ForbiddenError
from callshield.db.models.enforce import EvidenceArtifact
from callshield.db.models.enforce_ext import DataSubjectRequest
from callshield.domain.enums import ActorType, AuthorizationState


class GoneError(ApiError):
    status_code = 410
    code = "ARTIFACT_DELETED"


def actor_type_for(actor: str) -> ActorType:
    if actor.startswith("user:"):
        return ActorType.USER
    if actor.startswith("worker"):
        return ActorType.WORKER
    return ActorType.SYSTEM


def get_artifact(
    session: Session, tenant_id: uuid.UUID, artifact_id: uuid.UUID
) -> EvidenceArtifact:
    """Tenant-scoped fetch; another tenant's artifact is a 404."""
    return scoped_get_or_404(session, EvidenceArtifact, artifact_id, tenant_id)


def require_content_access(artifact: EvidenceArtifact) -> None:
    """Gate for reading stored content."""
    if artifact.deleted_at is not None:
        raise GoneError("artifact content was deleted; only audit metadata remains")
    if artifact.processing_authorization in (
        AuthorizationState.DENIED.value,
        AuthorizationState.REVOKED.value,
    ):
        raise ForbiddenError("processing is not authorized", code="PROCESSING_NOT_AUTHORIZED")


def require_sharing(artifact: EvidenceArtifact) -> None:
    """Gate for any outward sharing (export attachment, filing attachment)."""
    require_content_access(artifact)
    if artifact.sharing_authorization != AuthorizationState.PERMITTED.value:
        raise ForbiddenError("sharing is not authorized", code="SHARING_NOT_AUTHORIZED")


def sharing_blocker(artifact: EvidenceArtifact) -> str | None:
    """Non-raising variant: a reason code, or None when the artifact may be shared."""
    try:
        require_sharing(artifact)
    except ApiError as exc:
        return exc.code
    return None


def is_processing_withdrawn(session: Session, tenant_id: uuid.UUID) -> bool:
    """True once the tenant filed a completed WITHDRAWAL request (19.13-12)."""
    stmt = (
        select(DataSubjectRequest.id)
        .where(DataSubjectRequest.tenant_id == tenant_id)
        .where(DataSubjectRequest.kind == "WITHDRAWAL")
        .where(DataSubjectRequest.status == "COMPLETED")
        .limit(1)
    )
    return session.execute(stmt).first() is not None


def set_authorization(
    session: Session,
    tenant_id: uuid.UUID,
    artifact_id: uuid.UUID,
    *,
    field: str,
    state: AuthorizationState,
    actor: str,
) -> EvidenceArtifact:
    """Change processing_ or sharing_authorization (state, never content), audited."""
    if field not in ("processing_authorization", "sharing_authorization"):
        raise ValueError("unknown authorization field")
    artifact = get_artifact(session, tenant_id, artifact_id)
    previous = getattr(artifact, field)
    setattr(artifact, field, state.value)
    record_audit(
        session,
        tenant_id,
        actor,
        "evidence.authorization_changed",
        {"artifact_id": str(artifact.id), "field": field, "from": previous, "to": state.value},
    )
    return artifact


def latest_in_chain(
    session: Session, tenant_id: uuid.UUID, artifact: EvidenceArtifact
) -> EvidenceArtifact:
    """Follow `supersedes_id` successors to the newest version of a derived text."""
    current = artifact
    while True:
        stmt = scoped_select(EvidenceArtifact, tenant_id).where(
            EvidenceArtifact.supersedes_id == current.id
        )
        successor = (
            session.execute(stmt.order_by(EvidenceArtifact.version.desc())).scalars().first()
        )
        if successor is None:
            return current
        current = successor


def describe_artifact(artifact: EvidenceArtifact) -> dict[str, Any]:
    """API-safe view: never exposes storage_key; labels stay visible (ENF-002)."""
    return {
        "id": str(artifact.id),
        "case_id": str(artifact.case_id) if artifact.case_id else None,
        "incident_id": str(artifact.incident_id) if artifact.incident_id else None,
        "source_type": artifact.source_type,
        "media_type": artifact.media_type,
        "sha256": artifact.sha256,
        "byte_length": artifact.byte_length,
        "origin": artifact.origin,
        "evidence_label": artifact.evidence_label,
        "collection_method": artifact.collection_method,
        "collected_at": artifact.collected_at.isoformat(),
        "processing_authorization": artifact.processing_authorization,
        "sharing_authorization": artifact.sharing_authorization,
        "redaction_state": artifact.redaction_state,
        "derived_from_id": str(artifact.derived_from_id) if artifact.derived_from_id else None,
        "supersedes_id": str(artifact.supersedes_id) if artifact.supersedes_id else None,
        "version": artifact.version,
        "provenance": artifact.provenance,
        "expires_at": artifact.expires_at.isoformat() if artifact.expires_at else None,
        "legal_hold": artifact.legal_hold,
        "deleted_at": artifact.deleted_at.isoformat() if artifact.deleted_at else None,
        "content_available": artifact.deleted_at is None and artifact.storage_key is not None,
        "as_of": utcnow().isoformat(),
    }
