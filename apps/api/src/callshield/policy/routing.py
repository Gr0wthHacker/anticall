"""Deterministic inbound routing (D-003). Evaluated BEFORE any LLM and with no model input.

Order: loop guard -> trusted contacts (always reach the user) -> known unwanted ->
priority-ordered rules -> unknown caller default. Trusted callers are never blocked, not
even by a rule.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from callshield.core.phone import InvalidPhoneNumberError, normalize_e164
from callshield.domain.enums import RoutingAction, TrustedContactKind
from callshield.policy.config import LineRoutingConfig

RING_KINDS = frozenset(
    {TrustedContactKind.TRUSTED, TrustedContactKind.PERMITTED, TrustedContactKind.OVERRIDE}
)
RULE_ACTIONS = frozenset(
    {
        RoutingAction.ALLOW,
        RoutingAction.TRANSFER,
        RoutingAction.SCREEN,
        RoutingAction.VOICEMAIL,
        RoutingAction.REJECT,
    }
)


class RuleMatcher(BaseModel):
    """Validated matcher shape for routing_rules.matcher. Empty matchers are invalid."""

    model_config = ConfigDict(extra="forbid")

    caller_exact: list[str] = []
    caller_prefix: list[str] = []
    anonymous: bool | None = None
    attestation_in: list[str] = []
    hop_count_gte: int | None = None

    @field_validator("caller_exact")
    @classmethod
    def _normalize_exact(cls, values: list[str]) -> list[str]:
        try:
            return [normalize_e164(v) for v in values]
        except InvalidPhoneNumberError as exc:
            raise ValueError("caller_exact must be valid phone numbers") from exc

    @field_validator("caller_prefix")
    @classmethod
    def _check_prefix(cls, values: list[str]) -> list[str]:
        for v in values:
            if not v.startswith("+") or not v[1:].isdigit() or len(v) < 3:
                raise ValueError("caller_prefix must look like +<digits>")
        return values

    @model_validator(mode="after")
    def _non_empty(self) -> RuleMatcher:
        if not (
            self.caller_exact
            or self.caller_prefix
            or self.anonymous is not None
            or self.attestation_in
            or self.hop_count_gte is not None
        ):
            raise ValueError("matcher must set at least one criterion")
        return self


@dataclass(frozen=True)
class ContactEntry:
    e164: str
    kind: str


@dataclass(frozen=True)
class RuleEntry:
    id: uuid.UUID
    priority: int
    matcher: Mapping[str, Any]
    action: str


@dataclass(frozen=True)
class CallContext:
    caller: str | None  # valid E.164 or None (anonymous / unparseable)
    line_e164: str
    hop_count: int = 0
    diversions: tuple[str, ...] = ()
    attestation: Mapping[str, Any] | None = None

    @property
    def anonymous(self) -> bool:
        return self.caller is None


@dataclass(frozen=True)
class RoutingDecision:
    action: RoutingAction
    op: str  # transfer | voicemail | hangup | answer (screening)
    reason: str
    target: str | None = None
    rule_id: uuid.UUID | None = None

    @property
    def screens(self) -> bool:
        return self.action == RoutingAction.SCREEN


def ring_block_reason(config: LineRoutingConfig, ctx: CallContext) -> str | None:
    """Why the call must not be forwarded to the user's number (call-loop prevention)."""
    user = config.user_number
    if user is None:
        return "NO_USER_NUMBER"
    if user == ctx.line_e164:
        return "SELF_LOOP"
    if user in ctx.diversions or user == ctx.caller:
        return "LOOP_DETECTED"
    if ctx.hop_count >= config.max_hops:
        return "MAX_HOPS_EXCEEDED"
    return None


def ring_or_voicemail(
    config: LineRoutingConfig,
    ctx: CallContext,
    reason: str,
    action: RoutingAction = RoutingAction.TRANSFER,
    rule_id: uuid.UUID | None = None,
) -> RoutingDecision:
    blocked = ring_block_reason(config, ctx)
    if blocked is not None:
        return RoutingDecision(RoutingAction.VOICEMAIL, "voicemail", blocked, None, rule_id)
    return RoutingDecision(action, "transfer", reason, config.user_number, rule_id)


def fallback_decision(config: LineRoutingConfig, ctx: CallContext, reason: str) -> RoutingDecision:
    """Configured AI-failure fallback. Never a silent drop: HANGUP only if configured."""
    if config.ai_failure_fallback == "HANGUP":
        return RoutingDecision(RoutingAction.FALLBACK, "hangup", reason)
    if config.ai_failure_fallback == "TRANSFER":
        blocked = ring_block_reason(config, ctx)
        if blocked is None:
            return RoutingDecision(RoutingAction.FALLBACK, "transfer", reason, config.user_number)
    return RoutingDecision(RoutingAction.FALLBACK, "voicemail", reason)


def _contact_kind(contacts: Sequence[ContactEntry], caller: str | None) -> set[str]:
    if caller is None:
        return set()
    return {c.kind for c in contacts if c.e164 == caller}


def matcher_matches(raw: Mapping[str, Any], ctx: CallContext) -> bool:
    try:
        m = RuleMatcher(**dict(raw))
    except (ValidationError, TypeError):
        return False  # invalid matcher never matches (fail closed)
    checks: list[bool] = []
    if m.caller_exact:
        checks.append(ctx.caller in m.caller_exact)
    if m.caller_prefix:
        checks.append(ctx.caller is not None and ctx.caller.startswith(tuple(m.caller_prefix)))
    if m.anonymous is not None:
        checks.append(ctx.anonymous == m.anonymous)
    if m.attestation_in:
        attest = str((ctx.attestation or {}).get("attest", "NONE")).upper()
        checks.append(attest in {a.upper() for a in m.attestation_in})
    if m.hop_count_gte is not None:
        checks.append(ctx.hop_count >= m.hop_count_gte)
    return all(checks)


def _apply_rule(
    rule: RuleEntry, config: LineRoutingConfig, ctx: CallContext, screening_allowed: bool
) -> RoutingDecision | None:
    try:
        action = RoutingAction(rule.action)
    except ValueError:
        return None
    reason = "RULE_MATCH"
    if action in (RoutingAction.ALLOW, RoutingAction.TRANSFER):
        return ring_or_voicemail(config, ctx, reason, action, rule.id)
    if action == RoutingAction.SCREEN:
        if screening_allowed:
            return RoutingDecision(RoutingAction.SCREEN, "answer", reason, None, rule.id)
        return _unscreened_unknown(config, ctx, "SCREENING_NOT_PERMITTED", rule.id)
    if action == RoutingAction.REJECT:
        return RoutingDecision(RoutingAction.REJECT, "hangup", reason, None, rule.id)
    if action == RoutingAction.VOICEMAIL:
        return RoutingDecision(RoutingAction.VOICEMAIL, "voicemail", reason, None, rule.id)
    return None


def _unscreened_unknown(
    config: LineRoutingConfig, ctx: CallContext, reason: str, rule_id: uuid.UUID | None = None
) -> RoutingDecision:
    if config.unscreened_unknown_action == "RING":
        return ring_or_voicemail(config, ctx, reason, RoutingAction.TRANSFER, rule_id)
    return RoutingDecision(RoutingAction.VOICEMAIL, "voicemail", reason, None, rule_id)


def decide_route(
    config: LineRoutingConfig,
    contacts: Sequence[ContactEntry],
    rules: Sequence[RuleEntry],
    ctx: CallContext,
    screening_allowed: bool,
) -> RoutingDecision:
    """Pure function: same inputs, same decision. No LLM, no I/O."""
    kinds = _contact_kind(contacts, ctx.caller)
    if kinds & {k.value for k in RING_KINDS}:
        return ring_or_voicemail(config, ctx, "TRUSTED_CONTACT")
    if TrustedContactKind.KNOWN_UNWANTED.value in kinds:
        if config.known_unwanted_action == "REJECT":
            return RoutingDecision(RoutingAction.REJECT, "hangup", "KNOWN_UNWANTED")
        return RoutingDecision(RoutingAction.VOICEMAIL, "voicemail", "KNOWN_UNWANTED")
    for rule in sorted(rules, key=lambda r: (r.priority, str(r.id))):
        if matcher_matches(rule.matcher, ctx):
            applied = _apply_rule(rule, config, ctx, screening_allowed)
            if applied is not None:
                return applied
    return _decide_unknown(config, ctx, screening_allowed)


def _decide_unknown(
    config: LineRoutingConfig, ctx: CallContext, screening_allowed: bool
) -> RoutingDecision:
    if config.unknown_action == "SCREEN":
        if screening_allowed:
            return RoutingDecision(RoutingAction.SCREEN, "answer", "UNKNOWN_CALLER_SCREEN")
        return _unscreened_unknown(config, ctx, "SCREENING_NOT_PERMITTED")
    if config.unknown_action == "RING":
        return ring_or_voicemail(config, ctx, "UNKNOWN_CALLER_RING")
    return RoutingDecision(RoutingAction.VOICEMAIL, "voicemail", "UNKNOWN_CALLER_VOICEMAIL")
