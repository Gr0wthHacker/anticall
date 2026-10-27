"""Hard-stop detection: deterministic keywords + simple intent rules (no model involved).

Checked on every answering-party utterance from the listen window onward. Output is a kind
only; the session decides the (fixed) response. Phrases are matched on a normalized form
(lowercase, curly quotes folded, punctuation dropped) and a short negation guard prevents
"this is not a wrong number" from stopping the call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class HardStopKind(StrEnum):
    WRONG_PARTY = "WRONG_PARTY"
    STOP_REQUESTED = "STOP_REQUESTED"
    THREAT_OR_DISTRESS = "THREAT_OR_DISTRESS"


@dataclass(frozen=True)
class HardStop:
    kind: HardStopKind
    rule: str


_RULES: tuple[tuple[HardStopKind, str, re.Pattern[str]], ...] = tuple(
    (kind, name, re.compile(pattern))
    for kind, name, pattern in (
        (HardStopKind.WRONG_PARTY, "wrong_number", r"\bwrong (?:number|person|party)\b"),
        (
            HardStopKind.WRONG_PARTY,
            "didnt_call",
            r"\b(?:we|i|nobody|no one|nobody here) (?:did not|didnt|never) (?:call|phone|called)\b",
        ),
        (
            HardStopKind.WRONG_PARTY,
            "nobody_called",
            r"(?:nobody|no one) (?:here )?(?:called|phoned)",
        ),
        (
            HardStopKind.WRONG_PARTY,
            "nobody_called",
            r"\b(?:nobody|no one) (?:here )?(?:called|phoned)\b",
        ),
        (
            HardStopKind.WRONG_PARTY,
            "not_right_number",
            r"\bnot the (?:right|correct) (?:number|person|place)\b",
        ),
        (
            HardStopKind.WRONG_PARTY,
            "no_such_person",
            r"\bno ?one (?:here )?(?:by|with) that name\b|"
            r"\bdont know (?:who|what) (?:youre|you are) (?:looking|talking)\b",
        ),
        (
            HardStopKind.STOP_REQUESTED,
            "stop_calling",
            r"\b(?:stop|quit|cease) (?:calling|phoning)\b",
        ),
        (HardStopKind.STOP_REQUESTED, "take_off_list", r"\btake (?:me|us|this number) off\b"),
        (
            HardStopKind.STOP_REQUESTED,
            "remove_me",
            r"\b(?:remove|delete) (?:me|us|this number|my number) from\b",
        ),
        (
            HardStopKind.STOP_REQUESTED,
            "do_not_call_me",
            r"\b(?:dont|do not|never) (?:call|contact|phone) (?:me|us|here|this number)\b",
        ),
        (HardStopKind.STOP_REQUESTED, "leave_alone", r"\bleave (?:me|us) alone\b|\bplease stop\b"),
        (
            HardStopKind.THREAT_OR_DISTRESS,
            "calling_authorities",
            r"\b(?:ill|i will|im going to|well|we will) (?:call|get|contact|report you to) "
            r"(?:the )?(?:police|cops|sheriff|fbi|lawyer|attorney|authorities)\b",
        ),
        (
            HardStopKind.THREAT_OR_DISTRESS,
            "threat",
            r"\b(?:sue you|press charges|harass(?:ing|ment)|report you|find you|hurt you|kill you|"
            r"come after you|lawsuit)\b",
        ),
        (
            HardStopKind.THREAT_OR_DISTRESS,
            "distress",
            r"\b(?:help me|im scared|i am scared|im afraid|i am afraid|this is an emergency|"
            r"heart attack|i cant breathe|im in danger|kill myself|suicide)\b",
        ),
    )
)
_CURLY = {0x2019: "'", 0x2018: "'"}
_NEGATION_TAIL = re.compile(r"\b(?:not|isnt|is not|no|never|wasnt) (?:a |the |an )?$")


def normalize_speech(text: str) -> str:
    folded = text.lower().translate(_CURLY)
    folded = folded.replace("'", "")
    folded = re.sub(r"[^a-z0-9\s]", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


class HardStopDetector:
    """Stateless, table-driven. `check` returns the first matching stop or None."""

    def check(self, text: str) -> HardStop | None:
        norm = normalize_speech(text)
        for kind, name, pattern in _RULES:
            match = pattern.search(norm)
            if match is None:
                continue
            if kind is HardStopKind.WRONG_PARTY and _NEGATION_TAIL.search(norm[: match.start()]):
                continue
            return HardStop(kind, name)
        return None
