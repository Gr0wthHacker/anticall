"""Evidence subsystem: artifact store, ingest, derivatives, retention, rights.

No part of this package asserts that evidence is admissible, notarized, forensically
certified or proof of a caller's identity (PRD 19.1, 19.4).
"""

from __future__ import annotations

import callshield.db.models.enforce_ext  # noqa: F401  (register extension tables)
from callshield.evidence.access import (
    describe_artifact,
    get_artifact,
    require_content_access,
    require_sharing,
    set_authorization,
)
from callshield.evidence.derive import (
    RedactionSpec,
    redact_artifact,
    revise_transcript,
    transcript_versions,
)
from callshield.evidence.ingest import ArtifactInput, ingest_artifact
from callshield.evidence.integrity import IntegrityReport, verify_artifact, verify_integrity
from callshield.evidence.retention import (
    delete_artifacts,
    expire_sweep,
    schedule_expire_sweep,
    set_legal_hold,
)
from callshield.evidence.rights import (
    DELETION_LIMITS,
    build_data_export,
    request_artifact_deletion,
    withdraw_processing,
)
from callshield.evidence.store import (
    ArtifactStore,
    LocalArtifactStore,
    S3ArtifactStore,
    get_artifact_store,
    set_artifact_store,
)

__all__ = [
    "DELETION_LIMITS",
    "ArtifactInput",
    "ArtifactStore",
    "IntegrityReport",
    "LocalArtifactStore",
    "RedactionSpec",
    "S3ArtifactStore",
    "build_data_export",
    "delete_artifacts",
    "describe_artifact",
    "expire_sweep",
    "get_artifact",
    "get_artifact_store",
    "ingest_artifact",
    "redact_artifact",
    "request_artifact_deletion",
    "require_content_access",
    "require_sharing",
    "revise_transcript",
    "schedule_expire_sweep",
    "set_artifact_store",
    "set_authorization",
    "set_legal_hold",
    "transcript_versions",
    "verify_artifact",
    "verify_integrity",
    "withdraw_processing",
]
