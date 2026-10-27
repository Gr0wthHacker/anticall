"""refresh_case_dossier_snapshot_metadata + alert_user_only_for_material_changes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_get_or_404, scoped_select
from callshield.casebuilder.linking import all_link_rows, linked_incident_ids
from callshield.casebuilder.optout import attempts_for, opt_outs_for, resolution_cases_for
from callshield.core.hashing import sha256_json
from callshield.db.models.enforce import Case, EvidenceArtifact
from callshield.db.models.enforce_ext import CaseDossierSnapshot
from callshield.domain.enums import EventOrigin
from callshield.ledger import append_event

ALERT_TOPIC = "case.alert"


@dataclass
class SnapshotRefresh:
    snapshot: CaseDossierSnapshot
    changed: bool
    alerted: bool
    changes: list[str]


def compute_dossier_meta(session: Session, tenant_id: uuid.UUID, case: Case) -> dict[str, Any]:
    """Counts and orthogonal states only: no personal content, no timestamps."""
    resolution_ids = [r.id for r in resolution_cases_for(session, tenant_id, case.id)]
    artifacts = session.execute(
        select(func.count()).where(
            EvidenceArtifact.tenant_id == tenant_id,
            EvidenceArtifact.case_id == case.id,
            EvidenceArtifact.deleted_at.is_(None),
        )
    ).scalar_one()
    return {
        "state": case.state,
        "attribution_status": case.attribution_status,
        "recording_authorization": case.recording_authorization,
        "legal_assessment": case.legal_assessment,
        "case_resolution": case.case_resolution,
        "linked_incident_count": len(linked_incident_ids(session, tenant_id, case.id)),
        "link_history_count": len(all_link_rows(session, tenant_id, case.id)),
        "attempt_count": len(attempts_for(session, tenant_id, resolution_ids)),
        "opt_out_event_count": len(opt_outs_for(session, tenant_id, resolution_ids)),
        "artifact_count": artifacts,
    }


def latest_snapshot(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> CaseDossierSnapshot | None:
    stmt = (
        scoped_select(CaseDossierSnapshot, tenant_id)
        .where(CaseDossierSnapshot.case_id == case_id)
        .order_by(CaseDossierSnapshot.snapshot_no.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def refresh_dossier_snapshot(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> SnapshotRefresh:
    """Append a snapshot only when the dossier changed; alert only on that change."""
    case = scoped_get_or_404(session, Case, case_id, tenant_id)
    meta = compute_dossier_meta(session, tenant_id, case)
    digest = sha256_json(meta)
    previous = latest_snapshot(session, tenant_id, case_id)
    if previous is not None and previous.digest == digest:
        return SnapshotRefresh(previous, False, False, [])
    changes = (
        sorted(k for k in meta if previous.meta.get(k) != meta[k]) if previous else ["case_opened"]
    )
    snapshot = CaseDossierSnapshot(
        tenant_id=tenant_id,
        case_id=case_id,
        snapshot_no=(previous.snapshot_no + 1) if previous else 1,
        meta=meta,
        digest=digest,
    )
    session.add(snapshot)
    session.flush()
    payload = {
        "case_id": str(case_id),
        "snapshot_id": str(snapshot.id),
        "snapshot_no": snapshot.snapshot_no,
        "changes": changes,
    }
    _, created = append_event(
        session,
        tenant_id,
        ALERT_TOPIC,
        payload,
        EventOrigin.SYSTEM,
        f"case-alert:{case_id}:{snapshot.snapshot_no}",
    )
    return SnapshotRefresh(snapshot, True, created, changes)
