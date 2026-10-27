"""Facts the deterministic rules evaluate: DB snapshot plus user-attested statements."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_select
from callshield.db.models.defend import CallerObservation, Classification
from callshield.db.models.enforce import Case
from callshield.domain.enums import ReviewStatus
from callshield.filing.snapshot import CaseSnapshot, load_case_snapshot

CallType = Literal["TELEMARKETING", "INFORMATIONAL", "DEBT_COLLECTION", "EXEMPT", "UNKNOWN"]
Technology = Literal["LIVE_AGENT", "PRERECORDED", "ARTIFICIAL_VOICE", "AUTODIALER", "UNKNOWN"]
ExemptionStatus = Literal["NONE_KNOWN", "POSSIBLE", "CLAIMED", "UNKNOWN"]


class UserFacts(BaseModel):
    """Statements made by the user. Always treated as USER_ATTESTED, never verified."""

    model_config = ConfigDict(extra="forbid")

    call_type: CallType | None = None
    technology: Technology | None = None
    caller_jurisdiction: str | None = Field(default=None, max_length=64)
    called_party_state: str | None = Field(default=None, max_length=64)
    dnc_registered_on: date | None = None
    entity_specific_request_on: date | None = None
    exemption_status: ExemptionStatus | None = None
    caller_id_inaccurate: bool | None = None

    def merged_over(self, base: UserFacts) -> UserFacts:
        """Non-None fields here override `base` (later statements win)."""
        data = base.model_dump()
        data.update({k: v for k, v in self.model_dump().items() if v is not None})
        return UserFacts.model_validate(data)

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


@dataclass(frozen=True)
class CaseFacts:
    snapshot: CaseSnapshot
    user: UserFacts
    confirmed_labels: frozenset[str] = field(default_factory=frozenset)
    inferred_labels: frozenset[str] = field(default_factory=frozenset)
    attestation_metadata_seen: bool = False

    @property
    def support_ids(self) -> list[str]:
        ids = [str(e) for e in self.snapshot.ledger_event_ids]
        return ids + [str(i.incident_id) for i in self.snapshot.incidents]


def _classification_labels(
    session: Session, case: Case, session_ids: list[uuid.UUID]
) -> tuple[frozenset[str], frozenset[str]]:
    if not session_ids:
        return frozenset(), frozenset()
    rows = list(
        session.execute(
            scoped_select(Classification, case.tenant_id).where(
                Classification.call_id.in_(session_ids)
            )
        ).scalars()
    )
    superseded = {r.supersedes_id for r in rows if r.supersedes_id}
    reviewed = {ReviewStatus.CONFIRMED.value, ReviewStatus.CORRECTED.value}
    live = [r for r in rows if r.id not in superseded]
    confirmed = frozenset(r.label for r in live if r.reviewed_status in reviewed)
    inferred = frozenset(r.label for r in live if r.reviewed_status not in reviewed)
    return confirmed, inferred


def _attestation_seen(session: Session, case: Case, session_ids: list[uuid.UUID]) -> bool:
    if not session_ids:
        return False
    rows = session.execute(
        scoped_select(CallerObservation, case.tenant_id).where(
            CallerObservation.session_id.in_(session_ids)
        )
    ).scalars()
    return any(bool(r.attestation_metadata) for r in rows)


def load_case_facts(session: Session, case: Case, user: UserFacts) -> CaseFacts:
    snapshot = load_case_snapshot(session, case)
    session_ids = [i.call_session_id for i in snapshot.incidents if i.call_session_id]
    confirmed, inferred = _classification_labels(session, case, session_ids)
    return CaseFacts(
        snapshot=snapshot,
        user=user,
        confirmed_labels=confirmed,
        inferred_labels=inferred,
        attestation_metadata_seen=_attestation_seen(session, case, session_ids),
    )
