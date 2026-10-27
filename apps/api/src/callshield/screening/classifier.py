"""Rule-based first-pass classifier. Output is MODEL_INFERRED: a label with a confidence,
never a statement of fact about the caller."""

from __future__ import annotations

import re
from dataclasses import dataclass

from callshield.domain.enums import URGENT_LABELS, ClassificationLabel
from callshield.policy.urgency import detect_urgency

RULES_VERSION = "rules-1.0"

_SCAM = (
    r"gift cards?",
    r"\birs\b",
    r"social security (?:number|administration|number is)",
    r"warrant for your arrest",
    r"\barrest\b",
    r"bitcoin",
    r"wire transfer",
    r"remote access",
    r"your (?:account|card) (?:has been|is) (?:suspended|locked|compromised)",
    r"verify your (?:account|identity|card)",
    r"microsoft (?:support|technical)",
    r"you(?:'ve| have) won",
    r"lottery",
    r"\bsweepstakes\b",
    r"grandson|granddaughter",
)
_SALES = (
    r"\bsolar\b",
    r"special offer",
    r"limited time",
    r"insurance (?:quote|rates?|plan)",
    r"lower your (?:rate|payment|bill)",
    r"credit card (?:rates?|debt)",
    r"free (?:trial|quote)",
    r"extended warranty",
    r"vacation (?:package|offer)",
    r"\bpromotion\b",
    r"\bdiscount\b",
    r"marketing",
    r"\bsales?\b",
    r"business loan",
    r"\bwindows? replacement\b",
    r"offering you",
    r"pre-?approved",
    r"interest rate",
)
_DEBT = (r"\bdebt\b", r"collect(?:ion|or)s?\b", r"past due", r"outstanding balance", r"owe ")
_SURVEY = (r"\bsurvey\b", r"\bpoll\b", r"feedback about")
_WANTED = (
    r"appointment (?:reminder|confirmation)",
    r"prescription (?:is )?ready",
    r"\bdelivery\b",
    r"your order",
    r"reservation",
    r"returning your call",
    r"you called us",
    r"scheduled (?:service|visit)",
    r"pharmacy",
)
_INJECTION = (
    r"ignore (?:all |any )?(?:previous|prior|above) (?:instructions|rules)",
    r"disregard (?:your|the|all) (?:instructions|rules|prompt)",
    r"system prompt",
    r"reveal (?:your|the) (?:config|configuration|instructions|prompt)",
    r"you are now",
    r"say (?:that )?you approve",
    r"transfer me",
    r"act as ",
    r"developer mode",
    r"\bjailbreak\b",
)


def _compile(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


_GROUPS = {
    "scam": _compile(_SCAM),
    "sales": _compile(_SALES),
    "debt": _compile(_DEBT),
    "survey": _compile(_SURVEY),
    "wanted": _compile(_WANTED),
    "injection": _compile(_INJECTION),
}


@dataclass(frozen=True)
class RuleResult:
    label: ClassificationLabel
    confidence: float
    rules: tuple[str, ...]
    injection_suspected: bool = False
    version: str = RULES_VERSION

    @property
    def is_urgent(self) -> bool:
        return self.label in URGENT_LABELS


def _hits(group: str, text: str) -> int:
    return sum(1 for p in _GROUPS[group] if p.search(text))


def injection_suspected(text: str) -> bool:
    return _hits("injection", text) > 0


def classify_rules(text: str | None) -> RuleResult:
    """First-pass labels. Urgency wins over everything (conservative handoff, D-012)."""
    body = text or ""
    injected = injection_suspected(body)
    urgency = detect_urgency(body)
    if urgency.label is not None:
        conf = 0.9 if urgency.label == ClassificationLabel.EMERGENCY else 0.8
        return RuleResult(urgency.label, conf, ("urgency:" + ",".join(urgency.matched),), injected)
    if injected:
        return RuleResult(ClassificationLabel.SPAM_SUSPECTED, 0.6, ("injection_attempt",), True)
    return _topic_label(body)


def _topic_label(body: str) -> RuleResult:
    scam, sales = _hits("scam", body), _hits("sales", body)
    if scam:
        return RuleResult(
            ClassificationLabel.SCAM_SUSPECTED, min(0.6 + 0.15 * scam, 0.9), ("scam",)
        )
    if _hits("debt", body):
        return RuleResult(ClassificationLabel.DEBT_COLLECTION, 0.7, ("debt",))
    if sales:
        return RuleResult(
            ClassificationLabel.UNSOLICITED_SALES, 0.85 if sales >= 2 else 0.65, ("sales",)
        )
    if _hits("survey", body):
        return RuleResult(ClassificationLabel.SURVEY, 0.7, ("survey",))
    if _hits("wanted", body):
        return RuleResult(ClassificationLabel.INFORMATIONAL, 0.7, ("wanted",))
    return RuleResult(ClassificationLabel.UNKNOWN, 0.2, ())


def coarse_category(label: ClassificationLabel | str) -> str:
    """wanted | solicitation | scam-likely | urgent | unknown (PRD D-004..D-007 vocabulary)."""
    value = ClassificationLabel(label)
    if value in URGENT_LABELS:
        return "urgent"
    if value in (ClassificationLabel.SCAM_SUSPECTED, ClassificationLabel.SPAM_SUSPECTED):
        return "scam-likely"
    if value in (
        ClassificationLabel.UNSOLICITED_SALES,
        ClassificationLabel.DEBT_COLLECTION,
        ClassificationLabel.SURVEY,
    ):
        return "solicitation"
    if value in (ClassificationLabel.INFORMATIONAL, ClassificationLabel.TRUSTED):
        return "wanted"
    return "unknown"
