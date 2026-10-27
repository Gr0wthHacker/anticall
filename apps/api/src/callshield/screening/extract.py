"""Extract claimed organization / person from a caller's answer. Everything extracted is a
CLAIM about identity, never verified (D-005)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_ORG = re.compile(
    r"\b(?:calling|call(?:ing)? you)?\s*(?:from|with|at|representing|on behalf of)\s+"
    r"((?:[A-Z][\w&'\-\.]*)(?:\s+(?:[A-Z][\w&'\-\.]*|of|and|&)){0,5})"
)
_PERSON = re.compile(r"\b(?:this is|my name is|i'm|i am|it's)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_CLAIM_CHARS = 120


@dataclass(frozen=True)
class ExtractedClaims:
    organization: str | None
    person: str | None
    purpose: str


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    return _CONTROL.sub("", value).strip(" .,;:")[:MAX_CLAIM_CHARS] or None


def extract_claims(text: str) -> ExtractedClaims:
    org_match = _ORG.search(text)
    person_match = _PERSON.search(text)
    return ExtractedClaims(
        organization=_clean(org_match.group(1) if org_match else None),
        person=_clean(person_match.group(1) if person_match else None),
        purpose=_CONTROL.sub(" ", text).strip()[:300],
    )
