"""Job `webhook.purge_receipts`: delete replay nonces older than the retention window.

Receipts only need to outlive the signature replay window; keeping them forever is needless
retention. Scoped to the job's tenant. Idempotent: running twice deletes nothing the second time.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from callshield.core.clock import utcnow
from callshield.core.config import get_settings
from callshield.db.models.defend_ext import WebhookReceipt
from callshield.db.models.platform import Job
from callshield.worker.queue import job_handler

PURGE_JOB_KIND = "webhook.purge_receipts"


@job_handler(PURGE_JOB_KIND)
def run(session: Session, job: Job) -> None:
    retention_days = get_settings().webhook_receipt_retention_days
    cutoff = utcnow() - timedelta(days=retention_days)
    session.execute(
        delete(WebhookReceipt).where(
            WebhookReceipt.tenant_id == job.tenant_id, WebhookReceipt.received_at < cutoff
        )
    )
