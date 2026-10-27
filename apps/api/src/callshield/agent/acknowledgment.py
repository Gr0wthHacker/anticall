"""Deterministic classification of COUNTERPARTY transcript text (untrusted data).

Acknowledgment is only ever CLAIMED, and only when the counterparty's own transcript
matches these patterns. Model predictions never feed this module, incomplete segments
never match, and hedged/negated phrases never count (PRD 9.1, 5.2.1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from callshield.domain.enums import EvidenceLabel


class CounterpartyKind(StrEnum):
    ACKNOWLEDGED_CLAIM = "ACKNOWLEDGED_CLAIM"
    REFUSAL = "REFUSAL"
    CALL_BACK = "CALL_BACK"
    CHANNEL_REFERRAL = "CHANNEL_REFERRAL"
    TERMINATE_REQUEST = "TERMINATE_REQUEST"
    BOT_REFUSAL = "BOT_REFUSAL"
    SENSITIVE_REQUEST = "SENSITIVE_REQUEST"
    BOT_QUESTION = "BOT_QUESTION"
    WRONG_DESTINATION = "WRONG_DESTINATION"
    CONFLICT = "CONFLICT"
    INJECTION_ATTEMPT = "INJECTION_ATTEMPT"
    UNCLEAR = "UNCLEAR"


_I = re.IGNORECASE
_SENSITIVE_WORD = re.compile(
    r"social security|\bssn\b|credit card|card number|bank account|routing number|"
    r"\bpassword\b|\bpin\b|date of birth|maiden name|driver'?s? licen[cs]e",
    _I,
)
_REQUEST_CUE = re.compile(
    r"\bwhat(?:'s| is)\b|give me|provide|confirm|verify|read (?:me|out)|tell me|"
    r"need your|your\b|can you",
    _I,
)
_SENSITIVE_NUMBER = re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\b(?:\d[ -]?){13,16}\b")
_BOT_REFUSAL = re.compile(
    r"(?:don't|do not|won't|will not|not going to|refuse to)\s+(?:talk|speak|deal|work)\s+"
    r"(?:to|with)\s+(?:a\s+)?(?:robots?|bots?|machines?|computers?|ai|automated)|"
    r"\bno robots?\b|real person|speak (?:to|with) a human|talk to a human|get a human",
    _I,
)
_TERMINATE = re.compile(
    r"(?:end|terminate|hang up|disconnect)\s+(?:this|the)\s+call|please hang up|"
    r"hang up (?:now|the phone)",
    _I,
)
_REFUSAL = re.compile(
    r"(?:will not|won't|cannot|can't|refuse to|not going to|unable to|do not|don't)\s+"
    r"(?:\w+\s+){0,2}(?:stop|remove|add|honou?r|put)|\bwe (?:refuse|decline)\b",
    _I,
)
_CALL_BACK = re.compile(
    r"call (?:us |back )?back|try again later|call (?:again )?(?:tomorrow|later|during business)",
    _I,
)
_CHANNEL = re.compile(
    r"\be-?mail\b|mail us|write to|in writing|send (?:it|a letter)|web ?site|web form|portal",
    _I,
)
_WRITTEN_ONLY = re.compile(
    r"(?:only|must be)\s+(?:accept\s+|take\s+)?(?:requests?\s+)?(?:in writing|written)|"
    r"in writing only",
    _I,
)
_LEGAL_CLAIM = re.compile(
    r"\bby law\b|the law (?:says|requires)|under the (?:tcpa|ftc|fcc)|"
    r"legally (?:required|obligated)",
    _I,
)
_BOT_QUESTION = re.compile(
    r"are you (?:a )?(?:robot|bot|real|human|machine|ai|automated|a person)|"
    r"is this (?:a )?(?:robot|recording|machine)",
    _I,
)
_WRONG = re.compile(
    r"wrong number|you(?:'ve| have) (?:got )?the wrong|not a business|private residence|"
    r"you(?:'ve| have) reached a (?:private|residential)",
    _I,
)
_INJECTION = re.compile(
    r"ignore (?:your|all|any|previous|prior)\b|you are now\b|system prompt|new instructions|"
    r"\bdial\b|call \+?\d|say (?:you are|you're) (?:an? )?(?:attorney|lawyer|police|regulator)",
    _I,
)
_HEDGE = re.compile(
    r"can't promise|cannot promise|no guarantee|not able to confirm|unable to confirm|"
    r"can't confirm|cannot confirm|may take|up to \d+",
    _I,
)
_NEGATED_ACK = re.compile(
    r"(?:haven't|have not|hasn't|has not|not|never|didn't|did not)\s+(?:\w+\s+){0,2}"
    r"(?:record|recorded|note|noted|log|logged|receive|received|add|added)",
    _I,
)
_ACK = re.compile(
    r"\b(?:i've|i have|we've|we have|i|we)\s+(?:just\s+)?"
    r"(?:recorded|noted|logged|received|added)\b|"
    r"\b(?:request|number|that)\s+(?:has|have|is|was)\s+(?:been\s+)?"
    r"(?:recorded|received|noted|logged|added)\b|"
    r"\b(?:you(?:'re| are)|number (?:is|has been))\s+(?:now\s+)?(?:on|added to|in)\s+"
    r"(?:our|the)\s+(?:do[- ]not[- ]call|dnc)\b|"
    r"\b(?:we|i)(?:'ll| will)\s+(?:add|put|note|record)\b",
    _I,
)


@dataclass(frozen=True)
class Classification:
    primary: CounterpartyKind
    flags: frozenset[CounterpartyKind]
    label: EvidenceLabel = EvidenceLabel.CLAIMED  # counterparty statements are CLAIMED


def _flags(text: str) -> set[CounterpartyKind]:
    k = CounterpartyKind
    found: set[CounterpartyKind] = set()
    if _SENSITIVE_NUMBER.search(text) or (
        _SENSITIVE_WORD.search(text) and _REQUEST_CUE.search(text)
    ):
        found.add(k.SENSITIVE_REQUEST)
    checks = (
        (_BOT_REFUSAL, k.BOT_REFUSAL),
        (_TERMINATE, k.TERMINATE_REQUEST),
        (_REFUSAL, k.REFUSAL),
        (_CALL_BACK, k.CALL_BACK),
        (_CHANNEL, k.CHANNEL_REFERRAL),
        (_BOT_QUESTION, k.BOT_QUESTION),
        (_WRONG, k.WRONG_DESTINATION),
        (_INJECTION, k.INJECTION_ATTEMPT),
    )
    found.update(kind for pattern, kind in checks if pattern.search(text))
    if (
        _ACK.search(text)
        and not _NEGATED_ACK.search(text)
        and not _HEDGE.search(text)
        and k.REFUSAL not in found
    ):
        found.add(k.ACKNOWLEDGED_CLAIM)
    return found


_PRIORITY = (
    CounterpartyKind.INJECTION_ATTEMPT,
    CounterpartyKind.SENSITIVE_REQUEST,
    CounterpartyKind.BOT_REFUSAL,
    CounterpartyKind.TERMINATE_REQUEST,
    CounterpartyKind.WRONG_DESTINATION,
    CounterpartyKind.REFUSAL,
    CounterpartyKind.CONFLICT,
    CounterpartyKind.CHANNEL_REFERRAL,
    CounterpartyKind.CALL_BACK,
    CounterpartyKind.BOT_QUESTION,
    CounterpartyKind.ACKNOWLEDGED_CLAIM,
)


def classify_counterparty(text: str, *, complete: bool = True) -> Classification:
    """Classify ONE counterparty utterance. Incomplete segments are never interpreted."""
    if not complete:
        return Classification(CounterpartyKind.UNCLEAR, frozenset({CounterpartyKind.UNCLEAR}))
    flags = _flags(text)
    conflict = bool(_WRITTEN_ONLY.search(text) and CounterpartyKind.CALL_BACK in flags) or bool(
        _LEGAL_CLAIM.search(text)
    )
    if conflict:
        flags.add(CounterpartyKind.CONFLICT)
    primary = next((p for p in _PRIORITY if p in flags), CounterpartyKind.UNCLEAR)
    return Classification(primary, frozenset(flags or {CounterpartyKind.UNCLEAR}))


def cross_utterance_conflict(history: list[frozenset[CounterpartyKind]], text: str) -> bool:
    """Call-back in one utterance and written-only in another also conflicts."""
    saw_call_back = any(CounterpartyKind.CALL_BACK in f for f in history)
    return saw_call_back and bool(_WRITTEN_ONLY.search(text))
