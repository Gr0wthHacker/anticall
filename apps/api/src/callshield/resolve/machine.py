"""Resolution state-machine helpers: validated transitions + shortest safe routes.

All moves go through `domain.state_machines.evaluate_transition` (table + guards, fail
closed). There is intentionally no suppressed-in-sender-system state: ACKNOWLEDGED is a
claim, not suppression.
"""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from callshield.core.clock import utcnow
from callshield.core.errors import ConflictError
from callshield.db.models.resolve import ResolutionCase
from callshield.domain.enums import ResolutionState as R
from callshield.domain.state_machines import (
    RESOLUTION_TRANSITIONS,
    Machine,
    evaluate_transition,
)
from callshield.domain.topics import Topic
from callshield.resolve.events import add_timeline, emit

# States we may pass THROUGH when preparing a (re)contact. Active/terminal states never.
_ROUTE_THROUGH = frozenset({R.NEXT_STEP_REVIEW, R.DESTINATION_REVIEW, R.VERIFIED_DESTINATION})
_ROUTE_SOURCES = frozenset(
    {R.CREATED, R.ACKNOWLEDGED, R.UNCONFIRMED, R.REFUSED, R.INTERRUPTED, R.MONITORING, R.REOPENED}
)


def current_state(case: ResolutionCase) -> R:
    return R(case.status)


def route(source: R, goal: R) -> list[R]:
    """Shortest structural path source -> goal through the whitelisted intermediate states."""
    if source == goal:
        return []
    queue: deque[tuple[R, list[R]]] = deque([(source, [])])
    seen = {source}
    while queue:
        state, path = queue.popleft()
        for nxt in sorted(RESOLUTION_TRANSITIONS[state], key=str):
            if nxt in seen:
                continue
            if nxt == goal:
                return [*path, nxt]
            if nxt in _ROUTE_THROUGH:
                seen.add(nxt)
                queue.append((nxt, [*path, nxt]))
    return []


def transition(
    session: Session,
    case: ResolutionCase,
    to_state: R,
    *,
    actor: str,
    context: Mapping[str, Any] | None = None,
    attempt_id: uuid.UUID | None = None,
    note: str = "",
) -> None:
    """Move the case or raise ConflictError (guards fail closed)."""
    from_state = current_state(case)
    decision = evaluate_transition(Machine.RESOLUTION, from_state, to_state, context)
    if not decision.allowed:
        raise ConflictError(
            f"cannot move case from {from_state} to {to_state}",
            code="INVALID_STATE_TRANSITION",
            details={"reasons": list(decision.reasons)},
        )
    case.status = to_state.value
    case.updated_at = utcnow()
    data = {"from": str(from_state), "to": str(to_state), "note": note}
    add_timeline(session, case.tenant_id, case.id, "case.state_changed", actor, data, attempt_id)
    emit(
        session,
        case.tenant_id,
        Topic.CASE_STATE_CHANGED,
        {**data, "case_id": str(case.id)},
        ("case.state_changed", case.id, from_state, to_state, uuid.uuid4().hex[:12]),
    )


def advance_to(
    session: Session,
    case: ResolutionCase,
    goal: R,
    *,
    actor: str,
    context: Mapping[str, Any],
) -> None:
    """Walk the case to `goal` along a safe route, or raise ConflictError."""
    source = current_state(case)
    if source == goal:
        return
    if source not in _ROUTE_SOURCES and source not in _ROUTE_THROUGH:
        raise ConflictError(
            f"case in state {source} cannot be prepared for contact", code="INVALID_STATE"
        )
    steps = route(source, goal)
    if not steps:
        raise ConflictError(f"no safe route from {source} to {goal}", code="INVALID_STATE")
    for step in steps:
        transition(session, case, step, actor=actor, context=context)
