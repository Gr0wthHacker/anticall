"""Callback eligibility gates (spec "Callback eligibility gates"): PURE, fail closed.

`evaluate_gates` takes plain facts (no DB, no clock, no network) and reports every gate with
a machine-readable reason and a user-facing message. Any missing/None fact fails its gate.
It runs at queue time AND again inside the dial service immediately before dialing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from enum import StrEnum
from typing import Any

from callshield.callback.config import (
    CEIL_PER_INBOUND,
    CEIL_PER_NUMBER_30D,
    CEIL_PER_USER_DAY,
    INSTANCE_MAX_PER_NUMBER_30D,
    MIN_MINUTES_BETWEEN_ATTEMPTS,
)

CALLING_START_HOUR = 8
CALLING_END_HOUR = 21
_CLOCK_SKEW = timedelta(minutes=5)


class GateName(StrEnum):
    INBOUND_RECORD = "INBOUND_RECORD"
    ATTESTATION_A = "ATTESTATION_A"
    WITHIN_WINDOW = "WITHIN_WINDOW"
    USER_AUTHORIZATION = "USER_AUTHORIZATION"
    NOT_BLOCKLISTED = "NOT_BLOCKLISTED"
    CAPS = "CAPS"
    CALLING_HOURS = "CALLING_HOURS"


class AuthorizationMode(StrEnum):
    ENFORCE = "ENFORCE"  # a real, unexpired, unconsumed authorization must exist
    PREVIEW = "PREVIEW"  # eligibility check before the tap: the tap will supply gate 4


@dataclass(frozen=True)
class AuthorizationFacts:
    matches_user_and_call: bool
    expires_at: datetime
    consumed: bool


@dataclass(frozen=True)
class CapsFacts:
    inbound_attempts: int
    number_attempts_30d: int
    user_attempts_24h: int
    last_user_attempt_at: datetime | None
    instance_attempts_30d: int


@dataclass(frozen=True)
class CapLimits:
    per_inbound: int
    per_number_30d: int
    per_user_day: int


@dataclass(frozen=True)
class GateInputs:
    now: datetime
    inbound_found: bool = False
    destination_present: bool = False
    inbound_at: datetime | None = None
    attestation_level: str | None = None
    attestation_stripped: bool = False
    window_hours: int | None = None
    authorization: AuthorizationFacts | None = None
    blocked_by_user: bool | None = None
    blocked_by_instance: bool | None = None
    caps: CapsFacts | None = None
    limits: CapLimits | None = None
    user_tz: tzinfo | None = None
    destination_geography_provided: bool = False
    destination_tz: tzinfo | None = None
    authorization_mode: AuthorizationMode = AuthorizationMode.ENFORCE


@dataclass(frozen=True)
class GateResult:
    gate: GateName
    passed: bool
    reason: str
    message: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate.value,
            "passed": self.passed,
            "reason": self.reason,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class GateReport:
    results: tuple[GateResult, ...]

    @property
    def allowed(self) -> bool:
        return len(self.results) == len(GateName) and all(r.passed for r in self.results)

    @property
    def failures(self) -> tuple[GateResult, ...]:
        return tuple(r for r in self.results if not r.passed)

    @property
    def complaint_flow_only(self) -> bool:
        return any(r.gate is GateName.ATTESTATION_A and not r.passed for r in self.results)

    def reason_codes(self) -> list[str]:
        return [r.reason for r in self.failures]

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "complaint_flow_offered": not self.allowed,
            "complaint_flow_only": self.complaint_flow_only,
            "blocked_by": [r.gate.value for r in self.failures],
            "gates": [r.as_dict() for r in self.results],
        }


def _ok(gate: GateName, message: str = "OK", reason: str = "OK") -> GateResult:
    return GateResult(gate, True, reason, message, {})


def _no(gate: GateName, reason: str, message: str, **details: Any) -> GateResult:
    return GateResult(gate, False, reason, message, details)


def _inbound(i: GateInputs) -> GateResult:
    g = GateName.INBOUND_RECORD
    if not i.inbound_found:
        return _no(g, "INBOUND_NOT_FOUND", "We can only call back a number that called you.")
    if not i.destination_present:
        return _no(g, "NO_CALLER_ID", "That call did not show a caller ID to call back.")
    return _ok(g)


def _attestation(i: GateInputs) -> GateResult:
    g = GateName.ATTESTATION_A
    level = (i.attestation_level or "").strip().upper()
    if i.attestation_stripped or level not in {"A", "B", "C"}:
        return _no(
            g,
            "ATTESTATION_MISSING",
            "The caller's authenticity could not be verified (no STIR/SHAKEN attestation). "
            "File a complaint instead.",
        )
    if level != "A":
        return _no(
            g,
            "ATTESTATION_NOT_A",
            "The call did not carry full (A) caller authentication. File a complaint instead.",
            level=level,
        )
    return _ok(g)


def _window(i: GateInputs) -> GateResult:
    g = GateName.WITHIN_WINDOW
    if i.inbound_at is None or i.window_hours is None:
        return _no(g, "WINDOW_UNKNOWN", "We cannot tell when that call happened.")
    age = i.now - i.inbound_at
    if age < -_CLOCK_SKEW:
        return _no(g, "INBOUND_TIME_INVALID", "That call's time looks wrong.")
    if age > timedelta(hours=i.window_hours):
        return _no(
            g,
            "OUTSIDE_WINDOW",
            f"That call is older than your {i.window_hours}-hour callback window.",
            window_hours=i.window_hours,
        )
    return _ok(g)


def _authorization(i: GateInputs) -> GateResult:
    g = GateName.USER_AUTHORIZATION
    if i.authorization_mode is AuthorizationMode.PREVIEW:
        return _ok(g, "Your tap authorizes this one call.", "AWAITING_TAP")
    auth = i.authorization
    if auth is None:
        return _no(g, "AUTHORIZATION_MISSING", "Tap to authorize this callback.")
    if not auth.matches_user_and_call:
        return _no(g, "AUTHORIZATION_MISMATCH", "That authorization is for a different call.")
    if auth.consumed:
        return _no(g, "AUTHORIZATION_USED", "That authorization was already used.")
    if auth.expires_at <= i.now:
        return _no(g, "AUTHORIZATION_EXPIRED", "Your authorization expired. Tap again.")
    return _ok(g)


def _blocklist(i: GateInputs) -> GateResult:
    g = GateName.NOT_BLOCKLISTED
    if i.blocked_by_user is None or i.blocked_by_instance is None:
        return _no(g, "BLOCKLIST_UNKNOWN", "We could not check the callback blocklist.")
    if i.blocked_by_instance:
        return _no(g, "BLOCKED_INSTANCE", "This number asked not to be called again.")
    if i.blocked_by_user:
        return _no(g, "BLOCKED_USER", "You blocked callbacks to this number.")
    return _ok(g)


def cap_failure(c: CapsFacts, lim: CapLimits, now: datetime) -> tuple[str, str] | None:
    if c.inbound_attempts >= min(lim.per_inbound, CEIL_PER_INBOUND):
        return "CAP_PER_INBOUND_CALL", "The callback limit for this call has been reached."
    if c.number_attempts_30d >= min(lim.per_number_30d, CEIL_PER_NUMBER_30D):
        return "CAP_PER_NUMBER_30D", "You have already called this number back twice this month."
    if c.user_attempts_24h >= min(lim.per_user_day, CEIL_PER_USER_DAY):
        return "CAP_PER_USER_DAY", "You have reached today's callback limit."
    spacing = timedelta(minutes=MIN_MINUTES_BETWEEN_ATTEMPTS)
    if c.last_user_attempt_at is not None and now - c.last_user_attempt_at < spacing:
        return "CAP_MIN_SPACING", "Please wait 10 minutes between callbacks."
    if c.instance_attempts_30d >= INSTANCE_MAX_PER_NUMBER_30D:
        return "CAP_INSTANCE_30D", "This number has been called back too often this month."
    return None


def _caps(i: GateInputs) -> GateResult:
    g = GateName.CAPS
    if i.caps is None or i.limits is None:
        return _no(g, "CAPS_UNKNOWN", "We could not check callback limits.")
    failure = cap_failure(i.caps, i.limits, i.now)
    return _no(g, *failure) if failure else _ok(g)


def _in_hours(now: datetime, tz: tzinfo) -> bool:
    return CALLING_START_HOUR <= now.astimezone(tz).hour < CALLING_END_HOUR


def _hours(i: GateInputs) -> GateResult:
    g = GateName.CALLING_HOURS
    if i.user_tz is None:
        return _no(g, "USER_TIMEZONE_UNKNOWN", "Set your time zone to enable callbacks.")
    if not _in_hours(i.now, i.user_tz):
        return _no(g, "OUTSIDE_USER_HOURS", "Callbacks are only placed between 8am and 9pm.")
    if i.destination_geography_provided:
        if i.destination_tz is None:
            return _no(g, "DESTINATION_TIMEZONE_INVALID", "We cannot confirm their local time.")
        if not _in_hours(i.now, i.destination_tz):
            return _no(
                g, "OUTSIDE_DESTINATION_HOURS", "It is outside calling hours where they are."
            )
    return _ok(g)


def evaluate_gates(inputs: GateInputs) -> GateReport:
    """Evaluate all seven gates (no short-circuit, so the user sees every blocker)."""
    return GateReport(
        (
            _inbound(inputs),
            _attestation(inputs),
            _window(inputs),
            _authorization(inputs),
            _blocklist(inputs),
            _caps(inputs),
            _hours(inputs),
        )
    )
