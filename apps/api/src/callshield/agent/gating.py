"""AI-outbound gate: both settings flags AND the line's processing consent (PRD 11.3).

If any condition fails the call falls back to human-only / DTMF-only mode.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.core.config import Settings
from callshield.db.models.defend import CallSession, ConsentRecord
from callshield.domain.enums import ProcessingMode

_AI_MODES = frozenset(
    {
        ProcessingMode.LIVE_STT_TRANSIENT,
        ProcessingMode.TRANSCRIPT_RETAINED,
        ProcessingMode.RECORDING_RETAINED,
    }
)
_CANDIDATE_LIMIT = 100


@dataclass(frozen=True)
class AiGate:
    allowed: bool
    reason: str = ""


def latest_line_consent(
    session: Session, tenant_id: uuid.UUID, line_id: uuid.UUID
) -> ConsentRecord | None:
    """Newest consent record tied to the line (via its call sessions or legal_basis_config)."""
    line_sessions = select(CallSession.id).where(
        CallSession.tenant_id == tenant_id, CallSession.line_id == line_id
    )
    stmt = (
        select(ConsentRecord)
        .where(ConsentRecord.tenant_id == tenant_id)
        .order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc())
        .limit(_CANDIDATE_LIMIT)
    )
    session_ids = set(session.execute(line_sessions).scalars())
    for record in session.execute(stmt).scalars():
        if record.session_id in session_ids or (
            record.legal_basis_config.get("line_id") == str(line_id)
        ):
            return record
    return None


def evaluate_ai_gate(
    settings: Settings, session: Session, tenant_id: uuid.UUID, line_id: uuid.UUID
) -> AiGate:
    if not settings.outbound_enabled:
        return AiGate(False, "OUTBOUND_DISABLED")
    if not settings.ai_outbound_enabled:
        return AiGate(False, "AI_OUTBOUND_DISABLED")
    consent = latest_line_consent(session, tenant_id, line_id)
    if consent is None:
        return AiGate(False, "NO_PROCESSING_CONSENT")
    if not consent.authorized or consent.revoked_at is not None:
        return AiGate(False, "CONSENT_NOT_AUTHORIZED_OR_REVOKED")
    if consent.processing_mode not in _AI_MODES:
        return AiGate(False, "CONSENT_MODE_DISALLOWS_LIVE_PROCESSING")
    return AiGate(True)
