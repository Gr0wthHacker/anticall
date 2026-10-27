"""Synthetic telephony for callback tests and local dev (never touches a network).

`CallbackMockDialer` implements the `CallbackDialer` seam. Scenarios script the answering
party; `on_speak` reactions fire when the assistant speaks (nth utterance), `on_dtmf` when it
presses a digit. Fictional numbers only.
"""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from callshield.callback.dialer_port import (
    CallbackCallHandle,
    MediaEvent,
    MediaEventKind,
    VerifiedCaller,
)
from callshield.core.errors import PolicyBlockedError
from callshield.resolve.dialer import CallEvent, CallHandle

Event = MediaEvent | CallEvent


def say(text: str, end: float, duration: float = 1.0) -> MediaEvent:
    return MediaEvent(MediaEventKind.SPEECH, text, end, duration)


def ev(kind: MediaEventKind, t: float = 0.0) -> MediaEvent:
    return MediaEvent(kind, "", t, 0.0)


@dataclass
class CallbackScenario:
    opening: Sequence[Event]
    on_dtmf: dict[str, Sequence[Event]] = field(default_factory=dict)
    on_speak: list[Sequence[Event]] = field(default_factory=list)  # nth speak -> reaction
    speak_completes: list[bool] = field(default_factory=list)


class CallbackMockHandle:
    def __init__(self, scenario: CallbackScenario, call_ref: str) -> None:
        self.call_ref = call_ref
        self._scenario = scenario
        self._queue: deque[Event] = deque(scenario.opening)
        self._speak_index = 0
        self.dtmf_sent: list[str] = []
        self.spoken: list[str] = []
        self.events_before_first_speak = 0
        self.hung_up = False
        self.recording_started = False
        self.recording_stopped = False
        self.audio_started_after_speaks: int | None = None
        self.polled = 0

    def poll(self) -> Event | None:
        self.polled += 1
        return self._queue.popleft() if self._queue and not self.hung_up else None

    def send_dtmf(self, digits: str) -> None:
        self.dtmf_sent.append(digits)
        self._queue.extend(self._scenario.on_dtmf.get(digits, []))

    def speak(self, text: str) -> bool:
        self.spoken.append(text)
        idx = self._speak_index
        self._speak_index += 1
        if idx < len(self._scenario.on_speak):
            self._queue.extend(self._scenario.on_speak[idx])
        completes = self._scenario.speak_completes
        return completes[idx] if idx < len(completes) else True

    def hangup(self) -> None:
        self.hung_up = True

    def start_recording(self) -> None:
        self.recording_started = True
        self.audio_started_after_speaks = len(self.spoken)

    def stop_recording(self) -> bytes:
        self.recording_stopped = True
        return b"SYNTHETIC-AUDIO-AFTER-DISCLOSURE"


class CallbackMockDialer:
    kind = "callback-mock"
    configured = True

    def __init__(self, scenario: str | CallbackScenario = "business_intro") -> None:
        self._scenario = scenario
        self.calls_placed = 0
        self.dialed: list[str] = []
        self.callers: list[VerifiedCaller] = []
        self.handles: list[CallbackMockHandle] = []

    def place_call(
        self, destination_e164: str, session_id: uuid.UUID, *, max_seconds: int
    ) -> CallHandle:
        raise PolicyBlockedError("callbacks require a verified caller", code="CALLER_REQUIRED")

    def place_callback(
        self,
        destination_e164: str,
        session_id: uuid.UUID,
        *,
        caller: VerifiedCaller,
        max_seconds: int,
    ) -> CallbackCallHandle:
        scenario = (
            self._scenario
            if isinstance(self._scenario, CallbackScenario)
            else SCENARIOS[self._scenario]()
        )
        self.calls_placed += 1
        self.dialed.append(destination_e164)
        self.callers.append(caller)
        handle = CallbackMockHandle(scenario, f"cbmock-{session_id}")
        self.handles.append(handle)
        return handle


ANSWERED = MediaEventKind.ANSWERED
HANGUP = MediaEventKind.HANGUP
_INTRO = "Thank you for calling Acme Solar, this is Dana."
_REPLY = "We're Acme Solar. We sell residential solar panels. Our website is acmesolar.com."


def _after_disclosure(*reactions: Sequence[Event]) -> list[Sequence[Event]]:
    return list(reactions)


def _intro_scenarios() -> dict[str, Callable[[], CallbackScenario]]:
    return {
        "business_intro": lambda: CallbackScenario(
            opening=[ev(ANSWERED, 0.5), say(_INTRO, 3.0, 2.5)],
            on_speak=_after_disclosure(
                [say(_REPLY, 9.0, 4.0)],
                [say("We transfer to the Acme Solar sales desk. Call 415-555-0134.", 14.0, 3.0)],
            ),
        ),
        "business_intro_long": lambda: CallbackScenario(
            # continuous speech with no 800 ms gap: only the 10 s ceiling can trigger
            opening=[ev(ANSWERED, 0.0)]
            + [
                say(f"We are Acme Solar and we have offers number {i}.", float(i))
                for i in range(1, 12)
            ],
            on_speak=_after_disclosure([say(_REPLY, 20.0, 3.0)]),
        ),
        "bare_hello": lambda: CallbackScenario(
            opening=[ev(ANSWERED, 0.5), say("Hello?", 2.0, 0.8)],
            on_speak=_after_disclosure([say(_REPLY, 9.0, 4.0)]),
        ),
    }


def _outcome_scenarios() -> dict[str, Callable[[], CallbackScenario]]:
    menu = "Thank you for calling Acme Lending. Press 1 for sales, press 2 for an agent."
    return {
        "wrong_number_listen": lambda: CallbackScenario(
            opening=[ev(ANSWERED, 0.5), say("Hello? Wrong number, we didn't call you.", 3.0, 2.0)]
        ),
        "wrong_number_after": lambda: CallbackScenario(
            opening=[ev(ANSWERED, 0.5), say("Hello?", 2.0, 0.8)],
            on_speak=_after_disclosure([say("This is a wrong number.", 8.0, 2.0)]),
        ),
        "stop_calling": lambda: CallbackScenario(
            opening=[ev(ANSWERED, 0.5), say("Hello?", 2.0, 0.8)],
            on_speak=_after_disclosure([say("Stop calling this number!", 8.0, 2.0)]),
        ),
        "hangup_after_disclosure": lambda: CallbackScenario(
            opening=[ev(ANSWERED, 0.5), say(_INTRO, 3.0, 2.5)],
            on_speak=_after_disclosure([ev(HANGUP, 7.0)]),
        ),
        "voicemail": lambda: CallbackScenario(
            opening=[
                ev(ANSWERED, 0.5),
                say("Hi, you've reached Dana. Leave a message after the tone.", 4.0, 3.0),
            ]
        ),
        "ivr_dtmf": lambda: CallbackScenario(
            opening=[ev(ANSWERED, 0.5), say(menu, 5.0, 4.0)],
            on_dtmf={"2": [say("Compliance desk, how can I help you?", 9.0, 2.0)]},
            on_speak=_after_disclosure([say(_REPLY, 16.0, 4.0)]),
        ),
        "ivr_speech_only": lambda: CallbackScenario(
            opening=[
                ev(ANSWERED, 0.5),
                say("Welcome. In a few words, tell me what you are calling about.", 5.0, 4.0),
            ]
        ),
        "robot_question": lambda: CallbackScenario(
            opening=[ev(ANSWERED, 0.5), say(_INTRO, 3.0, 2.5)],
            on_speak=_after_disclosure(
                [say("Wait, are you a robot?", 8.0, 1.5)],
                [say("Who are you exactly?", 11.0, 1.5)],
                [say(_REPLY, 15.0, 3.0)],
            ),
        ),
        "no_answer": lambda: CallbackScenario(opening=[ev(MediaEventKind.NO_ANSWER, 30.0)]),
    }


SCENARIOS: dict[str, Callable[[], CallbackScenario]] = {
    **_intro_scenarios(),
    **_outcome_scenarios(),
}
