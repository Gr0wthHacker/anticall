"""Explicit export scope and exclusion rules for filing drafts (PRD 19.10).

A draft never carries unconsented third-party audio, unrelated names, community reports
or hidden personal details. Everything included is selected by name in the scope;
everything selected but blocked is reported in `exclusions` with the reason.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from callshield.domain.enums import AuthorizationState
from callshield.filing.snapshot import ArtifactFact, CaseSnapshot, ClaimFact, IncidentFact

_PERMITTED = AuthorizationState.PERMITTED.value
_AUDIO_SOURCES = frozenset({"AUDIO_RECORDING", "AI_TRANSCRIPT", "TRANSCRIPT"})


class ExportScope(BaseModel):
    """What the user chooses to include. Absent options mean "not included"."""

    model_config = ConfigDict(extra="forbid")

    incident_ids: list[uuid.UUID] | None = None  # None = every linked, non-dismissed incident
    artifact_ids: list[uuid.UUID] = Field(default_factory=list)
    include_audio: bool = False
    include_claimed_names: bool = True
    include_opt_out_history: bool = True


@dataclass
class Selection:
    calls: list[IncidentFact] = field(default_factory=list)
    attachments: list[ArtifactFact] = field(default_factory=list)
    claimed_names: list[str] = field(default_factory=list)
    opt_out_count: int = 0
    exclusions: list[dict[str, Any]] = field(default_factory=list)


def _is_audio(artifact: ArtifactFact) -> bool:
    return artifact.source_type.upper() in _AUDIO_SOURCES or artifact.media_type.startswith(
        "audio/"
    )


def _exclusion(kind: str, item_id: uuid.UUID, reason: str) -> dict[str, Any]:
    return {"kind": kind, "id": str(item_id), "reason": reason}


def artifact_exclusion_reason(
    artifact: ArtifactFact, scope: ExportScope, constraints: dict[str, Any]
) -> str | None:
    """Return why this selected artifact may not be included, or None if it may."""
    if artifact.sharing_authorization != _PERMITTED:
        return "SHARING_NOT_AUTHORIZED"
    if artifact.provenance.get("contains_third_party_speech") and artifact.consent_id is None:
        return "THIRD_PARTY_CONTENT_WITHOUT_CONSENT"
    if _is_audio(artifact):
        if not scope.include_audio:
            return "AUDIO_NOT_IN_EXPORT_SCOPE"
        if artifact.processing_authorization != _PERMITTED or artifact.consent_id is None:
            return "UNCONSENTED_AUDIO"
        if not constraints.get("audio_allowed", False):
            return "NOT_SUPPORTED_BY_DESTINATION"
    allowed = constraints.get("allowed_media_types") or []
    if not constraints.get("attachments_accepted_via_handoff", False) or (
        allowed and artifact.media_type not in allowed
    ):
        return "NOT_SUPPORTED_BY_DESTINATION"
    return None


def _select_calls(
    snapshot: CaseSnapshot, scope: ExportScope, exclusions: list[dict[str, Any]]
) -> list[IncidentFact]:
    by_id = {i.incident_id: i for i in snapshot.incidents}
    if scope.incident_ids is None:
        return list(snapshot.incidents)
    chosen: list[IncidentFact] = []
    for incident_id in dict.fromkeys(scope.incident_ids):
        fact = by_id.get(incident_id)
        if fact is None:
            exclusions.append(_exclusion("incident", incident_id, "NOT_LINKED_TO_CASE"))
        else:
            chosen.append(fact)
    return chosen


def _select_attachments(
    snapshot: CaseSnapshot,
    scope: ExportScope,
    constraints: dict[str, Any],
    exclusions: list[dict[str, Any]],
) -> list[ArtifactFact]:
    by_id = {a.artifact_id: a for a in snapshot.artifacts}
    chosen: list[ArtifactFact] = []
    for artifact_id in dict.fromkeys(scope.artifact_ids):
        artifact = by_id.get(artifact_id)
        if artifact is None:
            exclusions.append(_exclusion("artifact", artifact_id, "NOT_IN_CASE_OR_DELETED"))
            continue
        reason = artifact_exclusion_reason(artifact, scope, constraints)
        if reason:
            exclusions.append(_exclusion("artifact", artifact_id, reason))
        else:
            chosen.append(artifact)
    return chosen


def _claimed_names(snapshot: CaseSnapshot, calls: list[IncidentFact]) -> list[str]:
    """Only names the caller stated on the selected calls. Never the user's or others'."""
    sessions = {c.call_session_id for c in calls if c.call_session_id}
    claims: list[ClaimFact] = [c for c in snapshot.claims if c.session_id in sessions]
    return [c.claimed_name for c in claims if c.claimed_name.strip()]


def select_for_draft(
    snapshot: CaseSnapshot, scope: ExportScope, constraints: dict[str, Any]
) -> Selection:
    selection = Selection()
    selection.calls = _select_calls(snapshot, scope, selection.exclusions)
    selection.attachments = _select_attachments(snapshot, scope, constraints, selection.exclusions)
    if scope.include_claimed_names:
        selection.claimed_names = _claimed_names(snapshot, selection.calls)
    if scope.include_opt_out_history:
        selection.opt_out_count = len(snapshot.opt_outs)
    unselected = len(snapshot.artifacts) - len(
        set(scope.artifact_ids) & {a.artifact_id for a in snapshot.artifacts}
    )
    if unselected:
        selection.exclusions.append(
            {"kind": "artifact", "id": None, "reason": f"NOT_SELECTED_BY_USER ({unselected})"}
        )
    return selection
