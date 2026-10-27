"""Shared recompute step: attribution, opt-out timeline, dossier snapshot + alert."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_get_or_404
from callshield.casebuilder.attribution import recompute_attribution
from callshield.casebuilder.config import CaseBuilderConfig
from callshield.casebuilder.optout import recompute_optout_timeline
from callshield.casebuilder.snapshot import SnapshotRefresh, refresh_dossier_snapshot
from callshield.db.models.enforce import Case
from callshield.evidence.store import ArtifactStore


def recompute_case(
    session: Session,
    store: ArtifactStore | None,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    config: CaseBuilderConfig,
) -> SnapshotRefresh:
    """Idempotent: an unchanged dossier produces no new snapshot and no alert."""
    case = scoped_get_or_404(session, Case, case_id, tenant_id)
    recompute_attribution(session, store, tenant_id, case, config)
    recompute_optout_timeline(session, tenant_id, case)
    return refresh_dossier_snapshot(session, tenant_id, case.id)
