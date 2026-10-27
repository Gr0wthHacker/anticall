"""IVR parser + navigator (R-004, PRD 9.4)."""

from __future__ import annotations

from callshield.ivr.navigator import (
    ApprovedPath,
    IvrNavigator,
    NavAction,
    NavLimits,
    PathStep,
)
from callshield.ivr.parser import UtteranceKind, classify_utterance, parse_menu
from callshield.resolve.dialer import IVR_MENU

PATH = ApprovedPath(steps=(PathStep("2", ("agent",)),), timeout_seconds=30)


def test_parse_menu_press_for() -> None:
    opts = parse_menu(IVR_MENU)
    assert [(o.digit, o.label) for o in opts] == [("1", "sales"), ("2", "an agent")]
    assert opts[1].as_text() == "Press 2: an agent"


def test_parse_menu_for_press_and_words() -> None:
    opts = parse_menu("For billing, press 1. For support, press star.")
    assert [(o.digit, o.label) for o in opts] == [("1", "billing"), ("*", "support")]
    assert parse_menu("Welcome to our office.") == []


def test_classify_utterances() -> None:
    assert classify_utterance(IVR_MENU) is UtteranceKind.MENU
    assert classify_utterance("Please hold, your call is important.") is UtteranceKind.HOLD
    assert classify_utterance("Transferring you to an agent now.") is UtteranceKind.TRANSFER
    assert classify_utterance("Compliance desk, how can I help?") is UtteranceKind.HUMAN
    assert classify_utterance("The weather is nice.") is UtteranceKind.OTHER


def test_sends_dtmf_only_on_approved_path() -> None:
    nav = IvrNavigator(PATH)
    decision = nav.observe(IVR_MENU, 3)
    assert decision.action is NavAction.SEND_DTMF and decision.digits == "2"
    assert nav.route[0]["digit"] == "2" and nav.route[0]["path_version"] == 1


def test_no_approved_path_only_proposes() -> None:
    decision = IvrNavigator(None).observe(IVR_MENU, 3)
    assert decision.action is NavAction.PROPOSE
    assert decision.proposed_digit == "2" and len(decision.options) == 2


def test_low_confidence_or_empty_path_is_not_used() -> None:
    weak = ApprovedPath(steps=(PathStep("2"),), confidence=0.05)
    assert IvrNavigator(weak).observe(IVR_MENU, 1).action is NavAction.PROPOSE
    assert IvrNavigator(ApprovedPath(steps=())).observe(IVR_MENU, 1).action is NavAction.PROPOSE
    bad_digit = ApprovedPath(steps=(PathStep("22"),))
    assert IvrNavigator(bad_digit).observe(IVR_MENU, 1).action is NavAction.PROPOSE


def test_drift_when_option_missing_or_relabeled() -> None:
    changed = "Press 1 for billing, press 2 for new accounts."
    d = IvrNavigator(PATH).observe(changed, 1)
    assert d.action is NavAction.PAUSE_DRIFT and d.reason == "MENU_DRIFT"
    missing = "Press 1 for sales, press 3 for an agent."
    assert IvrNavigator(PATH).observe(missing, 1).action is NavAction.PAUSE_DRIFT


def test_menu_after_end_of_path_is_drift_not_probe() -> None:
    nav = IvrNavigator(PATH)
    nav.observe(IVR_MENU, 1)
    d = nav.observe("Press 5 for loans, press 6 for savings.", 2)
    assert d.action is NavAction.PAUSE_DRIFT and d.reason == "MENU_BEYOND_APPROVED_PATH"


def test_repeated_menu_aborts_instead_of_looping() -> None:
    nav = IvrNavigator(ApprovedPath(steps=(PathStep("2"), PathStep("2"))))
    assert nav.observe(IVR_MENU, 1).action is NavAction.SEND_DTMF
    again = nav.observe(IVR_MENU, 2)
    assert again.action is NavAction.ABORT and again.reason == "MENU_LOOP"
    assert nav.dtmf_count == 1


def test_hold_timeout_and_queue_loop() -> None:
    nav = IvrNavigator(PATH)
    assert nav.observe("Please hold.", 10).action is NavAction.WAIT
    timeout = nav.observe("Your call is important to us.", 45)
    assert timeout.action is NavAction.ABORT and timeout.reason == "HOLD_TIMEOUT"
    loop = IvrNavigator(PATH, NavLimits(max_hold_events=3))
    results = [loop.observe("Please hold.", 1).action for _ in range(5)]
    assert results[-1] is NavAction.ABORT


def test_transfer_waits_and_human_connects() -> None:
    nav = IvrNavigator(PATH)
    assert nav.observe("Transferring you now.", 1).action is NavAction.WAIT
    assert nav.observe("This is Dana speaking.", 2).action is NavAction.CONNECTED_HUMAN


def test_total_duration_and_step_limits() -> None:
    nav = IvrNavigator(PATH, NavLimits(max_total_seconds=60))
    d = nav.observe(IVR_MENU, 61)
    assert d.action is NavAction.ABORT and d.reason == "TOTAL_DURATION_EXCEEDED"
    steps = tuple(PathStep(str(i)) for i in range(1, 4))
    capped = IvrNavigator(ApprovedPath(steps=steps), NavLimits(max_steps=1))
    assert capped.observe("Press 1 for a, press 2 for b.", 1).action is NavAction.SEND_DTMF
    assert capped.observe("Press 2 for c, press 3 for d.", 2).action is NavAction.ABORT


def test_unrecognized_prompts_are_bounded() -> None:
    nav = IvrNavigator(PATH, NavLimits(max_other_events=2))
    actions = [nav.observe("blah blah", i).action for i in range(4)]
    assert actions[:2] == [NavAction.NOOP, NavAction.NOOP]
    assert actions[-1] is NavAction.ABORT
