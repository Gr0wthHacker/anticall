"""Job `evidence.expire_sweep`: delete expired objects and tombstone their rows."""

from __future__ import annotations

from sqlalchemy.orm import Session

from callshield.casebuilder.export import expire_exports
from callshield.db.models.platform import Job
from callshield.evidence.retention import DEFAULT_SWEEP_LIMIT, SWEEP_JOB_KIND, expire_sweep
from callshield.evidence.store import get_artifact_store
from callshield.worker.queue import job_handler


@job_handler(SWEEP_JOB_KIND)
def run(session: Session, job: Job) -> None:
    """Tenant-scoped, idempotent. Legal-hold items are skipped and reported."""
    store = get_artifact_store()
    limit = int(job.payload.get("limit", DEFAULT_SWEEP_LIMIT))
    expire_sweep(session, store, job.tenant_id, limit=limit)
    expire_exports(session, store, job.tenant_id)
