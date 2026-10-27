"""Dialogue layer: a model may only CHOOSE the next question intent.

It never produces words. `callshield.callback.disclosure` owns every utterance, so a
malicious or buggy model (local LLM or rules) cannot change what is said, cannot speak
before the disclosure, and is never handed a dial/schedule capability. The answering
party's text is passed as untrusted data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from callshield.callback.disclosure import QuestionIntent
from callshield.callback.extraction import ExtractedFields

_FIELD_FOR_INTENT: dict[QuestionIntent, str] = {
    QuestionIntent.COMPANY: "company_claimed",
    QuestionIntent.SELLER: "represents_seller",
    QuestionIntent.OFFER: "offer_type",
    QuestionIntent.TRANSFER: "transfer_target",
    QuestionIntent.CALLBACK_NUMBER: "numbers_mentioned",
    QuestionIntent.WEBSITE: "websites_mentioned",
}


@dataclass(frozen=True)
class DialogueView:
    asked: tuple[QuestionIntent, ...]
    missing: tuple[QuestionIntent, ...]
    turn: int
    counterparty_text: str  # UNTRUSTED


class CallbackDialogueModel(Protocol):
    """Optional attribute `endpoint: str` (if present) must pass `assert_local_only`."""

    def next_intent(self, view: DialogueView) -> QuestionIntent | None: ...


def missing_intents(fields: ExtractedFields) -> tuple[QuestionIntent, ...]:
    return tuple(i for i, name in _FIELD_FOR_INTENT.items() if not getattr(fields, name))


class RuleBasedCallbackModel:
    """Deterministic default: first still-missing intent that has not been asked."""

    def next_intent(self, view: DialogueView) -> QuestionIntent | None:
        return next((i for i in view.missing if i not in view.asked), None)
