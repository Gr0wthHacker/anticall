"""Evidence sufficiency checklist (ENF-012).

A weighted documentation checklist with explicit MISSING sources. It deliberately has
no legal-validity percentage and says nothing about whether a rule was violated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_get, scoped_get_or_404, scoped_select
from callshield.casebuilder.linking import incidents_for_case
from callshield.casebuilder.matching import claimed_entity_for
from callshield.casebuilder.optout import OptOutTimeline, recompute_optout_timeline
from callshield.db.models.defend import Incident, ProtectedLine
from callshield.db.models.enforce import Case, EvidenceArtifact
from callshield.domain.enums import (
    AttributionStatus,
    EvidenceLabel,
    EvidenceOrigin,
    EvidenceType,
    LineVerificationStatus,
    RecordingAuthorization,
)

DISCLAIMER = (
    "This checklist describes which kinds of documentation are present or missing. It is not "
    "a legal assessment, does not estimate the strength of any claim, and is not legal advice."
)
PRESENT, PARTIAL, MISSING = "PRESENT", "PARTIAL", "MISSING"


@dataclass(frozen=True)
class ChecklistItem:
    key: str
    description: str
    weight: int
    status: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "description": self.description,
            "weight": self.weight,
            "status": self.status,
            "detail": self.detail,
        }


def _has(types: set[str], *kinds: EvidenceType) -> bool:
    return bool(types & {k.value for k in kinds})


def _status(ok: bool, partial: bool = False) -> str:
    return PRESENT if ok else (PARTIAL if partial else MISSING)


def _artifacts(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[EvidenceArtifact]:
    stmt = scoped_select(EvidenceArtifact, tenant_id).where(
        EvidenceArtifact.case_id == case_id, EvidenceArtifact.deleted_at.is_(None)
    )
    return list(session.execute(stmt).scalars())


def _call_items(
    incidents: list[Incident],
    types: set[str],
    line: ProtectedLine | None,
    optout: OptOutTimeline,
) -> list[ChecklistItem]:
    return [
        ChecklistItem(
            "observed_call_metadata",
            "Observed call metadata from the carrier",
            3,
            _status(EvidenceType.SIGNALING_METADATA.value in types or bool(incidents)),
        ),
        ChecklistItem(
            "multiple_incidents",
            "More than one dated incident",
            2,
            _status(len(incidents) > 1, len(incidents) == 1),
            f"{len(incidents)} linked",
        ),
        ChecklistItem(
            "line_control",
            "Protected line verified as the user's",
            2,
            _status(
                line is not None
                and line.verification_status == LineVerificationStatus.VERIFIED.value
            ),
        ),
        ChecklistItem(
            "opt_out_documented",
            "An opt-out request was delivered or recorded",
            3,
            _status(optout.first_delivered_at is not None, bool(optout.opt_out_event_ids)),
        ),
        ChecklistItem(
            "subsequent_contact",
            "Contact after the opt-out request",
            3,
            _status(bool(optout.possible_subsequent_incident_ids)),
            "possible only; attribution is not verified",
        ),
    ]


def _evidence_items(
    case: Case, types: set[str], claims: list[str], has_external: bool
) -> list[ChecklistItem]:
    attributed = case.attribution_status == AttributionStatus.CORROBORATED.value
    return [
        ChecklistItem(
            "claimed_identity_recorded",
            "What the caller said they are (claimed)",
            2,
            _status(any(claims)),
        ),
        ChecklistItem(
            "identity_independently_verified",
            "Operator identity independently verified",
            2,
            _status(has_external or attributed),
        ),
        ChecklistItem(
            "authorized_transcript_or_recording",
            "Authorized transcript or recording",
            2,
            _status(_has(types, EvidenceType.AI_TRANSCRIPT, EvidenceType.AUDIO_RECORDING)),
            f"recording authorization: {case.recording_authorization}",
        ),
        ChecklistItem(
            "recording_authorization_evaluated",
            "Recording basis evaluated",
            1,
            _status(case.recording_authorization != RecordingAuthorization.NOT_EVALUATED.value),
        ),
        ChecklistItem(
            "user_statement",
            "User statement or attestation on file",
            1,
            _status(EvidenceType.USER_STATEMENT.value in types),
        ),
        ChecklistItem(
            "written_confirmation",
            "Written business confirmation or agency receipt",
            1,
            _status(_has(types, EvidenceType.BUSINESS_CONFIRMATION, EvidenceType.AGENCY_RECEIPT)),
        ),
    ]


def build_checklist(session: Session, tenant_id: uuid.UUID, case: Case) -> list[ChecklistItem]:
    incidents = incidents_for_case(session, tenant_id, case.id)
    arts = _artifacts(session, tenant_id, case.id)
    optout = recompute_optout_timeline(session, tenant_id, case, update_case=False)
    line = scoped_get(session, ProtectedLine, case.protected_line_id, tenant_id)
    types = {a.source_type for a in arts}
    claims = [claimed_entity_for(session, tenant_id, i.call_session_id) for i in incidents]
    has_external = any(a.origin == EvidenceOrigin.EXTERNAL_DOCUMENTARY.value for a in arts)
    return [
        *_call_items(incidents, types, line, optout),
        *_evidence_items(case, types, claims, has_external),
    ]


def evidence_sufficiency(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> dict[str, Any]:
    """Checklist report: items, explicit missing sources, raw weights (not a percentage)."""
    case = scoped_get_or_404(session, Case, case_id, tenant_id)
    items = build_checklist(session, tenant_id, case)
    present = sum(i.weight for i in items if i.status == PRESENT)
    return {
        "items": [i.as_dict() for i in items],
        "missing_sources": [i.key for i in items if i.status == MISSING],
        "partial_sources": [i.key for i in items if i.status == PARTIAL],
        "present_weight": present,
        "total_weight": sum(i.weight for i in items),
        "labels_note": [label.value for label in EvidenceLabel],
        "disclaimer": DISCLAIMER,
    }
