"""check_recording_processing_policy (19.12 step 4).

Absent or unclear consent means NOT_EVALUATED and is treated as denied: audio and
transcripts are never persisted, and nothing is sent to remote STT (19.13-2).
Signaling metadata is still kept (ENF-008).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.db.models.defend import ConsentRecord
from callshield.domain.enums import ProcessingMode, RecordingAuthorization
from callshield.evidence.access import is_processing_withdrawn

_RETAINED_MODES = {
    ProcessingMode.TRANSCRIPT_RETAINED.value,
    ProcessingMode.RECORDING_RETAINED.value,
}


@dataclass(frozen=True)
class RecordingPolicy:
    authorization: RecordingAuthorization
    consent_id: uuid.UUID | None
    processing_mode: str | None
    persist_audio: bool
    persist_transcript: bool
    reason: str

    @property
    def allows_remote_processing(self) -> bool:
        return self.authorization is RecordingAuthorization.PERMITTED


def _latest_consent(
    session: Session, tenant_id: uuid.UUID, line_id: uuid.UUID | None, call_id: uuid.UUID | None
) -> ConsentRecord | None:
    stmt = (
        select(ConsentRecord)
        .where(ConsentRecord.tenant_id == tenant_id)
        .order_by(ConsentRecord.created_at.desc(), ConsentRecord.id)
    )
    for record in session.execute(stmt).scalars():
        if call_id is not None and record.session_id == call_id:
            return record
        line_scoped = record.session_id is None and line_id is not None
        if line_scoped and record.legal_basis_config.get("line_id") == str(line_id):
            return record
    return None


def _deny(reason: str, record: ConsentRecord | None = None) -> RecordingPolicy:
    return RecordingPolicy(
        RecordingAuthorization.DENIED,
        record.id if record else None,
        record.processing_mode if record else None,
        False,
        False,
        reason,
    )


def check_recording_policy(
    session: Session,
    tenant_id: uuid.UUID,
    line_id: uuid.UUID | None,
    call_session_id: uuid.UUID | None,
) -> RecordingPolicy:
    """Evaluate the latest consent for the call (or its line). Fails closed."""
    if is_processing_withdrawn(session, tenant_id):
        return _deny("processing_withdrawn")
    record = _latest_consent(session, tenant_id, line_id, call_session_id)
    if record is None:
        return RecordingPolicy(
            RecordingAuthorization.NOT_EVALUATED, None, None, False, False, "no_consent_record"
        )
    if record.revoked_at is not None or not record.authorized:
        return _deny("consent_revoked_or_not_authorized", record)
    if record.processing_mode not in _RETAINED_MODES:
        return _deny("mode_does_not_permit_persistence", record)
    return RecordingPolicy(
        RecordingAuthorization.PERMITTED,
        record.id,
        record.processing_mode,
        record.processing_mode == ProcessingMode.RECORDING_RETAINED.value,
        True,
        "consent_permits_persistence",
    )
