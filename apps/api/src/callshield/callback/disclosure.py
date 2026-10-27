"""Fixed utterances. EVERY word the callback ever speaks lives in this file.

The disclosure is a versioned template stored in code, rendered with the call time and the
user's FIRST NAME only. No model, config value or request field can alter the text: the
dialogue model may only pick a `QuestionIntent` (an enum), never supply words.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

DEFAULT_TEMPLATE_ID = "v1"
RECORDING_SENTENCE = "This call is recorded."

# {recording_sentence} is replaced by RECORDING_SENTENCE or removed. A template without the
# placeholder can never carry the sentence, so outbound_recording must stay false for it.
TEMPLATES: dict[str, str] = {
    "v1": (
        "This is an automated assistant returning a call from this number at {time} "
        "on behalf of {first_name}. {recording_sentence}Can you confirm the company name "
        "and who you're calling for?"
    ),
    "v1-no-recording-notice": (
        "This is an automated assistant returning a call from this number at {time} "
        "on behalf of {first_name}. Can you confirm the company name and who you're "
        "calling for?"
    ),
}

APOLOGY = "Sorry for the trouble, goodbye"
CLOSING = "Thank you, goodbye"
ROBOT_ANSWER = "Yes, I'm an automated assistant calling on behalf of {first_name}."
WHO_ANSWER = "I'm an automated assistant returning a call on behalf of {first_name}."
DECLINE_ANSWER = "I'm only able to ask about the company that called."


class QuestionIntent(StrEnum):
    COMPANY = "company"
    SELLER = "seller_represented"
    OFFER = "offer_type"
    TRANSFER = "transfer_target"
    CALLBACK_NUMBER = "callback_number"
    WEBSITE = "website"


QUESTIONS: dict[QuestionIntent, str] = {
    QuestionIntent.COMPANY: "Which company are you calling from?",
    QuestionIntent.SELLER: "Which company or seller do you represent?",
    QuestionIntent.OFFER: "What product or service is this call about?",
    QuestionIntent.TRANSFER: "If calls get transferred, who do they go to?",
    QuestionIntent.CALLBACK_NUMBER: "What number do you use for callbacks?",
    QuestionIntent.WEBSITE: "What is your company's website?",
}


def template_has_recording_notice(template_id: str) -> bool:
    return "{recording_sentence}" in TEMPLATES.get(template_id, "")


def clean_first_name(display_name: str) -> str:
    """First name only; anything non-alphabetic is stripped, never a surname or number."""
    token = display_name.strip().split(" ")[0] if display_name.strip() else ""
    cleaned = re.sub(r"[^A-Za-z'\-]", "", token)[:30]
    return cleaned or "the owner of this number"


def render_disclosure(
    template_id: str,
    *,
    first_name: str,
    call_time: datetime,
    outbound_recording: bool,
) -> str:
    template = TEMPLATES[template_id]
    if outbound_recording and not template_has_recording_notice(template_id):
        raise ValueError("recording requires a disclosure with the recording notice")
    sentence = f"{RECORDING_SENTENCE} " if outbound_recording else ""
    return template.format(
        time=call_time.strftime("%I:%M %p").lstrip("0"),
        first_name=clean_first_name(first_name),
        recording_sentence=sentence,
    )


def allowed_utterances(disclosure: str, first_name: str) -> frozenset[str]:
    """The complete closed set of strings the session may speak for one call."""
    name = clean_first_name(first_name)
    return frozenset(
        {
            disclosure,
            APOLOGY,
            CLOSING,
            DECLINE_ANSWER,
            ROBOT_ANSWER.format(first_name=name),
            WHO_ANSWER.format(first_name=name),
            *QUESTIONS.values(),
        }
    )
