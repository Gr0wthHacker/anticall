"""Case lifecycle moves for the filing flow, always through domain.state_machines."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session

from callshield.audit.service import actor_for_user, record_case_audit
from callshield.core.clock import utcnow
from callshield.core.errors import ConflictError
from callshield.core.idempotency import make_idempotency_key
from callshield.core.ids import new_id
from callshield.db.models.enforce import Case
from callshield.domain.enums import ActorType, EnforceCaseState, EventOrigin
from callshield.domain.state_machines import Machine, evaluate_transition
from callshield.domain.topics import Topic
from callshield.filing import states as _states  # noqa: F401  (registers guards on import)
from callshield.ledger.service import append_event


def case_state(case: Case) -> EnforceCaseState:
    return EnforceCaseState(case.state)


def advance_case(
    session: Session,
    case: Case,
    target: EnforceCaseState,
    user_id: uuid.UUID,
    context: Mapping[str, Any] | None = None,
    reason: str = "",
) -> None:
    """Move the case one step. Raises ConflictError if the table or any guard refuses."""
    current = case_state(case)
    decision = evaluate_transition(Machine.ENFORCE_CASE, current, target, context)
    if not decision.allowed:
        raise ConflictError(
            f"case cannot move {current.value} -> {target.value}",
            code="INVALID_CASE_TRANSITION",
            details={"from": current.value, "to": target.value, "reasons": list(decision.reasons)},
        )
    case.state = target.value
    case.updated_at = utcnow()
    record_case_audit(
        session,
        case.tenant_id,
        case.id,
        ActorType.USER,
        "case.state_changed",
        {"from": current.value, "to": target.value, "reason": reason},
        actor_id=actor_for_user(user_id),
    )
    append_event(
        session,
        case.tenant_id,
        Topic.CASE_STATE_CHANGED,
        {"case_id": str(case.id), "from": current.value, "to": target.value},
        EventOrigin.USER_ACTION,
        make_idempotency_key("case-state", case.id, current.value, target.value, new_id()),
    )


def advance_path(
    session: Session,
    case: Case,
    path: Sequence[EnforceCaseState],
    user_id: uuid.UUID,
    reason: str = "",
) -> None:
    """Walk several unguarded steps in order (e.g. back to DRAFT_READY for a supplement)."""
    for target in path:
        advance_case(session, case, target, user_id, reason=reason)
