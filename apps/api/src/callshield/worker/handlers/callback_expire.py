"""Job `callback.expire_sweep`: delete expired callback media, tombstone expired fields."""

from __future__ import annotations

from sqlalchemy.orm import Session

from callshield.callback.retention import sweep
from callshield.callback.runtime import get_runtime
from callshield.core.clock import utcnow
from callshield.db.models.platform import Job
from callshield.worker.queue import job_handler

EXPIRE_JOB_KIND = "callback.expire_sweep"


@job_handler(EXPIRE_JOB_KIND)
def run(session: Session, job: Job) -> None:
    sweep(session, get_runtime().media_store(), job.tenant_id, utcnow())
