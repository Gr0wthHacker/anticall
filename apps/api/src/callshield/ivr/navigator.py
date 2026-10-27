"""Stateful IVR navigator (R-004, PRD 9.4).

Sends DTMF ONLY along a user-approved path. Without an approved path it merely PROPOSES.
It never probes extensions, never replays a menu it already answered (loop => abort),
and enforces step, hold and total-duration limits. Menu drift => pause and ask the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from callshield.ivr.parser import (
    MenuOption,
    UtteranceKind,
    classify_utterance,
    menu_signature,
    parse_menu,
)

MIN_PATH_CONFIDENCE = 0.2
_GOAL_WORDS = ("agent", "representative", "operator", "compliance", "do not call", "opt out")
_VALID_DIGITS = frozenset("0123456789*#")


class NavAction(StrEnum):
    SEND_DTMF = "SEND_DTMF"
    WAIT = "WAIT"
    PROPOSE = "PROPOSE"
    CONNECTED_HUMAN = "CONNECTED_HUMAN"
    ABORT = "ABORT"
    PAUSE_DRIFT = "PAUSE_DRIFT"
    NOOP = "NOOP"


@dataclass(frozen=True)
class PathStep:
    digit: str
    expect_keywords: tuple[str, ...] = ()  # option label must contain one (empty = any)


@dataclass(frozen=True)
class ApprovedPath:
    """A user-approved route for one destination (ivr_paths row)."""

    steps: tuple[PathStep, ...]
    version: int = 1
    confidence: float = 1.0
    last_tested_at: datetime | None = None
    timeout_seconds: int = 60
    fallback: str = "ABORT"  # ABORT | HANDOFF_TO_USER
    path_id: str | None = None

    def usable(self) -> bool:
        return (
            bool(self.steps)
            and self.confidence >= MIN_PATH_CONFIDENCE
            and all(s.digit in _VALID_DIGITS and len(s.digit) == 1 for s in self.steps)
        )

    def to_choices(self) -> list[dict[str, Any]]:
        return [{"digit": s.digit, "expect_keywords": list(s.expect_keywords)} for s in self.steps]

    @staticmethod
    def steps_from_choices(choices: list[Any]) -> tuple[PathStep, ...]:
        return tuple(
            PathStep(str(c["digit"]), tuple(str(k).lower() for k in c.get("expect_keywords", [])))
            for c in choices
        )


@dataclass(frozen=True)
class NavLimits:
    max_steps: int = 6
    max_total_seconds: float = 600.0
    max_hold_events: int = 20
    max_other_events: int = 12


@dataclass(frozen=True)
class NavDecision:
    action: NavAction
    digits: str = ""
    reason: str = ""
    options: tuple[MenuOption, ...] = ()
    proposed_digit: str = ""


@dataclass
class IvrNavigator:
    path: ApprovedPath | None
    limits: NavLimits = field(default_factory=NavLimits)
    step_index: int = 0
    dtmf_count: int = 0
    hold_started: float | None = None
    hold_events: int = 0
    other_events: int = 0
    last_menu_sig: str | None = None
    route: list[dict[str, Any]] = field(default_factory=list)

    def observe(self, text: str, offset_seconds: float) -> NavDecision:
        """Feed one counterparty utterance; returns what to do next."""
        if offset_seconds > self.limits.max_total_seconds:
            return NavDecision(NavAction.ABORT, reason="TOTAL_DURATION_EXCEEDED")
        kind = classify_utterance(text)
        if kind is UtteranceKind.MENU:
            return self._on_menu(parse_menu(text))
        if kind is UtteranceKind.HOLD:
            return self._on_hold(offset_seconds)
        if kind is UtteranceKind.TRANSFER:
            return NavDecision(NavAction.WAIT, reason="TRANSFER_IN_PROGRESS")
        if kind is UtteranceKind.HUMAN:
            return NavDecision(NavAction.CONNECTED_HUMAN)
        self.other_events += 1
        if self.other_events > self.limits.max_other_events:
            return NavDecision(NavAction.ABORT, reason="TOO_MANY_UNRECOGNIZED_PROMPTS")
        return NavDecision(NavAction.NOOP)

    def _on_hold(self, t: float) -> NavDecision:
        self.hold_events += 1
        if self.hold_started is None:
            self.hold_started = t
        timeout = self.path.timeout_seconds if self.path else 60
        if t - self.hold_started > timeout:
            return NavDecision(NavAction.ABORT, reason="HOLD_TIMEOUT")
        if self.hold_events > self.limits.max_hold_events:
            return NavDecision(NavAction.ABORT, reason="HOLD_LOOP")
        return NavDecision(NavAction.WAIT, reason="ON_HOLD")

    def _propose(self, options: list[MenuOption]) -> NavDecision:
        proposed = next(
            (o.digit for o in options if any(g in o.label.lower() for g in _GOAL_WORDS)), ""
        )
        return NavDecision(
            NavAction.PROPOSE,
            reason="NO_APPROVED_PATH",
            options=tuple(options),
            proposed_digit=proposed,
        )

    def _on_menu(self, options: list[MenuOption]) -> NavDecision:
        if self.path is None or not self.path.usable():
            return self._propose(options)
        sig = menu_signature(options)
        if sig == self.last_menu_sig and self.dtmf_count > 0:
            return NavDecision(NavAction.ABORT, reason="MENU_LOOP", options=tuple(options))
        if self.dtmf_count >= self.limits.max_steps:
            return NavDecision(NavAction.ABORT, reason="MAX_STEPS_EXCEEDED")
        if self.step_index >= len(self.path.steps):
            return NavDecision(
                NavAction.PAUSE_DRIFT, reason="MENU_BEYOND_APPROVED_PATH", options=tuple(options)
            )
        step = self.path.steps[self.step_index]
        match = next((o for o in options if o.digit == step.digit), None)
        label = match.label.lower() if match else ""
        if match is None or (
            step.expect_keywords and not any(k in label for k in step.expect_keywords)
        ):
            return NavDecision(NavAction.PAUSE_DRIFT, reason="MENU_DRIFT", options=tuple(options))
        self.last_menu_sig = sig
        self.step_index += 1
        self.dtmf_count += 1
        self.route.append(
            {
                "step": self.dtmf_count,
                "digit": step.digit,
                "label": match.label,
                "path_version": self.path.version,
            }
        )
        return NavDecision(NavAction.SEND_DTMF, digits=step.digit, options=tuple(options))
