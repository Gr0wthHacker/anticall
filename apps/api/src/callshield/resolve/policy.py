"""Deterministic outbound contact policy (PRD 8.1 critical control, R-002/R-003/R-009).

`OutboundPolicy.evaluate` is PURE: it takes plain data and returns every reason the
contact must be refused. It never accepts "a number plus a retry count": a contact needs
an authenticated owner, a verified destination with evidence, a separately created
single-use authorization, cooldown/daily-cap compliance and no concurrent attempt.
No model is consulted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from callshield.domain.enums import (
    ContactScope,
    DestinationProvenance,
    ResolutionState,
    Role,
    VerificationStatus,
)

ALLOWED_ROLES = frozenset({Role.CONSUMER_SUBSCRIBER, Role.HOUSEHOLD_ADMIN})
ALLOWED_SCOPES = frozenset({ContactScope.OUTBOUND_CALL_ONCE, ContactScope.OUTBOUND_CALL_SUPERVISED})
HARD_MAX_CALL_SECONDS = 1800


@dataclass(frozen=True)
class Reason:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reasons: tuple[Reason, ...] = ()
    max_call_seconds: int = 0
    retry_after: datetime | None = None

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(r.code for r in self.reasons)


@dataclass(frozen=True)
class DestinationFacts:
    case_id: uuid.UUID
    provenance: str
    verification_status: str
    has_evidence: bool
    is_current: bool  # not superseded by a newer destination row
    valid_e164: bool


@dataclass(frozen=True)
class AuthorizationFacts:
    case_id: uuid.UUID
    destination_id: uuid.UUID
    user_id: uuid.UUID
    scope: str
    expires_at: datetime
    consumed_at: datetime | None


@dataclass(frozen=True)
class PolicyInput:
    now: datetime
    outbound_enabled: bool
    dialer_configured: bool
    idempotency_key: str | None
    # actor + case
    actor_user_id: uuid.UUID | None
    actor_role: Role | None
    case_id: uuid.UUID
    case_owner_user_id: uuid.UUID | None
    case_state: str
    case_policy_region: str
    line_verified: bool
    # destination + authorization
    destination_id: uuid.UUID
    destination: DestinationFacts | None
    authorization_id: uuid.UUID | None
    authorization: AuthorizationFacts | None
    # rate controls (system-placed attempts only)
    last_attempt_at: datetime | None
    attempts_case_24h: int
    attempts_tenant_24h: int
    active_attempt_on_case: bool
    active_attempt_on_destination: bool
    cooldown_seconds: int
    max_attempts_per_case_per_day: int
    max_attempts_per_tenant_per_day: int
    max_call_seconds: int
    reasons_extra: tuple[Reason, ...] = field(default_factory=tuple)


def _actor_reasons(inp: PolicyInput) -> list[Reason]:
    out: list[Reason] = []
    if inp.actor_user_id is None:
        out.append(Reason("NOT_AUTHENTICATED", "An authenticated user is required."))
        return out
    if inp.actor_role not in ALLOWED_ROLES:
        out.append(Reason("ROLE_NOT_PERMITTED", "This role may not start outbound contact."))
    if inp.case_owner_user_id != inp.actor_user_id:
        out.append(Reason("NOT_CASE_OWNER", "Only the case owner may start contact."))
    return out


def _case_reasons(inp: PolicyInput) -> list[Reason]:
    out: list[Reason] = []
    if inp.case_state != ResolutionState.USER_APPROVED_CONTACT.value:
        out.append(
            Reason(
                "CASE_STATE_INVALID",
                f"Case state {inp.case_state} does not allow starting contact.",
            )
        )
    if not inp.case_policy_region.strip():
        out.append(Reason("POLICY_REGION_MISSING", "The case has no legal policy region set."))
    if not inp.line_verified:
        out.append(Reason("LINE_NOT_VERIFIED", "The protected line is not verified."))
    return out


def destination_reasons(
    inp_destination: DestinationFacts | None, case_id: uuid.UUID
) -> list[Reason]:
    """Shared by authorization creation and start-contact."""
    dest = inp_destination
    if dest is None or dest.case_id != case_id:
        return [Reason("DESTINATION_MISSING", "Destination not found on this case.")]
    out: list[Reason] = []
    if dest.provenance == DestinationProvenance.CALLER_ID_ONLY.value:
        out.append(
            Reason("DESTINATION_CALLER_ID_ONLY", "Caller-ID-only numbers can never be dialed.")
        )
    elif dest.provenance != DestinationProvenance.INDEPENDENTLY_VERIFIED.value:
        out.append(Reason("DESTINATION_UNVERIFIED", "Destination is not independently verified."))
    if dest.verification_status != VerificationStatus.VERIFIED.value:
        out.append(Reason("DESTINATION_NOT_VERIFIED_STATUS", "Destination is not marked VERIFIED."))
    if not dest.has_evidence:
        out.append(Reason("DESTINATION_NO_EVIDENCE", "No verification evidence is recorded."))
    if not dest.is_current:
        out.append(Reason("DESTINATION_SUPERSEDED", "Destination was superseded by a newer entry."))
    if not dest.valid_e164:
        out.append(Reason("DESTINATION_INVALID_NUMBER", "Destination is not a valid E.164 number."))
    return out


def _authorization_reasons(inp: PolicyInput) -> list[Reason]:
    auth = inp.authorization
    if inp.authorization_id is None or auth is None:
        return [
            Reason(
                "AUTHORIZATION_MISSING", "A separate, explicit contact authorization is required."
            )
        ]
    out: list[Reason] = []
    if (
        auth.case_id != inp.case_id
        or auth.destination_id != inp.destination_id
        or auth.user_id != inp.actor_user_id
    ):
        out.append(
            Reason(
                "AUTHORIZATION_MISMATCH",
                "Authorization does not match this case, destination and user.",
            )
        )
    if auth.scope not in {s.value for s in ALLOWED_SCOPES}:
        out.append(
            Reason("AUTHORIZATION_SCOPE_INVALID", "Authorization scope is not a call scope.")
        )
    if auth.consumed_at is not None:
        out.append(Reason("AUTHORIZATION_CONSUMED", "Authorization was already used (single use)."))
    if auth.expires_at <= inp.now:
        out.append(Reason("AUTHORIZATION_EXPIRED", "Authorization has expired."))
    return out


def _rate_reasons(inp: PolicyInput) -> tuple[list[Reason], datetime | None]:
    out: list[Reason] = []
    retry_after: datetime | None = None
    if inp.last_attempt_at is not None:
        ready = inp.last_attempt_at + timedelta(seconds=inp.cooldown_seconds)
        if inp.now < ready:
            retry_after = ready
            out.append(Reason("COOLDOWN_ACTIVE", f"Cooldown active until {ready.isoformat()}."))
    if inp.attempts_case_24h >= inp.max_attempts_per_case_per_day:
        out.append(Reason("CASE_DAILY_CAP", "Daily attempt cap for this case reached."))
    if inp.attempts_tenant_24h >= inp.max_attempts_per_tenant_per_day:
        out.append(Reason("TENANT_DAILY_CAP", "Daily attempt cap for this account reached."))
    if inp.active_attempt_on_case:
        out.append(Reason("ACTIVE_ATTEMPT_ON_CASE", "Another attempt is active on this case."))
    if inp.active_attempt_on_destination:
        out.append(
            Reason(
                "ACTIVE_ATTEMPT_ON_DESTINATION", "Another attempt is active to this destination."
            )
        )
    return out, retry_after


class OutboundPolicy:
    """Stateless evaluator. Every failing rule is reported (no short circuit)."""

    @staticmethod
    def evaluate(inp: PolicyInput) -> Decision:
        reasons: list[Reason] = []
        if not inp.outbound_enabled:
            reasons.append(Reason("OUTBOUND_DISABLED", "Outbound calling is disabled."))
        if not inp.dialer_configured:
            reasons.append(Reason("DIALER_NOT_CONFIGURED", "No telephony adapter is configured."))
        if not inp.idempotency_key:
            reasons.append(Reason("IDEMPOTENCY_KEY_MISSING", "An Idempotency-Key is required."))
        if not 0 < inp.max_call_seconds <= HARD_MAX_CALL_SECONDS:
            reasons.append(
                Reason("MAX_DURATION_INVALID", "A finite max call duration is required.")
            )
        reasons += _actor_reasons(inp)
        reasons += _case_reasons(inp)
        reasons += destination_reasons(inp.destination, inp.case_id)
        reasons += _authorization_reasons(inp)
        rate, retry_after = _rate_reasons(inp)
        reasons += rate
        reasons += inp.reasons_extra
        if reasons:
            return Decision(False, tuple(reasons), 0, retry_after)
        return Decision(True, (), min(inp.max_call_seconds, HARD_MAX_CALL_SECONDS))
