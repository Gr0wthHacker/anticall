"""Callback call-session state machine (spec "Call flow", steps 1-7), DB-free.

Driven entirely by `MediaEvent`s from the dialer handle on a virtual clock
(`offset_seconds`). Guarantees enforced here, in code:

* No speech before the disclosure at all; a hard stop before it hangs up silently.
* IVRs are navigated with DTMF only; speech-only menus end the call (`ivr_speech_only`).
* Voicemail: hang up, say nothing (`voicemail`).
* The listen window lives in `EphemeralListenBuffer`; it is discarded the instant the
  disclosure is triggered and only rep_name / company_claimed / greeting_type survive.
* Every spoken string must belong to the closed set in `callback.disclosure`; a dialogue
  model can only choose a `QuestionIntent`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from callshield.callback import disclosure as utter
from callshield.callback.dialer_port import (
    CallbackCallHandle,
    MediaEvent,
    MediaEventKind,
    normalize_event,
)
from callshield.callback.dialogue import (
    CallbackDialogueModel,
    DialogueView,
    RuleBasedCallbackModel,
    missing_intents,
)
from callshield.callback.extraction import (
    EphemeralListenBuffer,
    ExtractedFields,
    GreetingType,
    Intent,
    ListenExtraction,
    classify_greeting,
    detect_intent,
)
from callshield.callback.hardstop import HardStop, HardStopDetector, HardStopKind
from callshield.callback.locality import assert_local_only
from callshield.ivr.navigator import IvrNavigator, NavAction
from callshield.ivr.parser import parse_menu

BUSINESS_PAUSE_SECONDS = 0.8
MAX_DTMF_STEPS = 3
MAX_PERSONA_ANSWERS = 4
MAX_POLLS = 400

_VOICEMAIL = re.compile(
    r"leave (?:a |your )?(?:message|name)|after the (?:tone|beep)|at the tone|voicemail|"
    r"mailbox|record your message|not available to take your call",
    re.I,
)
_SPEECH_ONLY = re.compile(
    r"\b(?:please )?(?:say|tell me|speak)\b|in a few words|what you(?:'re| are) calling about",
    re.I,
)
_IVR_CUES = re.compile(
    r"\bpress\b|automated|your call may be|this call (?:may be|is being) recorded|"
    r"para espa|main menu|please hold",
    re.I,
)


class Phase(StrEnum):
    DIALING = "DIALING"
    CLASSIFY = "CLASSIFY"
    IVR = "IVR"
    LISTEN = "LISTEN"
    QA = "QA"
    DONE = "DONE"


class Outcome(StrEnum):
    COMPLETED = "COMPLETED"
    VOICEMAIL = "VOICEMAIL"
    IVR_SPEECH_ONLY = "IVR_SPEECH_ONLY"
    IVR_ABORTED = "IVR_ABORTED"
    NO_ANSWER = "NO_ANSWER"
    BUSY = "BUSY"
    FAILED = "FAILED"
    NO_RESPONSE = "NO_RESPONSE"
    HARD_STOP = "HARD_STOP"
    HUNG_UP_AFTER_DISCLOSURE = "HUNG_UP_AFTER_DISCLOSURE"
    HUNG_UP_BEFORE_DISCLOSURE = "HUNG_UP_BEFORE_DISCLOSURE"
    MAX_DURATION = "MAX_DURATION"
    STREAM_LOST = "STREAM_LOST"
    DISCLOSURE_INTERRUPTED = "DISCLOSURE_INTERRUPTED"


@dataclass(frozen=True)
class SessionConfig:
    template_id: str
    first_name: str
    call_time: datetime  # inbound call time in the user's local zone (rendered into the text)
    outbound_recording: bool
    listen_window_seconds: int
    bare_greeting_disclose_ms: int
    max_question_turns: int
    max_call_seconds: int


@dataclass
class SessionResult:
    outcome: Outcome = Outcome.FAILED
    log_code: str | None = None
    answered: bool = False
    disclosed: bool = False
    disclosure_offset: float | None = None
    duration_seconds: float = 0.0
    fields: ExtractedFields = field(default_factory=ExtractedFields)
    listen: ListenExtraction | None = None
    hung_up_after_disclosure: bool = False
    wrong_party: bool = False
    stop_requested: bool = False
    hard_stop: HardStop | None = None
    dtmf_sent: list[str] = field(default_factory=list)
    questions_asked: list[str] = field(default_factory=list)
    replies_heard: int = 0
    injection_attempts_ignored: int = 0
    transcript: list[tuple[str, str]] = field(default_factory=list)  # recording only
    audio: bytes | None = None


class CallbackSession:
    def __init__(
        self,
        handle: CallbackCallHandle,
        config: SessionConfig,
        model: CallbackDialogueModel | None = None,
        detector: HardStopDetector | None = None,
    ) -> None:
        self._h = handle
        self._cfg = config
        self._model: CallbackDialogueModel = model or RuleBasedCallbackModel()
        endpoint = getattr(self._model, "endpoint", None)
        if endpoint is not None:
            assert_local_only(str(endpoint))
        self._detector = detector or HardStopDetector()
        self._buffer = EphemeralListenBuffer()
        self._nav = IvrNavigator(path=None)
        self._seen_menus: set[str] = set()
        self._phase = Phase.DIALING
        self._now = 0.0
        self._listen_start = 0.0
        self._last_end: float | None = None
        self._last_bare = False
        self._first_greeting: GreetingType | None = None
        self._allowed: frozenset[str] = frozenset({utter.APOLOGY})
        self._persona_answers = 0
        self._recording = False
        self.result = SessionResult()

    # ------------------------------------------------------------------ driver
    def run(self) -> SessionResult:
        try:
            for _ in range(MAX_POLLS):
                if self._phase is Phase.DONE:
                    break
                raw = self._h.poll()
                self._step(None if raw is None else normalize_event(raw))
            else:
                self._end(Outcome.MAX_DURATION)
        finally:
            self._discard_listen()
            self._finish()
        return self.result

    def _step(self, event: MediaEvent | None) -> None:
        t = math.inf if event is None else event.offset_seconds
        begins = math.inf if event is None else event.offset_seconds - event.duration_seconds
        limit = float(self._cfg.max_call_seconds)
        disclosed_now = False
        if self._phase is Phase.LISTEN:
            trigger = self._disclosure_trigger()
            if trigger <= min(begins, limit):
                self._disclose(trigger)
                disclosed_now = True
        if self._phase is Phase.DONE or (disclosed_now and event is None):
            return  # after disclosing, re-poll: the callee's reaction is not queued yet
        if event is None:
            self._on_silence()
        elif t > limit:
            self._now = limit
            self._end(Outcome.MAX_DURATION)
        else:
            self._now = max(self._now, t)
            self._on_event(event)

    def _on_event(self, event: MediaEvent) -> None:
        kind = event.kind
        if kind is MediaEventKind.RINGING:
            return
        terminal = {
            MediaEventKind.NO_ANSWER: Outcome.NO_ANSWER,
            MediaEventKind.BUSY: Outcome.BUSY,
            MediaEventKind.FAILED: Outcome.FAILED,
            MediaEventKind.STREAM_LOST: Outcome.STREAM_LOST,
        }
        if kind in terminal:
            self._end(terminal[kind], hangup=False)
        elif kind is MediaEventKind.HANGUP:
            self._on_remote_hangup()
        elif kind is MediaEventKind.ANSWERED:
            self.result.answered = True
            self._phase = Phase.CLASSIFY
        elif kind is MediaEventKind.SPEECH:
            self._on_speech(event)

    def _on_remote_hangup(self) -> None:
        if self._phase is Phase.QA:
            self.result.hung_up_after_disclosure = True
            self._end(Outcome.HUNG_UP_AFTER_DISCLOSURE, hangup=False)
        elif self._phase is Phase.LISTEN:
            self._end(Outcome.HUNG_UP_BEFORE_DISCLOSURE, hangup=False)
        else:
            self._end(Outcome.NO_ANSWER, hangup=False)

    def _on_speech(self, event: MediaEvent) -> None:
        if self._phase in (Phase.CLASSIFY, Phase.IVR):
            self._route_first_speech(event)
        elif self._phase is Phase.LISTEN:
            self._on_listen_speech(event)
        elif self._phase is Phase.QA:
            self._on_reply(event)

    def _on_silence(self) -> None:
        if self._phase is Phase.QA:
            replies = self.result.replies_heard
            self._close(Outcome.COMPLETED if replies else Outcome.NO_RESPONSE)
        elif self._phase is Phase.LISTEN:
            self._end(Outcome.MAX_DURATION)
        elif self._phase is Phase.DIALING:
            self._end(Outcome.NO_ANSWER, hangup=False)
        else:
            self._end(Outcome.NO_RESPONSE)

    # ------------------------------------------------------------------ IVR / classify
    def _route_first_speech(self, event: MediaEvent) -> None:
        text = event.text
        if _VOICEMAIL.search(text):
            self._end(Outcome.VOICEMAIL, log_code="voicemail")
            return
        if not parse_menu(text) and _SPEECH_ONLY.search(text):
            self._end(Outcome.IVR_SPEECH_ONLY, log_code="ivr_speech_only")
            return
        decision = self._nav.observe(text, event.offset_seconds)
        if decision.action is NavAction.PROPOSE:
            self._press(decision.proposed_digit, text)
        elif decision.action in (NavAction.ABORT, NavAction.PAUSE_DRIFT):
            self._end(Outcome.IVR_ABORTED, log_code="ivr_aborted")
        elif decision.action is NavAction.WAIT or _IVR_CUES.search(text):
            self._phase = Phase.IVR
        else:
            self._begin_listen(event)

    def _press(self, digit: str, menu_text: str) -> None:
        signature = "|".join(f"{o.digit}:{o.label}" for o in parse_menu(menu_text))
        if (
            not digit
            or signature in self._seen_menus
            or len(self.result.dtmf_sent) >= MAX_DTMF_STEPS
        ):
            self._end(Outcome.IVR_ABORTED, log_code="ivr_no_route")
            return
        self._seen_menus.add(signature)
        self.result.dtmf_sent.append(digit)
        self._h.send_dtmf(digit)  # DTMF only: never synthesized speech
        self._phase = Phase.IVR

    # ------------------------------------------------------------------ listen window
    def _begin_listen(self, event: MediaEvent) -> None:
        self._phase = Phase.LISTEN
        self._listen_start = event.offset_seconds - event.duration_seconds
        if self._stop_if_hard(event.text):
            return
        if self._cfg.listen_window_seconds == 0:
            self._disclose(
                event.offset_seconds
            )  # fallback mode: nothing is extracted pre-disclosure
            return
        self._ingest_listen(event)

    def _on_listen_speech(self, event: MediaEvent) -> None:
        if not self._stop_if_hard(event.text):
            self._ingest_listen(event)

    def _ingest_listen(self, event: MediaEvent) -> None:
        self._buffer.push(event.text)
        self._last_end = event.offset_seconds
        greeting = classify_greeting(event.text)
        self._last_bare = greeting is GreetingType.BARE_GREETING
        if self._first_greeting is None:
            self._first_greeting = greeting

    def _disclosure_trigger(self) -> float:
        ceiling = self._listen_start + self._cfg.listen_window_seconds
        if self._last_end is None:
            return ceiling
        gap = (
            self._cfg.bare_greeting_disclose_ms / 1000
            if self._last_bare
            else BUSINESS_PAUSE_SECONDS
        )
        return min(ceiling, self._last_end + gap)

    def _discard_listen(self) -> None:
        if self._buffer.discarded:
            return
        extraction = self._buffer.extract()
        self.result.listen = ListenExtraction(
            extraction.rep_name,
            extraction.company_claimed,
            self._first_greeting or extraction.greeting_type,
        )
        self._buffer.discard()
        fields = self.result.fields
        fields.rep_name = fields.rep_name or extraction.rep_name
        fields.company_claimed = fields.company_claimed or extraction.company_claimed

    # ------------------------------------------------------------------ disclosure
    def _disclose(self, at: float) -> None:
        self._discard_listen()
        self._now = max(self._now, at)
        text = utter.render_disclosure(
            self._cfg.template_id,
            first_name=self._cfg.first_name,
            call_time=self._cfg.call_time,
            outbound_recording=self._cfg.outbound_recording,
        )
        self._allowed = utter.allowed_utterances(text, self._cfg.first_name)
        delivered = self._h.speak(text) or self._h.speak(text)  # one repeat if talked over
        self.result.disclosed = True
        self.result.disclosure_offset = at
        if not delivered:
            self._end(Outcome.DISCLOSURE_INTERRUPTED)
            return
        if self._cfg.outbound_recording:
            self._h.start_recording()  # starts only AFTER the disclosure finished
            self._recording = True
        self._phase = Phase.QA

    # ------------------------------------------------------------------ Q&A
    def _on_reply(self, event: MediaEvent) -> None:
        text = event.text
        self.result.replies_heard += 1
        if self._recording:
            self.result.transcript.append(("answering_party", text))
        if self._stop_if_hard(text):
            return
        intent = detect_intent(text)
        if intent is Intent.INJECTION:
            self.result.injection_attempts_ignored += 1  # ignored: not extracted, not answered
        else:
            self.result.fields.absorb(text)
        if intent in (Intent.ROBOT_QUESTION, Intent.WHO_ARE_YOU, Intent.PERSONAL_DATA_REQUEST):
            self._answer_persona(intent)
        else:
            self._next_question_or_close()

    def _answer_persona(self, intent: Intent) -> None:
        self._persona_answers += 1
        if self._persona_answers > MAX_PERSONA_ANSWERS:
            self._close(Outcome.COMPLETED)
            return
        name = utter.clean_first_name(self._cfg.first_name)
        answers = {
            Intent.ROBOT_QUESTION: utter.ROBOT_ANSWER.format(first_name=name),
            Intent.WHO_ARE_YOU: utter.WHO_ANSWER.format(first_name=name),
            Intent.PERSONAL_DATA_REQUEST: utter.DECLINE_ANSWER,
        }
        self._say(answers[intent])

    def _next_question_or_close(self) -> None:
        if len(self.result.questions_asked) >= self._cfg.max_question_turns:
            self._close(Outcome.COMPLETED)
            return
        intent = self._choose_intent()
        if intent is None:
            self._close(Outcome.COMPLETED)
            return
        self.result.questions_asked.append(intent.value)
        self._say(utter.QUESTIONS[intent])

    def _choose_intent(self) -> utter.QuestionIntent | None:
        asked = tuple(utter.QuestionIntent(a) for a in self.result.questions_asked)
        view = DialogueView(asked, missing_intents(self.result.fields), len(asked), "")
        try:
            chosen = self._model.next_intent(view)
        except Exception:
            chosen = RuleBasedCallbackModel().next_intent(view)
        if chosen is not None and (not isinstance(chosen, utter.QuestionIntent) or chosen in asked):
            chosen = RuleBasedCallbackModel().next_intent(view)  # model output is not trusted
        return chosen

    # ------------------------------------------------------------------ speaking / ending
    def _say(self, text: str) -> bool:
        if text not in self._allowed:
            raise RuntimeError("utterance is not part of the fixed callback script")
        if self._recording:
            self.result.transcript.append(("assistant", text))
        return self._h.speak(text)

    def _stop_if_hard(self, text: str) -> bool:
        stop = self._detector.check(text)
        if stop is None:
            return False
        self.result.hard_stop = stop
        self.result.wrong_party = stop.kind is HardStopKind.WRONG_PARTY
        self.result.stop_requested = stop.kind is HardStopKind.STOP_REQUESTED
        if self.result.disclosed:
            self._say(utter.APOLOGY)
        self._end(Outcome.HARD_STOP)
        return True

    def _close(self, outcome: Outcome) -> None:
        self._say(utter.CLOSING)
        self._end(outcome)

    def _end(self, outcome: Outcome, *, log_code: str | None = None, hangup: bool = True) -> None:
        if self._phase is Phase.DONE:
            return
        self.result.outcome = outcome
        self.result.log_code = log_code
        self._phase = Phase.DONE
        if hangup:
            self._h.hangup()

    def _finish(self) -> None:
        res = self.result
        res.duration_seconds = min(self._now, float(self._cfg.max_call_seconds))
        if self._recording:
            res.audio = self._h.stop_recording()
        if res.listen is not None:
            res.fields.rep_name = res.fields.rep_name or res.listen.rep_name
            res.fields.company_claimed = res.fields.company_claimed or res.listen.company_claimed
        if self._phase is not Phase.DONE:  # only reachable after an unexpected error
            self._h.hangup()
            self.result.outcome = Outcome.FAILED
            self._phase = Phase.DONE
