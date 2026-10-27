"""Dialogue decision layer. A DialogueModel only PROPOSES ToolCalls; the controller
executes them through the bounded registry, which rejects anything off-list.

`counterparty_text` is UNTRUSTED DATA. The rule-based default ignores its content beyond
the deterministic classification. An optional LLM implementation must treat it the same
way and is never required.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from callshield.agent.acknowledgment import Classification, CounterpartyKind
from callshield.agent.templates import (
    ACK_UNCLEAR,
    CALLBACK_FOLLOWUP,
    DISCLOSE_AUTOMATED,
    END_GOODBYE,
    OUTBOUND_OPT_OUT,
)
from callshield.agent.tools import ToolCall
from callshield.domain.enums import ContactOutcome


@dataclass
class DialogueState:
    session_id: uuid.UUID
    request_spoken: bool
    spoken_template_ids: list[str] = field(default_factory=list)
    last: Classification | None = None
    counterparty_text: str = ""  # untrusted
    turn: int = 0


class DialogueModel(Protocol):
    def decide(self, state: DialogueState) -> list[ToolCall]: ...


def _speak(state: DialogueState, template_id: str) -> ToolCall:
    return ToolCall("speak_opt_out", {"session_id": state.session_id, "template_id": template_id})


def _record(state: DialogueState, code: ContactOutcome, notes: str) -> ToolCall:
    return ToolCall(
        "record_disposition", {"session_id": state.session_id, "code": str(code), "notes": notes}
    )


def _end(state: DialogueState) -> ToolCall:
    return ToolCall("end_session", {"session_id": state.session_id})


class RuleBasedDialogueModel:
    """Deterministic default. Needs no network and no LLM."""

    def decide(self, state: DialogueState) -> list[ToolCall]:
        if not state.request_spoken:
            return [_speak(state, OUTBOUND_OPT_OUT)]
        kind = state.last.primary if state.last else CounterpartyKind.UNCLEAR
        spoken = state.spoken_template_ids
        if kind is CounterpartyKind.ACKNOWLEDGED_CLAIM:
            return [
                _record(state, ContactOutcome.ACKNOWLEDGED, "counterparty claimed receipt"),
                _end(state),
            ]
        if kind is CounterpartyKind.REFUSAL:
            return [
                _record(state, ContactOutcome.REFUSED, "counterparty refused"),
                _speak(state, END_GOODBYE),
                _end(state),
            ]
        if kind is CounterpartyKind.CHANNEL_REFERRAL:
            return [
                _record(state, ContactOutcome.REFERRED_TO_CHANNEL, "referred to a channel"),
                _speak(state, ACK_UNCLEAR),
                _end(state),
            ]
        if kind is CounterpartyKind.CALL_BACK and CALLBACK_FOLLOWUP not in spoken:
            return [_speak(state, CALLBACK_FOLLOWUP)]
        if kind is CounterpartyKind.BOT_QUESTION and DISCLOSE_AUTOMATED not in spoken:
            return [_speak(state, DISCLOSE_AUTOMATED)]
        if ACK_UNCLEAR not in spoken:
            return [
                _record(state, ContactOutcome.UNRESOLVED, "no confirmation"),
                _speak(state, ACK_UNCLEAR),
            ]
        return [_end(state)]
