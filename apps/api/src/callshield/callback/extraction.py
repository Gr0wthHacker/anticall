"""Local, deterministic field extraction and the in-RAM listen buffer.

Nothing here touches disk, network or logs. Answering-party text is untrusted data: it is
pattern-matched into structured fields and never interpreted as instructions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, NoReturn

from callshield.callback.hardstop import normalize_speech


class GreetingType(StrEnum):
    BARE_GREETING = "BARE_GREETING"
    BUSINESS_INTRO = "BUSINESS_INTRO"
    OTHER = "OTHER"


class Intent(StrEnum):
    """Persona-relevant intents of an answering-party utterance (rule-based)."""

    ROBOT_QUESTION = "ROBOT_QUESTION"
    WHO_ARE_YOU = "WHO_ARE_YOU"
    PERSONAL_DATA_REQUEST = "PERSONAL_DATA_REQUEST"
    INJECTION = "INJECTION"
    NONE = "NONE"


_BARE = re.compile(
    r"^\s*(?:hello|hi|hey|yes|yeah|yep|good (?:morning|afternoon|evening))\W*(?:there\W*)?$", re.I
)
_CAPS = r"([A-Z][\w&'\-]*(?: (?:[A-Z][\w&'\-]*|of|and|&)){0,4})"
_COMPANY = re.compile(
    r"(?i:thank you for calling|thanks for calling|welcome to|you(?:'ve| have) reached|"
    r"calling from|call(?:ing)? with|this is [a-z]+ (?:from|with|at)|from|at) " + _CAPS
)
_REP = re.compile(
    r"(?i:my name is|this is|you(?:'re| are) (?:speaking|talking) (?:with|to)|"
    r"\bi'm|\bi am|\bit's) ([A-Z][a-z]+(?: [A-Z][a-z]+)?)(?=[\s,.!?]|$)"
)
_SELLER = re.compile(
    r"(?i:on behalf of|representing|i represent|we represent|working for|calling for) " + _CAPS
)
_TRANSFER = re.compile(
    r"(?:transfer(?:ring)?|connect(?:ing)?|pass(?:ing)?|send(?:ing)?) (?:you|this call|the call|"
    r"your call) (?:over )?to (?:the |our )?" + r"([A-Za-z][\w&'\- ]{2,40}?)(?=[.,;!?]|$)",
    re.I,
)
_PHONE = re.compile(r"(?:\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]\d{3}[\s\-.]\d{4}")
_SITE = re.compile(
    r"\b((?:https?://)?(?:www\.)?[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.(?:com|net|org|io|co|us|biz|info)"
    r"(?:/\S*)?)",
    re.I,
)
_SPOKEN_SITE = re.compile(r"\b([a-z0-9]+) dot (com|net|org|io|co|us)\b", re.I)
_OFFERS = (
    ("extended warranty", "AUTO_WARRANTY"),
    ("car warranty", "AUTO_WARRANTY"),
    ("auto warranty", "AUTO_WARRANTY"),
    ("solar", "SOLAR"),
    ("medicare", "HEALTH_INSURANCE"),
    ("health insurance", "HEALTH_INSURANCE"),
    ("student loan", "STUDENT_LOAN"),
    ("debt", "DEBT_RELIEF"),
    ("home security", "HOME_SECURITY"),
    ("credit card", "CREDIT_CARD"),
    ("mortgage", "MORTGAGE"),
    ("timeshare", "TIMESHARE"),
    ("tax", "TAX_RELIEF"),
    ("loan", "LOAN"),
)
_INTRO_CUES = re.compile(
    r"thank you for calling|thanks for calling|how (?:can|may) i (?:help|assist)|speaking|"
    r"this is [A-Za-z]+|my name is|calling from|you(?:'ve| have) reached|welcome to",
    re.I,
)
_ROBOT = re.compile(
    r"\b(?:are|r) you (?:a |an )?(?:robot|bot|machine|computer|ai|artificial|automated|"
    r"recording|real person|human|person|real|live)\b|\bis this (?:a |an )?(?:robot|recording|bot|"
    r"machine|ai)\b|\b(?:am i|are we) (?:talking|speaking) to (?:a |an )?"
    r"(?:real |live )?(?:robot|person|human|machine|computer|bot)\b",
    re.I,
)
_WHO = re.compile(
    r"\bwho (?:are you|is this|s this|is calling|s calling|am i (?:speaking|talking) "
    r"(?:with|to))\b|\bwho(?:'s| is) (?:this|calling)\b|\bwhat(?:'s| is) this (?:about|call)\b|"
    r"\bwhy are you calling\b",
    re.I,
)
_PERSONAL = re.compile(
    r"\b(?:your|his|her|the owner s|the owners) (?:home |street |mailing )?"
    r"(?:address|phone number|number|last name|full name|surname|social|ssn|"
    r"date of birth|birthday|email|account|bank|card|credit card|password)\b|"
    r"\bwhat(?:'s| is) (?:your|his|her) (?:name|number)\b|\bgive me (?:your|his|her)\b",
    re.I,
)
_INJECTION = re.compile(
    r"ignore (?:all |any |your |previous |the |prior |above )*(?:instructions?|prompts?|rules|"
    r"directions?)|disregard|system prompt|you are now|pretend (?:to be|you)|\bact as\b|"
    r"new instructions|as an ai language model|repeat (?:your|the) (?:instructions|prompt)|"
    r"reveal your|developer mode|jailbreak|forget (?:everything|your)",
    re.I,
)


def detect_intent(text: str) -> Intent:
    """Order matters: injection first so it is never answered, even if it asks 'who are you'."""
    if _INJECTION.search(text):
        return Intent.INJECTION
    norm = normalize_speech(text)
    if _ROBOT.search(text) or _ROBOT.search(norm):
        return Intent.ROBOT_QUESTION
    if _PERSONAL.search(text) or _PERSONAL.search(norm):
        return Intent.PERSONAL_DATA_REQUEST
    if _WHO.search(text) or _WHO.search(norm):
        return Intent.WHO_ARE_YOU
    return Intent.NONE


def classify_greeting(text: str) -> GreetingType:
    if _BARE.match(text):
        return GreetingType.BARE_GREETING
    if _INTRO_CUES.search(text) or _COMPANY.search(text):
        return GreetingType.BUSINESS_INTRO
    return GreetingType.OTHER


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip(" ,.") if match else None


def extract_offer_type(text: str) -> str | None:
    lowered = text.lower()
    return next((label for needle, label in _OFFERS if needle in lowered), None)


def extract_numbers(text: str) -> list[str]:
    return [re.sub(r"\D", "", m.group(0)) for m in _PHONE.finditer(text)]


def extract_websites(text: str) -> list[str]:
    sites = [m.group(1).lower().rstrip(".,") for m in _SITE.finditer(text)]
    sites += [f"{m.group(1)}.{m.group(2)}".lower() for m in _SPOKEN_SITE.finditer(text)]
    return sites


@dataclass(frozen=True)
class ListenExtraction:
    """The ONLY things that survive the listen window."""

    rep_name: str | None
    company_claimed: str | None
    greeting_type: GreetingType


@dataclass
class ExtractedFields:
    """Answering-party values (all labeled CLAIMED when persisted)."""

    rep_name: str | None = None
    company_claimed: str | None = None
    represents_seller: str | None = None
    offer_type: str | None = None
    transfer_target: str | None = None
    numbers_mentioned: list[str] = field(default_factory=list)
    websites_mentioned: list[str] = field(default_factory=list)

    def absorb(self, text: str) -> None:
        """Merge whatever this utterance reveals; first claim wins, lists accumulate."""
        self.rep_name = self.rep_name or _first(_REP, text)
        self.company_claimed = self.company_claimed or _first(_COMPANY, text)
        self.represents_seller = self.represents_seller or _first(_SELLER, text)
        self.offer_type = self.offer_type or extract_offer_type(text)
        self.transfer_target = self.transfer_target or _first(_TRANSFER, text)
        for number in extract_numbers(text):
            if number not in self.numbers_mentioned:
                self.numbers_mentioned.append(number)
        for site in extract_websites(text):
            if site not in self.websites_mentioned:
                self.websites_mentioned.append(site)


def _forbid(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise TypeError("the listen buffer cannot be serialized or persisted")


class EphemeralListenBuffer:
    """Listen-window text, RAM only. Cleared at disclosure; has NO persistence methods.

    Content is never exposed (no getters, repr redacted, pickling/copying refused). The one
    way out is `extract()`, which returns the three fields allowed to survive the window.
    """

    __slots__ = ("_chunks",)
    __getstate__ = __reduce__ = __reduce_ex__ = __copy__ = __deepcopy__ = _forbid

    def __init__(self) -> None:
        self._chunks: list[str] | None = []

    def push(self, text: str) -> None:
        if self._chunks is None:
            raise RuntimeError("listen buffer already discarded")
        self._chunks.append(text)

    def extract(self) -> ListenExtraction:
        joined = " ".join(self._chunks or [])
        fields = ExtractedFields()
        fields.absorb(joined)
        greeting = classify_greeting(self._chunks[0]) if self._chunks else GreetingType.OTHER
        return ListenExtraction(fields.rep_name, fields.company_claimed, greeting)

    def discard(self) -> None:
        if self._chunks is not None:
            self._chunks.clear()
        self._chunks = None

    @property
    def discarded(self) -> bool:
        return self._chunks is None

    def __repr__(self) -> str:
        return "<EphemeralListenBuffer content-redacted>"
