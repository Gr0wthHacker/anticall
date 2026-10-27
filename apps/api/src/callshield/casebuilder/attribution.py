"""Attribution gate (ENF-006, 19.13-3).

A spoofable displayed number never establishes seller identity or a verified
repeat-contact. Attribution moves only through several INDEPENDENT features
(claimed entity statements, script similarity, timing, user confirmation), and
CORROBORATED additionally requires an explicit human review. The pipeline itself can
never produce CORROBORATED.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from sqlalchemy.orm import Session

from callshield.audit import record_case_audit
from callshield.auth.scoping import scoped_get_or_404, scoped_select
from callshield.casebuilder.config import CaseBuilderConfig
from callshield.casebuilder.linking import current_links, incidents_for_case
from callshield.casebuilder.matching import claimed_entity_for
from callshield.core.errors import UnprocessableError
from callshield.db.models.defend import CallSession
from callshield.db.models.enforce import Case, EvidenceArtifact
from callshield.domain.enums import (
    ActorType,
    AttributionStatus,
    EventOrigin,
    EvidenceType,
)
from callshield.domain.topics import Topic
from callshield.evidence.access import latest_in_chain
from callshield.evidence.store import ArtifactStore, ObjectNotFoundError
from callshield.ledger import append_event

_TOKEN = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True)
class AttributionFeatures:
    same_displayed_cli: bool = False  # spoofable: context only, NOT an independent feature
    claimed_entity_match: bool = False
    script_similarity: float | None = None
    timing_pattern_match: bool = False
    user_confirmed: bool = False


@dataclass(frozen=True)
class AttributionDecision:
    status: AttributionStatus
    independent_features: tuple[str, ...]
    requires_human_review: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


def independent_features(features: AttributionFeatures, similarity_threshold: float) -> list[str]:
    found: list[str] = []
    if features.claimed_entity_match:
        found.append("claimed_entity_statements")
    if (
        features.script_similarity is not None
        and features.script_similarity >= similarity_threshold
    ):
        found.append("script_similarity")
    if features.timing_pattern_match:
        found.append("timing_pattern")
    if features.user_confirmed:
        found.append("user_confirmation")
    return found


def evaluate_attribution(
    features: AttributionFeatures,
    config: CaseBuilderConfig,
    human_reviewed: bool = False,
) -> AttributionDecision:
    """Pure decision. `human_reviewed` can only be set by review_attribution()."""
    found = independent_features(features, config.script_similarity_threshold)
    enough = len(found) >= config.min_independent_features
    if features.same_displayed_cli and not found:
        reasons = ("displayed number only; a displayed number can be spoofed",)
        return AttributionDecision(AttributionStatus.CANDIDATE, (), False, reasons)
    if not found:
        return AttributionDecision(AttributionStatus.UNKNOWN, (), False, ("no features",))
    if enough and human_reviewed:
        return AttributionDecision(AttributionStatus.CORROBORATED, tuple(found), False, ())
    if enough:
        return AttributionDecision(
            AttributionStatus.CANDIDATE, tuple(found), True, ("human review required",)
        )
    return AttributionDecision(
        AttributionStatus.CANDIDATE, tuple(found), False, ("fewer than required features",)
    )


def repeat_contact_verified(case_status: str) -> bool:
    """Verified repeat contact exists only after human-reviewed corroboration."""
    return case_status == AttributionStatus.CORROBORATED.value


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def max_pairwise_similarity(texts: list[str]) -> float | None:
    """Highest Jaccard similarity between any two scripts; None with fewer than two."""
    sets = [_tokens(t) for t in texts if t.strip()]
    if len(sets) < 2:
        return None
    return max(len(a & b) / len(a | b) for a, b in combinations(sets, 2) if a | b)


def _transcript_texts(
    session: Session, store: ArtifactStore | None, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[str]:
    if store is None:
        return []
    stmt = scoped_select(EvidenceArtifact, tenant_id).where(
        EvidenceArtifact.case_id == case_id,
        EvidenceArtifact.source_type == EvidenceType.AI_TRANSCRIPT.value,
        EvidenceArtifact.deleted_at.is_(None),
        EvidenceArtifact.supersedes_id.is_(None),
    )
    texts: list[str] = []
    for root in session.execute(stmt).scalars():
        newest = latest_in_chain(session, tenant_id, root)
        if newest.storage_key is None or newest.processing_authorization != "PERMITTED":
            continue
        try:
            texts.append(store.get(newest.storage_key).decode("utf-8", errors="replace"))
        except ObjectNotFoundError:
            continue
    return texts


def _timing_match(starts: list[Any], config: CaseBuilderConfig) -> bool:
    hours = sorted(s.hour + s.minute / 60 for s in starts if s is not None)
    window = config.timing_window_hours
    for i in range(len(hours)):
        inside = [h for h in hours if abs(h - hours[i]) <= window]
        if len(inside) >= config.timing_min_calls:
            return True
    return False


def collect_features(
    session: Session,
    store: ArtifactStore | None,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    config: CaseBuilderConfig,
) -> AttributionFeatures:
    incidents = incidents_for_case(session, tenant_id, case_id)
    clis = [i.displayed_caller_id for i in incidents if i.displayed_caller_id]
    entities = [claimed_entity_for(session, tenant_id, i.call_session_id) for i in incidents]
    entities = [e for e in entities if e]
    starts = []
    for incident in incidents:
        call = (
            scoped_get_or_404(session, CallSession, incident.call_session_id, tenant_id)
            if incident.call_session_id
            else None
        )
        starts.append(call.started_at if call and call.started_at else incident.occurred_at)
    confirmed = any(
        row.evidence_basis.get("user_confirms_same_operator") is True
        for row in current_links(session, tenant_id, case_id).values()
        if row.linkage_type in ("USER_LINK", "REVIEWER_LINK")
    )
    return AttributionFeatures(
        same_displayed_cli=len(clis) != len(set(clis)),
        claimed_entity_match=len(entities) != len(set(entities)),
        script_similarity=max_pairwise_similarity(
            _transcript_texts(session, store, tenant_id, case_id)
        ),
        timing_pattern_match=_timing_match(starts, config),
        user_confirmed=confirmed,
    )


def recompute_attribution(
    session: Session,
    store: ArtifactStore | None,
    tenant_id: uuid.UUID,
    case: Case,
    config: CaseBuilderConfig,
) -> AttributionDecision:
    """Pipeline step. Moves UNKNOWN<->CANDIDATE only; human-set states are preserved
    except that a CORROBORATED case whose support disappeared is demoted for re-review."""
    features = collect_features(session, store, tenant_id, case.id, config)
    decision = evaluate_attribution(features, config)
    if case.attribution_status == AttributionStatus.DISPUTED.value:
        return decision
    if case.attribution_status == AttributionStatus.CORROBORATED.value:
        if len(decision.independent_features) >= config.min_independent_features:
            return decision
        _set_status(session, tenant_id, case, AttributionStatus.CANDIDATE, "system", "support_lost")
        return decision
    if decision.status.value != case.attribution_status:
        _set_status(session, tenant_id, case, decision.status, "system", "pipeline_recompute")
    return decision


def _set_status(
    session: Session,
    tenant_id: uuid.UUID,
    case: Case,
    status: AttributionStatus,
    actor: str,
    why: str,
) -> None:
    previous = case.attribution_status
    case.attribution_status = status.value
    payload = {"case_id": str(case.id), "from": previous, "to": status.value, "why": why}
    record_case_audit(
        session,
        tenant_id,
        case.id,
        ActorType.USER if actor.startswith("user:") else ActorType.SYSTEM,
        "attribution.changed",
        payload,
        actor_id=actor,
    )
    append_event(
        session,
        tenant_id,
        Topic.CORRECTION_RECORDED,
        payload,
        EventOrigin.USER_ACTION if actor.startswith("user:") else EventOrigin.SYSTEM,
        f"attribution:{case.id}:{previous}:{status.value}:{case.review_version}:{why}",
    )


def review_attribution(
    session: Session,
    store: ArtifactStore | None,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    status: AttributionStatus,
    basis: str,
    actor: str,
    config: CaseBuilderConfig,
) -> Case:
    """Human review. CORROBORATED needs >= N independent features beyond the number."""
    case = scoped_get_or_404(session, Case, case_id, tenant_id)
    if status is AttributionStatus.CORROBORATED:
        features = collect_features(session, store, tenant_id, case_id, config)
        decision = evaluate_attribution(features, config, human_reviewed=True)
        if decision.status is not AttributionStatus.CORROBORATED:
            raise UnprocessableError(
                "corroboration needs multiple independent features besides the displayed number",
                code="INSUFFICIENT_INDEPENDENT_FEATURES",
                details={"found": list(decision.independent_features)},
            )
    _set_status(session, tenant_id, case, status, actor, f"human_review:{basis[:80]}")
    return case
