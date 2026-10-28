"""Bridge: scripted FakeStt + synthetic audio reproduce the mock scenarios' disclosure timing."""

from __future__ import annotations

import ast
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from callshield.callback.dialer_port import MediaEvent, MediaEventKind
from callshield.callback.mock_scenarios import SCENARIOS, CallbackMockHandle, CallbackScenario
from callshield.callback.session import CallbackSession, SessionConfig, SessionResult
from callshield.voice.base import SttEvent
from callshield.voice.callback_bridge import CallbackSpeechBridge
from callshield.voice.endpoint import VadEndpointer
from callshield.voice.fakes import FakeStt, ScriptedEvent, chunked, silence_pcm, sine_pcm
from callshield.voice.vad import EnergyVad

CONFIG = SessionConfig("v1", "Jordan Tester", datetime(2026, 10, 27, 9, 5), False, 10, 1500, 4, 120)
INTRO = "Thank you for calling Acme Solar, this is Dana."


def _bridge(script: list[ScriptedEvent | SttEvent]) -> CallbackSpeechBridge:
    stream = FakeStt(script).new_stream()
    return CallbackSpeechBridge(stream, VadEndpointer(EnergyVad(), 16000))


def _events(bridge: CallbackSpeechBridge, audio: bytes) -> list[MediaEvent]:
    out: list[MediaEvent] = []
    for piece in chunked(audio):
        out += bridge.feed(piece)
    return out + bridge.finish()


def _run(scenario: CallbackScenario) -> tuple[SessionResult, CallbackMockHandle]:
    handle = CallbackMockHandle(scenario, f"cb-{uuid.uuid4()}")
    return CallbackSession(handle, CONFIG).run(), handle


def _bridged(name: str, audio: bytes, utterance: SttEvent, answered_at: float) -> CallbackScenario:
    # whisper-style: the final result arrives once the pause has been detected
    bridge = _bridge([ScriptedEvent(utterance, release_s=utterance.end_s + 0.9)])
    opening = [bridge.answered(answered_at), *_events(bridge, audio)]
    return CallbackScenario(opening=opening, on_speak=SCENARIOS[name]().on_speak)


@pytest.mark.parametrize(
    ("name", "audio", "utterance", "answered_at"),
    [
        (
            "business_intro",
            silence_pcm(0.5) + sine_pcm(2.5) + silence_pcm(2.0),
            SttEvent(INTRO, True, 0.5, 3.0),
            0.5,
        ),
        (
            "bare_hello",
            silence_pcm(1.2) + sine_pcm(0.8) + silence_pcm(2.5),
            SttEvent("Hello?", True, 1.2, 2.0),
            0.5,
        ),
    ],
    ids=["business_intro", "bare_hello"],
)
def test_bridge_matches_mock_scenario_disclosure_timing(
    name: str, audio: bytes, utterance: SttEvent, answered_at: float
) -> None:
    expected, expected_handle = _run(SCENARIOS[name]())
    actual, handle = _run(_bridged(name, audio, utterance, answered_at))
    assert expected.disclosed and actual.disclosed
    assert actual.disclosure_offset == pytest.approx(expected.disclosure_offset, abs=0.03)
    assert handle.spoken[0] == expected_handle.spoken[0]
    assert actual.outcome == expected.outcome
    assert actual.listen == expected.listen


def test_bridge_emits_only_final_events_with_call_clock_offsets() -> None:
    bridge = CallbackSpeechBridge(
        FakeStt(
            [
                SttEvent("hel", False, 0.0, 0.3),
                SttEvent("  hello  ", True, 0.0, 0.5),
                SttEvent("", True, 0, 0),
            ]
        ).new_stream(),
        None,
        base_offset_s=10.0,
    )
    events = _events(bridge, silence_pcm(1.0))
    assert events == [MediaEvent(MediaEventKind.SPEECH, "hello", 10.5, 0.5)]
    assert bridge.partials_seen == 1


def test_bridge_snaps_end_to_vad_speech_end_when_close() -> None:
    audio = sine_pcm(1.0) + silence_pcm(1.5)
    # recognizer reports a sloppy end (1.3 s); the VAD measured 1.0 s
    bridge = _bridge([ScriptedEvent(SttEvent("hello", True, 0.0, 1.3), release_s=1.9)])
    (event,) = _events(bridge, audio)
    assert event.offset_seconds == pytest.approx(1.0)
    assert event.duration_seconds == pytest.approx(1.0)
    assert bridge.pauses_seen == 1


def test_bridge_keeps_stt_times_when_vad_end_is_far_or_unseen() -> None:
    bridge = _bridge([SttEvent("hello", True, 0.0, 1.3)])
    (event,) = _events(bridge, sine_pcm(1.4))
    assert event.offset_seconds == pytest.approx(1.3)


def test_voice_package_has_no_network_imports_or_prints() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "callshield" / "voice"
    banned = {"socket", "urllib", "http", "requests", "httpx", "aiohttp", "websockets", "ssl"}
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
            else:
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
                    pytest.fail(f"print() in {path.name}")
                continue
            assert not names & banned, f"{path.name} imports {names & banned}"


def test_importing_voice_modules_does_not_load_heavy_libraries() -> None:
    import callshield.voice.registry  # noqa: F401

    for heavy in ("faster_whisper", "vosk", "piper", "onnxruntime", "ctranslate2"):
        assert heavy not in sys.modules
