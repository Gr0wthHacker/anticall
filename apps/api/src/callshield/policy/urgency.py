"""Conservative urgent-call detection (D-012) from caller-stated purpose.

Keyword/pattern based, deterministic. A hit never blocks anything: it only forces a human
handoff. False positives are acceptable; false negatives on severe cases are not, so the
patterns are broad. Detection is a label, not a verdict about the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from callshield.domain.enums import ClassificationLabel

# Ordered by priority: first matching group wins.
_PATTERNS: tuple[tuple[ClassificationLabel, tuple[str, ...]], ...] = (
    (
        ClassificationLabel.EMERGENCY,
        (
            r"\b911\b",
            r"\bemergency\b",
            r"\bpolice\b",
            r"\bfire (?:department|dept)\b",
            r"\bambulance\b",
            r"\blife[- ]or[- ]death\b",
            r"\bparamedic",
        ),
    ),
    (
        ClassificationLabel.URGENT_MEDICAL,
        (
            r"\bhospital\b",
            r"\bemergency room\b",
            r"\ber\b",
            r"\bsurgery\b",
            r"\bsurgeon\b",
            r"\bnurse\b",
            r"\bdoctor\b",
            r"\bclinic\b",
            r"\bmedical\b",
            r"\bpatient\b",
            r"\bicu\b",
            r"\btest results?\b",
            r"\bprescription\b.*\burgent",
        ),
    ),
    (
        ClassificationLabel.URGENT_SCHOOL,
        (
            r"\bschool\b",
            r"\bprincipal\b",
            r"\bteacher\b",
            r"\bdaycare\b",
            r"\bday care\b",
            r"\bpick(?:ing)? (?:him|her|them|your (?:child|son|daughter|kid)) up\b",
            r"\bsick at school\b",
        ),
    ),
    (
        ClassificationLabel.URGENT_FAMILY,
        (
            r"\baccident\b",
            r"\bpassed away\b",
            r"\bdied\b",
            r"\bdeath in the family\b",
            r"\byour (?:mom|mother|dad|father|husband|wife|son|daughter|brother|sister|child)\b",
            r"\bfamily (?:emergency|member)\b",
        ),
    ),
)
_COMPILED = tuple(
    (label, tuple(re.compile(p, re.IGNORECASE) for p in patterns)) for label, patterns in _PATTERNS
)


@dataclass(frozen=True)
class UrgencyResult:
    label: ClassificationLabel | None
    matched: tuple[str, ...] = ()

    @property
    def is_urgent(self) -> bool:
        return self.label is not None


def detect_urgency(text: str | None) -> UrgencyResult:
    """Return the highest-priority urgent label found in the caller-stated purpose."""
    if not text:
        return UrgencyResult(None)
    for label, patterns in _COMPILED:
        hits = tuple(m.group(0).lower() for p in patterns if (m := p.search(text)))
        if hits:
            return UrgencyResult(label, hits)
    return UrgencyResult(None)
