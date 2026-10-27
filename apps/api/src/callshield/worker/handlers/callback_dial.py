"""Jobs `callback.dial` and `callback.place` (idempotent).

`callback.dial` re-runs every gate, claims the request, consumes the single-use authorization
and counts the attempt; the job's commit makes the attempt durable. It then enqueues
`callback.place` (max_attempts=1, so a crash can never ring twice), which does a last
time/blocklist/identity check and rings. Only the user's one-tap authorization starts this
chain; the AI never schedules anything.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from callshield.callback.dialer_service import PLACE_JOB_KIND, place_call, run_dial
from callshield.callback.runtime import get_runtime
from callshield.callback.service import DIAL_JOB_KIND
from callshield.db.models.platform import Job
from callshield.worker.queue import job_handler


@job_handler(DIAL_JOB_KIND)
def run(session: Session, job: Job) -> None:
    request_id = uuid.UUID(str(job.payload["request_id"]))
    run_dial(session, get_runtime(), job.tenant_id, request_id, defer_place=True)


@job_handler(PLACE_JOB_KIND)
def place(session: Session, job: Job) -> None:
    request_id = uuid.UUID(str(job.payload["request_id"]))
    place_call(session, get_runtime(), job.tenant_id, request_id)
