"""Integrity verification. A matching hash shows the stored bytes are unchanged since
ingest. It does not prove authenticity, chain of custody, or admissibility (PRD E-004)."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from callshield.core.hashing import sha256_hex
from callshield.db.models.enforce import EvidenceArtifact
from callshield.evidence.access import get_artifact
from callshield.evidence.store import ArtifactStore, ObjectNotFoundError

OK = "OK"
MISMATCH = "MISMATCH"
MISSING = "MISSING"
DELETED = "DELETED"


@dataclass(frozen=True)
class IntegrityReport:
    artifact_id: str
    status: str
    expected_sha256: str | None
    actual_sha256: str | None
    expected_length: int | None
    actual_length: int | None

    @property
    def ok(self) -> bool:
        return self.status == OK

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_artifact(store: ArtifactStore, artifact: EvidenceArtifact) -> IntegrityReport:
    """Compare stored bytes with the digest and length recorded at ingest."""
    base = (str(artifact.id), artifact.sha256, artifact.byte_length)
    if artifact.deleted_at is not None or artifact.storage_key is None:
        return IntegrityReport(base[0], DELETED, base[1], None, base[2], None)
    try:
        data = store.get(artifact.storage_key)
    except ObjectNotFoundError:
        return IntegrityReport(base[0], MISSING, base[1], None, base[2], None)
    actual = sha256_hex(data)
    good = actual == artifact.sha256 and len(data) == artifact.byte_length
    return IntegrityReport(base[0], OK if good else MISMATCH, base[1], actual, base[2], len(data))


def verify_integrity(
    session: Session, store: ArtifactStore, tenant_id: uuid.UUID, artifact_id: uuid.UUID
) -> IntegrityReport:
    """Tenant-scoped integrity check by id."""
    return verify_artifact(store, get_artifact(session, tenant_id, artifact_id))
