"""Job `case.export`: build the versioned evidence packet zip (PRD 19.5)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from callshield.casebuilder.export import EXPORT_JOB_KIND, run_export
from callshield.db.models.platform import Job
from callshield.evidence.store import get_artifact_store
from callshield.worker.queue import job_handler


@job_handler(EXPORT_JOB_KIND)
def run(session: Session, job: Job) -> None:
    export_id = uuid.UUID(str(job.payload["export_id"]))
    run_export(session, get_artifact_store(), job.tenant_id, export_id)
