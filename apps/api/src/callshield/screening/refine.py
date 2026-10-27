"""Optional LLM refinement of the rule label, with strict validation and safe fallback.

Any failure (exception, timeout, slow response, invalid/over-broad output) yields the rule
result. The model cannot lower an urgent or confident rule label, cannot produce TRUSTED,
and never influences routing except through the label it is allowed to emit.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from callshield.domain.enums import URGENT_LABELS, ClassificationLabel
from callshield.llm.client import LLMClient, LLMError
from callshield.llm.prompts import SYSTEM_PROMPT, build_user_prompt, parse_llm_output
from callshield.screening.classifier import RuleResult, classify_rules

CONFIDENT_RULE = 0.7
MAX_LLM_CONFIDENCE = 0.9


@dataclass(frozen=True)
class ClassificationOutcome:
    label: ClassificationLabel
    confidence: float
    model_version: str
    source: str  # "rules" | "llm"
    rule: RuleResult
    fallback_reason: str | None = None  # set when the LLM was tried and not used
    injection_suspected: bool = False


def _accept_llm(rule: RuleResult, llm_label: ClassificationLabel) -> bool:
    if rule.is_urgent:
        return False  # never lower or change an urgent handoff
    if llm_label in URGENT_LABELS:
        return True  # conservative: the model may raise urgency
    return rule.confidence < CONFIDENT_RULE


def _from_rules(rule: RuleResult, reason: str | None) -> ClassificationOutcome:
    return ClassificationOutcome(
        label=rule.label,
        confidence=rule.confidence,
        model_version=rule.version,
        source="rules",
        rule=rule,
        fallback_reason=reason,
        injection_suspected=rule.injection_suspected,
    )


def classify_statement(
    text: str,
    llm: LLMClient | None,
    timeout_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> ClassificationOutcome:
    rule = classify_rules(text)
    if llm is None:
        return _from_rules(rule, None)
    if rule.injection_suspected:
        return _from_rules(rule, "INJECTION_SUSPECTED_LLM_SKIPPED")
    started = monotonic()
    try:
        raw = llm.complete(SYSTEM_PROMPT, build_user_prompt(text), timeout_seconds=timeout_seconds)
    except LLMError:
        return _from_rules(rule, "LLM_ERROR")
    except Exception:  # a model/client bug must never drop the call
        return _from_rules(rule, "LLM_UNEXPECTED_ERROR")
    if monotonic() - started > timeout_seconds:
        return _from_rules(rule, "LLM_TOO_SLOW")
    parsed = parse_llm_output(raw)
    if parsed is None:
        return _from_rules(rule, "LLM_INVALID_OUTPUT")
    if not _accept_llm(rule, parsed.label):
        return _from_rules(rule, "LLM_OUTPUT_NOT_ACCEPTED")
    return ClassificationOutcome(
        label=parsed.label,
        confidence=min(parsed.confidence, MAX_LLM_CONFIDENCE),
        model_version=f"{rule.version}+llm:{llm.name}",
        source="llm",
        rule=rule,
    )
