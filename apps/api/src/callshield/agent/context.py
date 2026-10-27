"""Per-call agent state. Held by the controller; models never receive the dialer handle."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from callshield.domain.enums import EvidenceLabel
from callshield.ivr.navigator import ApprovedPath
from callshield.resolve.dialer import CallHandle


@dataclass
class TranscriptEntry:
    seq: int
    speaker: str  # COUNTERPARTY | AGENT | SYSTEM
    text: str
    label: EvidenceLabel
    complete: bool = True
    offset_seconds: float = 0.0


@dataclass
class ClaimRecord:
    """Stored on the attempt (`agent_claims`). Never a fact about the sender's systems."""

    type: str
    label: EvidenceLabel
    text: str = ""
    transcript_seq: int | None = None
    accepted: bool = True
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "label": str(self.label),
            "text": self.text,
            "transcript_seq": self.transcript_seq,
            "accepted": self.accepted,
            "note": self.note,
        }


@dataclass
class AgentSessionContext:
    session_id: uuid.UUID
    case_id: uuid.UUID
    destination_id: uuid.UUID
    handle: CallHandle
    case_summary: dict[str, Any]
    destination_info: dict[str, Any]
    approved_path: ApprovedPath | None
    active: bool = True
    authorized: bool = True  # set by the service ONLY after policy approval + consumed auth
    ai_allowed: bool = False
    human_connected: bool = False
    pending_dtmf: str | None = None  # the single digit the navigator approved right now
    ended: bool = False
    end_reason: str = ""
    handoff_requested: bool = False
    request_spoken: bool = False
    request_delivered: bool = False  # opt-out template completed uninterrupted
    verified_ack: ClaimRecord | None = None  # set by controller from counterparty text only
    transcript: list[TranscriptEntry] = field(default_factory=list)
    claims: list[ClaimRecord] = field(default_factory=list)
    dtmf_sent: list[str] = field(default_factory=list)
    spoken_template_ids: list[str] = field(default_factory=list)
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    rejected_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    dispositions: list[dict[str, Any]] = field(default_factory=list)

    def add_transcript(
        self, speaker: str, text: str, label: EvidenceLabel, *, complete: bool, offset: float
    ) -> TranscriptEntry:
        entry = TranscriptEntry(len(self.transcript) + 1, speaker, text, label, complete, offset)
        self.transcript.append(entry)
        return entry
