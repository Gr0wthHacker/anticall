"""Retention sweep: audio/transcripts 30 days, fields/metadata 90 days (tombstoned, not removed).

A user-opened case is a legal hold: records linked to a case that is not CLOSED are skipped
until the user closes (or deletes) the case.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_get, scoped_select
from callshield.callback.config import NUMBER_WINDOW_DAYS
from callshield.callback.storage import MediaStore
from callshield.db.models.callback_ext import (
    CallbackGlobalAttempt,
    CallbackMedia,
    CallbackRequest,
    CallbackResult,
)
from callshield.db.models.enforce import Case
from callshield.domain.enums import EnforceCaseState

STALE_DIALING = timedelta(minutes=10)


@dataclass
class SweepReport:
    media_deleted: int = 0
    results_tombstoned: int = 0
    held: int = 0
    stale_requests_failed: int = 0
    global_attempts_purged: int = 0


def on_legal_hold(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID | None) -> bool:
    if case_id is None:
        return False
    case = scoped_get(session, Case, case_id, tenant_id)
    return case is not None and case.state != EnforceCaseState.CLOSED.value


def _held_for_request(session: Session, tenant_id: uuid.UUID, request_id: uuid.UUID) -> bool:
    request = scoped_get(session, CallbackRequest, request_id, tenant_id)
    return request is not None and on_legal_hold(session, tenant_id, request.case_id)


def sweep(session: Session, store: MediaStore, tenant_id: uuid.UUID, now: datetime) -> SweepReport:
    report = SweepReport()
    media = session.execute(
        scoped_select(CallbackMedia, tenant_id).where(
            CallbackMedia.deleted_at.is_(None), CallbackMedia.expires_at <= now
        )
    ).scalars()
    for item in list(media):
        if _held_for_request(session, tenant_id, item.request_id):
            report.held += 1
            continue
        store.delete(item.storage_key)
        item.deleted_at = now
        report.media_deleted += 1
    results = session.execute(
        scoped_select(CallbackResult, tenant_id).where(
            CallbackResult.tombstoned_at.is_(None), CallbackResult.expires_at <= now
        )
    ).scalars()
    for row in list(results):
        if on_legal_hold(session, tenant_id, row.case_id):
            report.held += 1
            continue
        row.fields = {}
        row.call_metadata = {}
        row.tombstoned_at = now
        report.results_tombstoned += 1
    _fail_stale_requests(session, tenant_id, now, report)
    purged = session.execute(
        delete(CallbackGlobalAttempt).where(
            CallbackGlobalAttempt.attempted_at < now - timedelta(days=NUMBER_WINDOW_DAYS)
        )
    )
    report.global_attempts_purged = int(getattr(purged, "rowcount", 0) or 0)
    session.flush()
    return report


def _fail_stale_requests(
    session: Session, tenant_id: uuid.UUID, now: datetime, report: SweepReport
) -> None:
    stale = session.execute(
        scoped_select(CallbackRequest, tenant_id).where(
            CallbackRequest.status == "DIALING", CallbackRequest.updated_at < now - STALE_DIALING
        )
    ).scalars()
    for request in list(stale):
        request.status = "FAILED"
        request.open_slot = None
        request.updated_at = now
        report.stale_requests_failed += 1
