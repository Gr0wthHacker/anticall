"""IVR transcript parsing (R-004): menu options, utterance classification.

Everything here treats transcript text as untrusted DATA. Parsed options are shown to the
user (accessibility text) and can be matched against a user-approved path; text never
becomes an instruction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_DIGIT_WORDS = {"star": "*", "pound": "#", "hash": "#", "zero": "0"}
_KEY = r"(\d|star|pound|hash|\*|#)"
_PRESS_FOR = re.compile(
    rf"(?:press|dial)\s+{_KEY}\s+(?:for|to)\s+(.+?)(?=[,.;]?\s*(?:and\s+|or\s+)?(?:press|dial)\b|[.;]|$)",
    re.IGNORECASE,
)
_FOR_PRESS = re.compile(
    rf"for\s+(.+?),?\s*(?:press|dial)\s+{_KEY}\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MenuOption:
    digit: str
    label: str

    def as_text(self) -> str:
        """Accessibility text for display."""
        return f"Press {self.digit}: {self.label}"


def _norm_digit(raw: str) -> str:
    return _DIGIT_WORDS.get(raw.lower(), raw)


def _clean_label(label: str) -> str:
    return re.sub(r"\s+", " ", label).strip(" ,.;:").removesuffix(" and").strip()


def parse_menu(text: str) -> list[MenuOption]:
    """Extract (digit, label) options. Duplicated digits keep the first label."""
    found: list[MenuOption] = []
    for match in _PRESS_FOR.finditer(text):
        found.append(MenuOption(_norm_digit(match.group(1)), _clean_label(match.group(2))))
    if not found:
        for match in _FOR_PRESS.finditer(text):
            found.append(MenuOption(_norm_digit(match.group(2)), _clean_label(match.group(1))))
    seen: set[str] = set()
    unique: list[MenuOption] = []
    for option in found:
        if option.digit not in seen and option.label:
            seen.add(option.digit)
            unique.append(option)
    return unique


class UtteranceKind(StrEnum):
    MENU = "MENU"
    HOLD = "HOLD"
    TRANSFER = "TRANSFER"
    HUMAN = "HUMAN"
    OTHER = "OTHER"


_HOLD = re.compile(
    r"please hold|stay on the line|remain on the line|your call is (?:important|being)|"
    r"estimated wait|next available|are (?:busy|assisting)|in the queue|wait time",
    re.IGNORECASE,
)
_TRANSFER = re.compile(
    r"transferring (?:you|your call)|connecting you|while (?:i|we) (?:transfer|connect)",
    re.IGNORECASE,
)
_HUMAN = re.compile(
    r"how (?:can|may) i (?:help|assist)|this is [a-z]+ (?:speaking|with)|speaking\b|"
    r"my name is|\b(?:compliance|customer service|support) (?:desk|department)\b",
    re.IGNORECASE,
)


def classify_utterance(text: str) -> UtteranceKind:
    if parse_menu(text):
        return UtteranceKind.MENU
    if _TRANSFER.search(text):
        return UtteranceKind.TRANSFER
    if _HOLD.search(text):
        return UtteranceKind.HOLD
    if _HUMAN.search(text):
        return UtteranceKind.HUMAN
    return UtteranceKind.OTHER


def menu_signature(options: list[MenuOption]) -> str:
    return "|".join(f"{o.digit}:{o.label.lower()}" for o in options)
