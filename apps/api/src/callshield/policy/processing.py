"""Consent / processing permission module (PRD 11.3, 19.13 scenario 2).

Standalone and pure: no DB access. Default DENY. Live processing and persistence are
separate gates. When live STT is denied, callers get provider-only routing (voicemail/ring)
and no audio or transcript is processed, sent or stored.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from callshield.core.config import Settings
from callshield.domain.enums import ProcessingMode
from callshield.policy.jurisdiction import (
    JurisdictionEntry,
    JurisdictionPolicy,
    default_policy,
)

_LIVE_MODES = frozenset(
    {
        ProcessingMode.LIVE_STT_TRANSIENT,
        ProcessingMode.TRANSCRIPT_RETAINED,
        ProcessingMode.RECORDING_RETAINED,
    }
)
_TRANSCRIPT_MODES = frozenset(
    {ProcessingMode.TRANSCRIPT_RETAINED, ProcessingMode.RECORDING_RETAINED}
)


class ConsentLike(Protocol):
    @property
    def processing_mode(self) -> str: ...

    @property
    def authorized(self) -> bool: ...

    @property
    def revoked_at(self) -> Any: ...

    @property
    def legal_basis_config(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ProcessingDecision:
    allow_live_stt: bool = False
    allow_recording: bool = False
    allow_transcript_persist: bool = False
    allow_remote_stt: bool = False
    mode: ProcessingMode = ProcessingMode.METADATA_ONLY
    reasons: tuple[str, ...] = field(default_factory=tuple)
    jurisdiction: str = "UNKNOWN"
    policy_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "allow_live_stt": self.allow_live_stt,
            "allow_recording": self.allow_recording,
            "allow_transcript_persist": self.allow_transcript_persist,
            "allow_remote_stt": self.allow_remote_stt,
            "mode": self.mode.value,
            "reasons": list(self.reasons),
            "jurisdiction": self.jurisdiction,
            "policy_version": self.policy_version,
        }


def _active_consents(records: Iterable[ConsentLike]) -> dict[ProcessingMode, ConsentLike]:
    """Latest record per mode wins (input must be oldest-first). Revoked/unauthorized
    records clear earlier grants for that mode."""
    latest: dict[ProcessingMode, ConsentLike] = {}
    for record in records:
        try:
            mode = ProcessingMode(record.processing_mode)
        except ValueError:
            continue
        if record.authorized and record.revoked_at is None:
            latest[mode] = record
        else:
            latest.pop(mode, None)
    return latest


def _resolve_entry(
    jurisdiction: str | JurisdictionEntry | None, policy: JurisdictionPolicy
) -> JurisdictionEntry:
    if isinstance(jurisdiction, JurisdictionEntry):
        return jurisdiction
    return policy.lookup(jurisdiction)


def _live_reasons(
    settings: Settings, entry: JurisdictionEntry, grants: dict[ProcessingMode, ConsentLike]
) -> list[str]:
    reasons: list[str] = []
    if not settings.live_stt_enabled:
        reasons.append("LIVE_STT_DISABLED_BY_DEPLOYMENT")
    if entry.status == "UNKNOWN":
        reasons.append("JURISDICTION_UNKNOWN")
    elif not entry.permits_processing:
        reasons.append("JURISDICTION_NOT_PERMITTED")
    live = [grants[m] for m in _LIVE_MODES if m in grants]
    if not live:
        reasons.append("NO_CONSENT")
    elif entry.all_party_consent_possible is not False and not any(
        c.legal_basis_config.get("all_party_basis_confirmed") is True for c in live
    ):
        reasons.append("ALL_PARTY_BASIS_NOT_CONFIRMED")
    return reasons


def _highest_mode(live: bool, transcript: bool, recording: bool) -> ProcessingMode:
    if not live:
        return ProcessingMode.METADATA_ONLY
    if recording:
        return ProcessingMode.RECORDING_RETAINED
    if transcript:
        return ProcessingMode.TRANSCRIPT_RETAINED
    return ProcessingMode.LIVE_STT_TRANSIENT


def evaluate_processing(
    settings: Settings,
    line_config: Mapping[str, Any],
    consent_records: Iterable[ConsentLike],
    jurisdiction: str | JurisdictionEntry | None,
    policy: JurisdictionPolicy | None = None,
) -> ProcessingDecision:
    """Decide what may be done with call audio. `consent_records` oldest-first."""
    table = policy or default_policy()
    entry = _resolve_entry(jurisdiction, table)
    grants = _active_consents(consent_records)
    reasons = _live_reasons(settings, entry, grants)
    if reasons:
        return ProcessingDecision(
            reasons=tuple(reasons), jurisdiction=entry.code, policy_version=table.version
        )
    transcript = any(m in grants for m in _TRANSCRIPT_MODES)
    recording = ProcessingMode.RECORDING_RETAINED in grants
    remote = bool(line_config.get("allow_remote_stt")) and any(
        c.legal_basis_config.get("remote_stt_ok") is True for c in grants.values()
    )
    out = list(_persistence_notes(transcript, recording, remote, line_config))
    return ProcessingDecision(
        allow_live_stt=True,
        allow_recording=recording,
        allow_transcript_persist=transcript,
        allow_remote_stt=remote,
        mode=_highest_mode(True, transcript, recording),
        reasons=tuple(out or ["ALLOWED_BY_CONSENT_AND_POLICY"]),
        jurisdiction=entry.code,
        policy_version=table.version,
    )


def _persistence_notes(
    transcript: bool, recording: bool, remote: bool, line_config: Mapping[str, Any]
) -> list[str]:
    notes: list[str] = []
    if not transcript:
        notes.append("TRANSCRIPT_PERSIST_NOT_CONSENTED")
    if not recording:
        notes.append("RECORDING_NOT_CONSENTED")
    if not remote:
        notes.append(
            "REMOTE_STT_NOT_PERMITTED" if line_config.get("allow_remote_stt") else "LOCAL_STT_ONLY"
        )
    return notes
