"""Submission state machine and the stricter case-lifecycle guards (PRD 19.3).

Submission lifecycle (names follow domain.enums.SubmissionState):
  PENDING (prepared) -> IN_PROGRESS (sent) -> SUBMITTED (real transport ack)
  -> AGENCY_ACKNOWLEDGED (distinguishable confirmation from the receiving body)
  IN_PROGRESS -> FAILED (definitive rejection) | UNKNOWN (ambiguous; status lookup only)
  EXTERNAL_SUBMISSION_REQUIRED -> SUBMITTED_EXTERNALLY (user-attested, never AGENCY_ACKNOWLEDGED)

SUBMITTED_EXTERNALLY is defined here because domain.enums (shared) has no such value.
Guards are added with register_guard; registration is import-time safe and idempotent.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from callshield.domain.enums import EnforceCaseState as E
from callshield.domain.enums import SubmissionState as S
from callshield.domain.state_machines import (
    _GUARDS,
    Guard,
    GuardResult,
    Machine,
    register_guard,
)

SUBMITTED_EXTERNALLY = "SUBMITTED_EXTERNALLY"

SUBMISSION_TRANSITIONS: dict[str, frozenset[str]] = {
    S.PENDING.value: frozenset({S.IN_PROGRESS.value, S.FAILED.value}),
    S.IN_PROGRESS.value: frozenset({S.SUBMITTED.value, S.FAILED.value, S.UNKNOWN.value}),
    # UNKNOWN is resolved by a status lookup that shows a real acknowledgment, or by the user
    # recording what they verified themselves (never automatic, see filing.unknown_resolution).
    S.UNKNOWN.value: frozenset({S.SUBMITTED.value, S.FAILED.value, SUBMITTED_EXTERNALLY}),
    S.SUBMITTED.value: frozenset({S.AGENCY_ACKNOWLEDGED.value}),
    S.EXTERNAL_SUBMISSION_REQUIRED.value: frozenset({SUBMITTED_EXTERNALLY}),
    S.AGENCY_ACKNOWLEDGED.value: frozenset(),
    S.FAILED.value: frozenset(),
    SUBMITTED_EXTERNALLY: frozenset(),
    S.UNSUPPORTED_DESTINATION.value: frozenset(),
}

# States that block another submission of the same case+agency+digest (ENF-018).
ACTIVE_STATES: frozenset[str] = frozenset(
    {
        S.PENDING.value,
        S.IN_PROGRESS.value,
        S.UNKNOWN.value,
        S.SUBMITTED.value,
        S.AGENCY_ACKNOWLEDGED.value,
        S.EXTERNAL_SUBMISSION_REQUIRED.value,
        SUBMITTED_EXTERNALLY,
    }
)
# States where the filing is considered made (used for supplements).
FILED_STATES: frozenset[str] = frozenset(
    {S.SUBMITTED.value, S.AGENCY_ACKNOWLEDGED.value, SUBMITTED_EXTERNALLY}
)

Context = Mapping[str, Any]


def _nonempty(ctx: Context, key: str) -> bool:
    value = ctx.get(key)
    return isinstance(value, str) and bool(value.strip())


def submission_transition_allowed(from_state: str, to_state: str, ctx: Context) -> GuardResult:
    """Table + guard check for filing_submissions.state. Fails closed."""
    if to_state not in SUBMISSION_TRANSITIONS.get(from_state, frozenset()):
        return GuardResult(False, f"{from_state} -> {to_state} is not a valid transition")
    if (
        from_state == S.UNKNOWN.value
        and to_state in {S.FAILED.value, SUBMITTED_EXTERNALLY}
        and not ctx.get("user_resolution")
    ):
        return GuardResult(False, "leaving UNKNOWN this way requires the user's own verification")
    if to_state == S.IN_PROGRESS.value and not ctx.get("approval_valid"):
        return GuardResult(False, "a valid exact-digest approval is required before sending")
    if to_state == S.SUBMITTED.value and not (
        ctx.get("transport_ack") and _nonempty(ctx, "transport_ref")
    ):
        return GuardResult(False, "SUBMITTED requires a real transport acknowledgment reference")
    if to_state == S.AGENCY_ACKNOWLEDGED.value and not ctx.get("agency_confirmation_distinct"):
        return GuardResult(
            False, "AGENCY_ACKNOWLEDGED requires a distinguishable agency confirmation"
        )
    if to_state == SUBMITTED_EXTERNALLY and not (
        ctx.get("external_submission_attested") and ctx.get("receipt_id")
    ):
        return GuardResult(
            False, "external submission must be attested by the user with a receipt row"
        )
    return GuardResult(True)


def _ack_or_attested(ctx: Context) -> GuardResult:
    if ctx.get("transport_ack") and _nonempty(ctx, "transport_ref"):
        return GuardResult(True)
    if ctx.get("external_submission_attested") and ctx.get("receipt_id"):
        return GuardResult(True)
    return GuardResult(False, "filing guard missing: transport ack or attested receipt")


def _all_true(*keys: str) -> Guard:
    def guard(ctx: Context) -> GuardResult:
        missing = [k for k in keys if not ctx.get(k)]
        return GuardResult(
            not missing, f"filing guard missing: {', '.join(missing)}" if missing else ""
        )

    guard.__name__ = "filing_guard_" + "_".join(keys)
    return guard


_CASE_GUARDS: tuple[tuple[E, E, Guard], ...] = (
    (
        E.DRAFT_READY,
        E.USER_APPROVED,
        _all_true("approved_payload_digest", "payload_digest_matches", "snapshot_digest_current"),
    ),
    (E.USER_APPROVED, E.SUBMISSION_IN_PROGRESS, _all_true("approval_valid", "submission_enabled")),
    (E.SUBMISSION_IN_PROGRESS, E.SUBMITTED, _ack_or_attested),
    (E.EXTERNAL_SUBMISSION_REQUIRED, E.SUBMITTED, _all_true("receipt_id")),
    (E.SUBMITTED, E.AGENCY_ACKNOWLEDGED, _all_true("agency_confirmation_distinct")),
)


def register_filing_guards() -> None:
    """Add this module's guards once. Safe to call repeatedly or after a module reload."""
    for from_state, to_state, guard in _CASE_GUARDS:
        existing = _GUARDS.get((Machine.ENFORCE_CASE, from_state, to_state), [])
        if any(g.__name__ == guard.__name__ for g in existing):
            continue
        register_guard(Machine.ENFORCE_CASE, from_state, to_state, guard)


register_filing_guards()
