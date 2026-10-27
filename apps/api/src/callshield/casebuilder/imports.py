"""Import of historic user-attested accounts (PRD 19.11).

Imported accounts are USER_ATTESTED, stay separate from software-observed events, and
never receive invented recordings, carrier metadata, dates, identities or confirmations.
`collected_at` is when the user told us, not when the event happened; any event date
the user supplied is kept only in provenance as stated.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_get_or_404
from callshield.core.config import Settings
from callshield.db.models.enforce import Case, EvidenceArtifact
from callshield.domain.enums import (
    AuthorizationState,
    EvidenceLabel,
    EvidenceOrigin,
    EvidenceType,
)
from callshield.evidence.ingest import ArtifactInput, ingest_artifact
from callshield.evidence.store import ArtifactStore

IMPORT_DISCLAIMER = (
    "Account imported from the user's recollection. No recording, carrier metadata, "
    "date, identity or confirmation was generated or inferred by the system."
)


@dataclass(frozen=True)
class UserAccount:
    narrative: str
    stated_event_date: date | None = None
    approximate_count: int | None = None
    category: str = "OTHER"  # e.g. RETURN_CALL_ATTEMPT, INBOUND_CALLS, OPT_OUT_REQUEST


def import_user_accounts(
    session: Session,
    store: ArtifactStore,
    settings: Settings,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    accounts: list[UserAccount],
    actor: str,
) -> list[EvidenceArtifact]:
    """Store each account as its own USER_STATED / USER_ATTESTED artifact."""
    scoped_get_or_404(session, Case, case_id, tenant_id)
    created: list[EvidenceArtifact] = []
    for account in accounts:
        body = {
            "narrative": account.narrative,
            "category": account.category,
            "approximate_count": account.approximate_count,
        }
        created.append(
            ingest_artifact(
                session,
                store,
                settings,
                tenant_id,
                ArtifactInput(
                    data=json.dumps(body, sort_keys=True).encode("utf-8"),
                    source_type=EvidenceType.USER_STATEMENT,
                    origin=EvidenceOrigin.USER_STATED,
                    evidence_label=EvidenceLabel.USER_ATTESTED,
                    media_type="application/json",
                    collection_method="user_import",
                    processing_authorization=AuthorizationState.PERMITTED,
                    provenance={
                        "imported": True,
                        "no_observed_metadata": True,
                        "stated_event_date": account.stated_event_date.isoformat()
                        if account.stated_event_date
                        else None,
                        "date_basis": "user_stated" if account.stated_event_date else "unknown",
                        "disclaimer": IMPORT_DISCLAIMER,
                    },
                    case_id=case_id,
                ),
                actor,
            )
        )
    return created
