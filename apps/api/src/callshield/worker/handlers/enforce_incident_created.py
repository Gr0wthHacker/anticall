"""Job `incident.created`: run the automatic case-building pipeline (PRD 19.12)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from callshield.casebuilder.pipeline import run_incident_pipeline
from callshield.db.models.platform import Job
from callshield.worker.queue import job_handler

JOB_KIND = "incident.created"


@job_handler(JOB_KIND)
def run(session: Session, job: Job) -> None:
    """Idempotent: re-delivery links nothing twice and alerts only on material change."""
    incident_id = uuid.UUID(str(job.payload["incident_id"]))
    run_incident_pipeline(session, job.tenant_id, incident_id)
