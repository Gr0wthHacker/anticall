"""Retention, legal hold and deletion (ENF-010, ENF-020, PRD 11.3).

Deleting removes the stored object AND tombstones the inventory row (deleted_at set,
storage_key cleared, provenance scrubbed). Non-personal audit metadata (ids, source
type, digest, size, timestamps) is kept. A legal hold blocks every deletion path;
there is no bypass argument.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from callshield.audit import record_audit, record_case_audit
from callshield.auth.scoping import scoped_select
from callshield.core.clock import utcnow
from callshield.db.models.enforce import EvidenceArtifact
from callshield.domain.enums import EventOrigin
from callshield.domain.topics import Topic
from callshield.evidence.access import actor_type_for, get_artifact
from callshield.evidence.store import ArtifactStore
from callshield.ledger import append_event
from callshield.worker.queue import enqueue

SWEEP_JOB_KIND = "evidence.expire_sweep"
DEFAULT_SWEEP_LIMIT = 500


@dataclass
class DeletionResult:
    deleted: list[str] = field(default_factory=list)
    retained: list[dict[str, str]] = field(default_factory=list)


def artifact_family(
    session: Session, tenant_id: uuid.UUID, artifact: EvidenceArtifact
) -> list[EvidenceArtifact]:
    """Root artifact plus every derivative/revision descended from it."""
    root = artifact
    while (parent_id := root.derived_from_id or root.supersedes_id) is not None:
        root = get_artifact(session, tenant_id, parent_id)
    family = {root.id: root}
    frontier = [root.id]
    while frontier:
        stmt = scoped_select(EvidenceArtifact, tenant_id).where(
            EvidenceArtifact.derived_from_id.in_(frontier)
            | EvidenceArtifact.supersedes_id.in_(frontier)
        )
        children = [c for c in session.execute(stmt).scalars() if c.id not in family]
        family.update({c.id: c for c in children})
        frontier = [c.id for c in children]
    return sorted(family.values(), key=lambda a: (a.created_at, str(a.id)))


def set_legal_hold(
    session: Session,
    tenant_id: uuid.UUID,
    artifact_id: uuid.UUID,
    hold: bool,
    actor: str,
    reason: str,
) -> list[EvidenceArtifact]:
    """Place or release a hold on an artifact and its whole derivative family."""
    family = artifact_family(session, tenant_id, get_artifact(session, tenant_id, artifact_id))
    for item in family:
        item.legal_hold = hold
    record_audit(
        session,
        tenant_id,
        actor,
        "evidence.legal_hold_set" if hold else "evidence.legal_hold_released",
        {"artifact_ids": [str(a.id) for a in family], "reason": reason},
    )
    return family


def _tombstone(
    session: Session,
    store: ArtifactStore,
    artifact: EvidenceArtifact,
    topic: Topic,
    reason: str,
    now: datetime,
    actor: str,
) -> None:
    if artifact.storage_key is not None:
        store.delete(artifact.storage_key)
    previous_keys = sorted(artifact.provenance.keys())
    artifact.storage_key = None
    artifact.deleted_at = now
    artifact.provenance = {
        "tombstone": {
            "reason": reason,
            "deleted_at": now.isoformat(),
            "scrubbed_keys": previous_keys,
        }
    }
    payload: dict[str, Any] = {"artifact_id": str(artifact.id), "reason": reason}
    append_event(
        session,
        artifact.tenant_id,
        topic,
        payload,
        EventOrigin.USER_ACTION if actor.startswith("user:") else EventOrigin.WORKER,
        f"{topic.value}:{artifact.id}",
    )
    if artifact.case_id is not None:
        record_case_audit(
            session,
            artifact.tenant_id,
            artifact.case_id,
            actor_type_for(actor),
            "evidence.deleted",
            payload,
            actor_id=actor,
        )


def delete_artifacts(
    session: Session,
    store: ArtifactStore,
    tenant_id: uuid.UUID,
    artifact_id: uuid.UUID,
    actor: str,
    reason: str,
    now: datetime | None = None,
) -> DeletionResult:
    """Delete an artifact and its derivatives, except items under legal hold."""
    now = now or utcnow()
    target = get_artifact(session, tenant_id, artifact_id)
    result = DeletionResult()
    for item in artifact_family(session, tenant_id, target):
        if item.deleted_at is not None:
            continue
        if item.legal_hold:
            result.retained.append({"artifact_id": str(item.id), "reason": "LEGAL_HOLD"})
            continue
        _tombstone(session, store, item, Topic.EVIDENCE_DELETED, reason, now, actor)
        result.deleted.append(str(item.id))
    return result


@dataclass
class SweepResult:
    deleted_ids: list[str] = field(default_factory=list)
    held_ids: list[str] = field(default_factory=list)


def expire_sweep(
    session: Session,
    store: ArtifactStore,
    tenant_id: uuid.UUID,
    now: datetime | None = None,
    limit: int = DEFAULT_SWEEP_LIMIT,
) -> SweepResult:
    """Delete objects and tombstone rows whose retention has expired. Idempotent."""
    now = now or utcnow()
    stmt = (
        scoped_select(EvidenceArtifact, tenant_id)
        .where(EvidenceArtifact.expires_at.is_not(None))
        .where(EvidenceArtifact.expires_at <= now)
        .where(EvidenceArtifact.deleted_at.is_(None))
        .order_by(EvidenceArtifact.expires_at)
        .limit(limit)
    )
    result = SweepResult()
    for artifact in session.execute(stmt).scalars().all():
        if artifact.legal_hold:
            result.held_ids.append(str(artifact.id))
            continue
        _tombstone(
            session, store, artifact, Topic.ARTIFACT_EXPIRED, "retention_expired", now, "worker"
        )
        result.deleted_ids.append(str(artifact.id))
    if result.deleted_ids or result.held_ids:
        record_audit(
            session,
            tenant_id,
            "worker",
            "evidence.expire_sweep",
            {"deleted": len(result.deleted_ids), "held": len(result.held_ids)},
        )
    return result


def schedule_expire_sweep(
    session: Session, tenant_id: uuid.UUID, now: datetime | None = None
) -> bool:
    """Enqueue at most one sweep per tenant per UTC day. Returns True if newly queued."""
    day = (now or utcnow()).date().isoformat()
    _, created = enqueue(
        session, tenant_id, SWEEP_JOB_KIND, {}, idempotency_key=f"evidence-sweep:{day}"
    )
    return created
