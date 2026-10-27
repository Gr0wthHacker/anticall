"""Deterministic post-screening policy: classification label -> routing decision.

The label (rule or model) is only an input. Transfers, hangups and spoken statements are
chosen here from user configuration; model text is never spoken (D-007, D-008, D-012).
"""

from __future__ import annotations

from dataclasses import dataclass

from callshield.domain.enums import URGENT_LABELS, ClassificationLabel, RoutingAction
from callshield.policy.config import LineRoutingConfig
from callshield.policy.routing import CallContext, RoutingDecision, ring_or_voicemail
from callshield.screening.classifier import RuleResult
from callshield.screening.refine import ClassificationOutcome
from callshield.screening.templates import removal_statement

REMOVAL_MIN_CONFIDENCE = 0.8


@dataclass(frozen=True)
class ScreeningDecision:
    routing: RoutingDecision
    say: str | None = None  # fixed template text to play before the routing op


def _obvious_sales(outcome: ClassificationOutcome) -> bool:
    """D-008 gate: rule-confirmed, high-confidence sales; never model-only."""
    rule: RuleResult = outcome.rule
    return (
        rule.label == ClassificationLabel.UNSOLICITED_SALES
        and rule.confidence >= REMOVAL_MIN_CONFIDENCE
        and outcome.label == ClassificationLabel.UNSOLICITED_SALES
        and not rule.injection_suspected
    )


def _unwanted(config: LineRoutingConfig, reason: str) -> RoutingDecision:
    if config.scam_action == "REJECT":
        return RoutingDecision(RoutingAction.REJECT, "hangup", reason)
    return RoutingDecision(RoutingAction.VOICEMAIL, "voicemail", reason)


def decide_after_screening(
    config: LineRoutingConfig, ctx: CallContext, outcome: ClassificationOutcome
) -> ScreeningDecision:
    label = outcome.label
    if label in URGENT_LABELS:
        return ScreeningDecision(ring_or_voicemail(config, ctx, "URGENT_HANDOFF"))
    if label == ClassificationLabel.INFORMATIONAL:
        return ScreeningDecision(ring_or_voicemail(config, ctx, "WANTED_CALL"))
    if label in (ClassificationLabel.SCAM_SUSPECTED, ClassificationLabel.SPAM_SUSPECTED):
        return ScreeningDecision(_unwanted(config, "SCAM_OR_SPAM_SUSPECTED"))
    if label == ClassificationLabel.UNSOLICITED_SALES:
        if config.removal_statement_enabled and _obvious_sales(outcome):
            text = removal_statement(config.removal_statement_template_id)
            hangup = RoutingDecision(RoutingAction.REJECT, "hangup", "REMOVAL_STATEMENT_DELIVERED")
            return ScreeningDecision(hangup, say=text)
        return ScreeningDecision(RoutingDecision(RoutingAction.VOICEMAIL, "voicemail", "SALES"))
    if label in (
        ClassificationLabel.DEBT_COLLECTION,
        ClassificationLabel.SURVEY,
        ClassificationLabel.OTHER,
    ):
        return ScreeningDecision(RoutingDecision(RoutingAction.VOICEMAIL, "voicemail", "SOLICITOR"))
    if config.unknown_after_screening == "VOICEMAIL":
        return ScreeningDecision(RoutingDecision(RoutingAction.VOICEMAIL, "voicemail", "UNKNOWN"))
    return ScreeningDecision(ring_or_voicemail(config, ctx, "UNKNOWN_PURPOSE_HANDOFF"))
