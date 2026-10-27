"""Pure-function state machines: explicit transition tables plus guard hooks.

`can_transition` evaluates BOTH the table and the registered guards. A guarded
edge with no/insufficient context is denied (fail closed).
`is_structurally_allowed` checks the table only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from callshield.domain.enums import EnforceCaseState as E
from callshield.domain.enums import ResolutionState as R

Context = Mapping[str, Any]


class Machine(StrEnum):
    RESOLUTION = "RESOLUTION"
    ENFORCE_CASE = "ENFORCE_CASE"


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    reason: str = ""


Guard = Callable[[Context], GuardResult]

RESOLUTION_TRANSITIONS: dict[R, frozenset[R]] = {
    R.CREATED: frozenset({R.DESTINATION_REVIEW, R.CLOSED}),
    R.DESTINATION_REVIEW: frozenset({R.VERIFIED_DESTINATION, R.NEXT_STEP_REVIEW, R.CLOSED}),
    R.VERIFIED_DESTINATION: frozenset({R.USER_APPROVED_CONTACT, R.DESTINATION_REVIEW, R.CLOSED}),
    R.USER_APPROVED_CONTACT: frozenset({R.IN_PROGRESS, R.VERIFIED_DESTINATION, R.CLOSED}),
    R.IN_PROGRESS: frozenset({R.REQUEST_DELIVERED, R.REFUSED, R.INTERRUPTED}),
    R.REQUEST_DELIVERED: frozenset({R.ACKNOWLEDGED, R.UNCONFIRMED, R.REFUSED, R.INTERRUPTED}),
    R.ACKNOWLEDGED: frozenset({R.MONITORING, R.NEXT_STEP_REVIEW, R.CLOSED}),
    R.UNCONFIRMED: frozenset({R.MONITORING, R.NEXT_STEP_REVIEW, R.CLOSED}),
    R.REFUSED: frozenset({R.MONITORING, R.NEXT_STEP_REVIEW, R.CLOSED}),
    R.INTERRUPTED: frozenset({R.MONITORING, R.NEXT_STEP_REVIEW, R.CLOSED}),
    R.MONITORING: frozenset({R.POSSIBLE_RECONTACT, R.NEXT_STEP_REVIEW, R.CLOSED}),
    R.POSSIBLE_RECONTACT: frozenset({R.ATTRIBUTION_REVIEW}),
    R.ATTRIBUTION_REVIEW: frozenset({R.REOPENED, R.NOT_RELATED}),
    R.NOT_RELATED: frozenset({R.MONITORING, R.CLOSED}),
    R.REOPENED: frozenset({R.DESTINATION_REVIEW, R.NEXT_STEP_REVIEW, R.CLOSED}),
    # An additional outbound call always restarts from destination review (R-009).
    R.NEXT_STEP_REVIEW: frozenset({R.DESTINATION_REVIEW, R.MONITORING, R.CLOSED}),
    R.CLOSED: frozenset(),
}

ENFORCE_TRANSITIONS: dict[E, frozenset[E]] = {
    E.OPENED: frozenset({E.INTAKE, E.CLOSED}),
    E.INTAKE: frozenset({E.EVIDENCE_COLLECTING, E.CLOSED}),
    E.EVIDENCE_COLLECTING: frozenset({E.REVIEW_REQUIRED, E.CLOSED}),
    E.REVIEW_REQUIRED: frozenset({E.ELIGIBLE_TO_DRAFT, E.EVIDENCE_COLLECTING, E.CLOSED}),
    E.ELIGIBLE_TO_DRAFT: frozenset({E.DRAFT_READY, E.REVIEW_REQUIRED, E.CLOSED}),
    E.DRAFT_READY: frozenset({E.USER_APPROVED, E.REVIEW_REQUIRED, E.CLOSED}),
    E.USER_APPROVED: frozenset(
        {E.SUBMISSION_IN_PROGRESS, E.EXTERNAL_SUBMISSION_REQUIRED, E.DRAFT_READY, E.CLOSED}
    ),
    E.EXTERNAL_SUBMISSION_REQUIRED: frozenset({E.SUBMITTED, E.DRAFT_READY, E.CLOSED}),
    # No CLOSED edge from SUBMISSION_IN_PROGRESS: ambiguous outcomes must be resolved first.
    E.SUBMISSION_IN_PROGRESS: frozenset({E.SUBMITTED, E.SUBMISSION_FAILED}),
    E.SUBMITTED: frozenset({E.AGENCY_ACKNOWLEDGED, E.MONITORING, E.CLOSED}),
    E.SUBMISSION_FAILED: frozenset({E.DRAFT_READY, E.USER_APPROVED, E.CLOSED}),
    E.AGENCY_ACKNOWLEDGED: frozenset({E.MONITORING, E.CLOSED}),
    E.MONITORING: frozenset({E.SUPPLEMENT_PENDING, E.CLOSED}),
    E.SUPPLEMENT_PENDING: frozenset({E.REVIEW_REQUIRED, E.MONITORING, E.CLOSED}),
    E.CLOSED: frozenset(),
}

_TABLES: dict[Machine, Mapping[Any, frozenset[Any]]] = {
    Machine.RESOLUTION: RESOLUTION_TRANSITIONS,
    Machine.ENFORCE_CASE: ENFORCE_TRANSITIONS,
}

_GUARDS: dict[tuple[Machine, Any, Any], list[Guard]] = {}


def register_guard(machine: Machine, from_state: Any, to_state: Any, guard: Guard) -> None:
    """Attach an extra guard to an edge (modules may add stricter ones)."""
    _GUARDS.setdefault((machine, from_state, to_state), []).append(guard)


def _require(*keys: str) -> Guard:
    def guard(ctx: Context) -> GuardResult:
        missing = [k for k in keys if not ctx.get(k)]
        return GuardResult(not missing, f"missing: {', '.join(missing)}" if missing else "")

    return guard


def _enforce_in_progress(ctx: Context) -> GuardResult:
    if ctx.get("adapter_method") != "documented_api":
        return GuardResult(
            False, "no approved documented_api adapter; use EXTERNAL_SUBMISSION_REQUIRED"
        )
    return _require("scoped_credential")(ctx)


def _submitted(ctx: Context) -> GuardResult:
    if ctx.get("transport_ack") or ctx.get("external_submission_attested"):
        return GuardResult(True)
    return GuardResult(False, "a real transport acknowledgment or attested external submission")


register_guard(
    Machine.RESOLUTION,
    R.DESTINATION_REVIEW,
    R.VERIFIED_DESTINATION,
    _require("destination_independently_verified"),
)
register_guard(
    Machine.RESOLUTION,
    R.VERIFIED_DESTINATION,
    R.USER_APPROVED_CONTACT,
    _require("user_authorization_id"),
)
register_guard(
    Machine.RESOLUTION,
    R.USER_APPROVED_CONTACT,
    R.IN_PROGRESS,
    _require("policy_approved", "user_authorization_id", "destination_independently_verified"),
)
register_guard(
    Machine.ENFORCE_CASE,
    E.DRAFT_READY,
    E.USER_APPROVED,
    _require(
        "line_authorized",
        "evidence_snapshot_digest",
        "required_fields_complete",
        "attestation_completed",
        "sharing_confirmed",
        "destination_validated",
    ),
)
register_guard(
    Machine.ENFORCE_CASE, E.USER_APPROVED, E.SUBMISSION_IN_PROGRESS, _enforce_in_progress
)
register_guard(Machine.ENFORCE_CASE, E.SUBMISSION_IN_PROGRESS, E.SUBMITTED, _submitted)
register_guard(Machine.ENFORCE_CASE, E.EXTERNAL_SUBMISSION_REQUIRED, E.SUBMITTED, _submitted)
register_guard(
    Machine.ENFORCE_CASE, E.SUBMITTED, E.AGENCY_ACKNOWLEDGED, _require("agency_confirmation_ref")
)


@dataclass(frozen=True)
class TransitionDecision:
    allowed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


def is_structurally_allowed(machine: Machine, from_state: Any, to_state: Any) -> bool:
    return to_state in _TABLES[machine].get(from_state, frozenset())


def allowed_targets(machine: Machine, from_state: Any) -> frozenset[Any]:
    return _TABLES[machine].get(from_state, frozenset())


def evaluate_transition(
    machine: Machine, from_state: Any, to_state: Any, context: Context | None = None
) -> TransitionDecision:
    if not is_structurally_allowed(machine, from_state, to_state):
        return TransitionDecision(False, (f"{from_state} -> {to_state} is not a valid transition",))
    ctx = context or {}
    failures = [
        r.reason for g in _GUARDS.get((machine, from_state, to_state), []) if not (r := g(ctx)).ok
    ]
    return TransitionDecision(not failures, tuple(failures))


def can_transition(
    machine: Machine, from_state: Any, to_state: Any, context: Context | None = None
) -> bool:
    return evaluate_transition(machine, from_state, to_state, context).allowed
