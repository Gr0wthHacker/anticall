"""Conversation controller: IVR navigation, then (optionally) the bounded agent.

Safety properties enforced HERE, independent of any DialogueModel:
- stop conditions (R-013) are deterministic and end the session before the model is asked;
- acknowledgment is recorded only from the counterparty's complete transcript text after
  the request was spoken;
- the model's output is a list of ToolCalls executed through the bounded registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from callshield.agent.acknowledgment import (
    Classification,
    CounterpartyKind,
    classify_counterparty,
    cross_utterance_conflict,
)
from callshield.agent.context import AgentSessionContext, ClaimRecord, TranscriptEntry
from callshield.agent.dialogue import DialogueModel, DialogueState
from callshield.agent.templates import END_GOODBYE
from callshield.agent.tools import AgentToolRegistry, ToolCall
from callshield.domain.enums import EvidenceLabel
from callshield.ivr.navigator import IvrNavigator, NavAction
from callshield.resolve.dialer import CallEvent, CallEventKind

MAX_AGENT_TURNS = 8
MAX_EVENTS = 200


class StopCondition(StrEnum):
    """R-013."""

    COUNTERPARTY_REFUSES_BOT = "COUNTERPARTY_REFUSES_BOT"
    COUNTERPARTY_TERMINATE = "COUNTERPARTY_TERMINATE"
    SENSITIVE_INFO = "SENSITIVE_INFO"
    DESTINATION_CHANGED = "DESTINATION_CHANGED"
    CONSENT_FAILURE = "CONSENT_FAILURE"
    USER_CANCEL = "USER_CANCEL"
    POLICY_UNCERTAINTY = "POLICY_UNCERTAINTY"
    CONFLICTING_INSTRUCTIONS = "CONFLICTING_INSTRUCTIONS"


_STOP_FOR: dict[CounterpartyKind, StopCondition] = {
    CounterpartyKind.INJECTION_ATTEMPT: StopCondition.POLICY_UNCERTAINTY,
    CounterpartyKind.SENSITIVE_REQUEST: StopCondition.SENSITIVE_INFO,
    CounterpartyKind.BOT_REFUSAL: StopCondition.COUNTERPARTY_REFUSES_BOT,
    CounterpartyKind.TERMINATE_REQUEST: StopCondition.COUNTERPARTY_TERMINATE,
    CounterpartyKind.WRONG_DESTINATION: StopCondition.DESTINATION_CHANGED,
    CounterpartyKind.CONFLICT: StopCondition.CONFLICTING_INSTRUCTIONS,
}
_ALWAYS_STOP = frozenset({CounterpartyKind.INJECTION_ATTEMPT, CounterpartyKind.SENSITIVE_REQUEST})
_SAY_GOODBYE = frozenset(
    {StopCondition.COUNTERPARTY_REFUSES_BOT, StopCondition.COUNTERPARTY_TERMINATE}
)
_PARKED = frozenset({StopCondition.CONFLICTING_INSTRUCTIONS, StopCondition.POLICY_UNCERTAINTY})


@dataclass
class CallRunResult:
    end_reason: str = ""
    stop_condition: StopCondition | None = None
    parked_for_review: bool = False
    answered: bool = False
    busy: bool = False
    no_answer: bool = False
    setup_failed: bool = False
    stream_lost: bool = False
    remote_hangup: bool = False
    human_connected: bool = False
    handoff: bool = False
    ivr_failure: str = ""
    ivr_proposal: list[str] = field(default_factory=list)
    observed: set[CounterpartyKind] = field(default_factory=set)


class CallController:
    def __init__(
        self,
        ctx: AgentSessionContext,
        navigator: IvrNavigator,
        model: DialogueModel,
        *,
        max_seconds: int,
    ) -> None:
        self.ctx = ctx
        self.nav = navigator
        self.model = model
        self.max_seconds = max_seconds
        self.registry = AgentToolRegistry(ctx)
        self.result = CallRunResult()
        self._turns = 0
        self._history: list[frozenset[CounterpartyKind]] = []

    # ------------------------------------------------------------------ loop

    def run(self) -> CallRunResult:
        events = 0
        while not self.ctx.ended and events < MAX_EVENTS:
            event = self.ctx.handle.poll()
            if event is None:
                self._finish("SILENCE")
                break
            events += 1
            if event.offset_seconds > self.max_seconds:
                self._finish("MAX_DURATION")
                break
            self._dispatch(event)
        if not self.ctx.ended:
            self._finish("MAX_EVENTS")
        self.ctx.pending_dtmf = None
        self.result.end_reason = self.result.end_reason or self.ctx.end_reason
        return self.result

    def _finish(self, reason: str) -> None:
        self.ctx.end_reason = self.ctx.end_reason or reason
        self.result.end_reason = self.result.end_reason or reason
        self.registry.execute(ToolCall("end_session", {"session_id": self.ctx.session_id}))

    def _dispatch(self, event: CallEvent) -> None:
        kind = event.kind
        if kind is CallEventKind.ANSWERED:
            self.result.answered = True
        elif kind is CallEventKind.BUSY:
            self.result.busy = True
            self._finish("BUSY")
        elif kind is CallEventKind.NO_ANSWER:
            self.result.no_answer = True
            self._finish("NO_ANSWER")
        elif kind is CallEventKind.FAILED:
            self.result.setup_failed = True
            self._finish("SETUP_FAILED")
        elif kind is CallEventKind.STREAM_LOST:
            self.result.stream_lost = True
            self._finish("STREAM_LOST")
        elif kind is CallEventKind.HANGUP:
            self.result.remote_hangup = True
            self._finish("REMOTE_HANGUP")
        elif kind is CallEventKind.SPEECH:
            self._on_speech(event)

    # ---------------------------------------------------------------- speech

    def _on_speech(self, event: CallEvent) -> None:
        entry = self.ctx.add_transcript(
            "COUNTERPARTY",
            event.text,
            EvidenceLabel.CLAIMED,
            complete=event.complete,
            offset=event.offset_seconds,
        )
        cls = classify_counterparty(event.text, complete=event.complete)
        flags = set(cls.flags)
        if cross_utterance_conflict(self._history, event.text):
            flags.add(CounterpartyKind.CONFLICT)
            cls = Classification(CounterpartyKind.CONFLICT, frozenset(flags))
        self._history.append(cls.flags)
        self.result.observed |= flags
        stop = self._stop_for(cls)
        if stop is not None:
            self._stop(stop)
        elif self.ctx.human_connected:
            self._agent_turn(entry, cls)
        else:
            self._ivr_turn(entry)

    def _stop_for(self, cls: Classification) -> StopCondition | None:
        stop = _STOP_FOR.get(cls.primary)
        if stop is None:
            return None
        if self.ctx.human_connected or cls.primary in _ALWAYS_STOP:
            return stop
        return None

    def _stop(self, stop: StopCondition) -> None:
        self.result.stop_condition = stop
        self.result.parked_for_review = stop in _PARKED
        if stop in _SAY_GOODBYE and self.ctx.ai_allowed and self.ctx.human_connected:
            self.registry.execute(
                ToolCall(
                    "speak_opt_out", {"session_id": self.ctx.session_id, "template_id": END_GOODBYE}
                )
            )
        self._finish(f"STOP_{stop}")

    # ------------------------------------------------------------------- IVR

    def _ivr_turn(self, entry: TranscriptEntry) -> None:
        decision = self.nav.observe(entry.text, entry.offset_seconds)
        action = decision.action
        if action is NavAction.SEND_DTMF:
            self.ctx.pending_dtmf = decision.digits
            sent = self.registry.execute(
                ToolCall(
                    "send_dtmf", {"session_id": self.ctx.session_id, "digits": decision.digits}
                )
            )
            if not sent.ok:
                self.result.ivr_failure = sent.error
                self._finish("IVR_DTMF_REJECTED")
        elif action is NavAction.PROPOSE:
            self.result.ivr_proposal = [o.as_text() for o in decision.options]
            self.result.ivr_failure = decision.reason
            self.result.parked_for_review = True
            self._finish("IVR_NEEDS_USER_APPROVAL")
        elif action in (NavAction.PAUSE_DRIFT, NavAction.ABORT):
            self.result.ivr_failure = decision.reason
            self.result.parked_for_review = action is NavAction.PAUSE_DRIFT
            self._finish(f"IVR_{decision.reason}")
        elif action is NavAction.CONNECTED_HUMAN:
            self._on_human(entry)

    def _on_human(self, entry: TranscriptEntry) -> None:
        self.ctx.human_connected = True
        self.result.human_connected = True
        if not self.ctx.ai_allowed:
            self.registry.execute(
                ToolCall("request_human_handoff", {"session_id": self.ctx.session_id})
            )
            self.result.handoff = True
            self._finish("HUMAN_HANDOFF")
            return
        self._agent_turn(entry, classify_counterparty(entry.text, complete=entry.complete))

    # ----------------------------------------------------------------- agent

    def _agent_turn(self, entry: TranscriptEntry, cls: Classification) -> None:
        ctx = self.ctx
        if (
            cls.primary is CounterpartyKind.ACKNOWLEDGED_CLAIM
            and ctx.request_spoken
            and entry.complete
        ):
            claim = ClaimRecord(
                "ACKNOWLEDGEMENT_CLAIM", EvidenceLabel.CLAIMED, entry.text, entry.seq
            )
            ctx.verified_ack = claim
            ctx.claims.append(claim)
        self._turns += 1
        if self._turns > MAX_AGENT_TURNS:
            self._finish("MAX_AGENT_TURNS")
            return
        state = DialogueState(
            session_id=ctx.session_id,
            request_spoken=ctx.request_spoken,
            spoken_template_ids=list(ctx.spoken_template_ids),
            last=cls,
            counterparty_text=entry.text,
            turn=self._turns,
        )
        for call in self.model.decide(state):
            self.registry.execute(call)
            if ctx.ended:
                break
