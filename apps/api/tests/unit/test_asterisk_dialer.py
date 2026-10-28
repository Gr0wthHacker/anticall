"""AsteriskDialer / AsteriskCallHandle against a fake ARI and fake media (real loop thread)."""

from __future__ import annotations

import functools
import hashlib
import inspect
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from callshield.callback.dialer_port import MediaEvent, MediaEventKind, VerifiedCaller
from callshield.callback.session import CallbackSession, Outcome, SessionConfig
from callshield.core.errors import PolicyBlockedError
from callshield.telephony.asterisk.dialer import AsteriskCallHandle, AsteriskDialer, HandleTiming
from callshield.telephony.asterisk.loopthread import LoopThread
from callshield.telephony.asterisk.media import MediaBridge, StaticGate
from callshield.telephony.asterisk.settings import AsteriskSettings
from tests.asterisk_support import (
    CALLER_E164,
    USER_E164,
    FakeAri,
    FakeMedia,
    ScriptedStt,
    final,
    make_tts,
    speech_payload,
    wait_until_sync,
)

DEST = CALLER_E164
FAST = HandleTiming(
    business_pause_s=0.1,
    bare_greeting_s=0.2,
    no_speech_s=0.3,
    ivr_wait_s=0.3,
    reply_wait_s=0.4,
    ring_wait_s=0.5,
    speak_timeout_s=5.0,
)


@dataclass
class Env:
    dialer: AsteriskDialer
    ari: FakeAri
    loop: LoopThread
    media: list[FakeMedia] = field(default_factory=list)
    stt: ScriptedStt = field(default_factory=ScriptedStt)

    def caller(self) -> VerifiedCaller:
        return VerifiedCaller(uuid.uuid4(), USER_E164)

    def place(self, max_seconds: int = 60) -> AsteriskCallHandle:
        return self.dialer.place_callback(
            DEST, uuid.uuid4(), caller=self.caller(), max_seconds=max_seconds
        )

    def event(self, data: dict[str, Any]) -> None:
        self.loop.submit(self.dialer.dispatch_event(data)).result(3)

    def answer(self, handle: AsteriskCallHandle) -> FakeMedia:
        self.event({"type": "StasisStart", "channel": {"id": handle.call_ref}})
        wait_until_sync(lambda: bool(self.media) and self.media[-1].opened_for is not None)
        return self.media[-1]

    def on_loop(self, fn: Callable[[], None]) -> None:
        self.loop.call_soon(fn)


@pytest.fixture
def make_env(tmp_path: Path) -> Iterator[Callable[..., Env]]:
    envs: list[Env] = []

    def build(
        *,
        gate: bool = True,
        stt: ScriptedStt | None = None,
        timing: HandleTiming = FAST,
        recording_dir: Path | None = None,
    ) -> Env:
        settings = AsteriskSettings(enabled=True, recording_dir=recording_dir)
        loop = LoopThread()
        loop.start()
        ari = FakeAri()
        env = Env(None, ari, loop, stt=stt or ScriptedStt())  # type: ignore[arg-type]

        def media_factory(bridge: MediaBridge) -> FakeMedia:
            media = FakeMedia(bridge)
            env.media.append(media)
            return media

        env.dialer = AsteriskDialer(
            settings,
            ari,
            loop,
            stt=env.stt,
            tts=make_tts(),
            gate_factory=lambda _c: StaticGate(gate),
            media_factory=media_factory,
            timing=timing,
        )
        envs.append(env)
        return env

    yield build
    for env in envs:
        env.dialer.close_all()
        env.loop.stop()


# ------------------------------------------------------------------ caller ID
def test_originates_with_verified_caller_only(make_env: Callable[..., Env]) -> None:
    env = make_env()
    caller = env.caller()
    handle = env.dialer.place_callback(DEST, uuid.uuid4(), caller=caller, max_seconds=60)
    (params,) = env.ari.find("POST", "/channels")
    assert params["callerId"] == caller.e164 == USER_E164
    assert params["endpoint"] == f"PJSIP/{DEST}@trunk" and params["app"] == "callshield-out"
    assert params["channelId"] == handle.call_ref


def test_no_other_caller_id_path_exists(make_env: Callable[..., Env]) -> None:
    env = make_env()
    sig = inspect.signature(AsteriskDialer.place_callback)
    assert [n for n in sig.parameters if "caller" in n] == ["caller"]
    assert sig.parameters["caller"].annotation in (VerifiedCaller, "VerifiedCaller")
    assert not [f for f in AsteriskSettings.model_fields if "caller" in f or "cli" in f]
    with pytest.raises(TypeError):
        env.dialer.place_callback(  # type: ignore[call-arg]
            DEST, uuid.uuid4(), caller=env.caller(), caller_id="+14155550199", max_seconds=60
        )
    with pytest.raises(PolicyBlockedError) as err:
        env.dialer.place_callback(DEST, uuid.uuid4(), caller=USER_E164, max_seconds=60)  # type: ignore[arg-type]
    assert err.value.code == "NO_CALLER_ID"
    with pytest.raises(PolicyBlockedError):
        env.dialer.place_call(DEST, uuid.uuid4(), max_seconds=60)  # legacy path has no caller
    assert env.ari.find("POST", "/channels") == []


def test_bad_destination_refused_before_any_ari_call(make_env: Callable[..., Env]) -> None:
    env = make_env()
    for bad in ("4155550123", "+1415;DROP", "+0123"):
        with pytest.raises(PolicyBlockedError):
            env.dialer.place_callback(bad, uuid.uuid4(), caller=env.caller(), max_seconds=30)
    assert env.ari.calls == []


def test_non_local_engine_blocks_the_callback(make_env: Callable[..., Env]) -> None:
    env = make_env(stt=ScriptedStt(local_only=False))
    with pytest.raises(PolicyBlockedError) as err:
        env.place()
    assert err.value.code == "CALLBACK_REMOTE_PROCESSING_BLOCKED"
    assert env.ari.find("POST", "/channels") == []


def test_originate_failure_surfaces_cleanly(make_env: Callable[..., Env]) -> None:
    env = make_env()
    env.ari.fail_substrings.add("POST /channels")
    with pytest.raises(PolicyBlockedError) as err:
        env.place()
    assert err.value.code == "DIALER_UNAVAILABLE" and env.dialer.active_calls == 0


# ------------------------------------------------------------------ timers
def test_max_duration_is_clamped_and_enforced(make_env: Callable[..., Env]) -> None:
    env = make_env()
    assert env.place(max_seconds=999).max_seconds == 120.0
    env2 = make_env()
    handle = env2.place(max_seconds=1)
    env2.answer(handle)
    started = time.monotonic()
    events: list[MediaEvent] = []
    while len(events) < 2 and time.monotonic() - started < 4:
        ev = handle.poll()
        if ev is not None:
            events.append(ev)
    assert [e.kind for e in events] == [MediaEventKind.ANSWERED, MediaEventKind.HANGUP]
    assert events[1].offset_seconds > 1.0  # beyond the limit, so the session ends MAX_DURATION
    wait_until_sync(lambda: bool(env2.ari.find("DELETE", handle.call_ref)))


# ------------------------------------------------------------------ call control
def test_dtmf_validates_and_sends(make_env: Callable[..., Env]) -> None:
    env = make_env()
    handle = env.place()
    env.answer(handle)
    handle.send_dtmf("2")
    handle.send_dtmf("1#")
    assert [p["dtmf"] for p in env.ari.find("POST", "/dtmf")] == ["2", "1#"]
    for bad in ("", "x", "12;rm", "9" * 40):
        with pytest.raises(ValueError):
            handle.send_dtmf(bad)


def test_speak_plays_tts_frames_and_reports_completion(make_env: Callable[..., Env]) -> None:
    env = make_env()
    handle = env.place()
    media = env.answer(handle)
    assert handle.speak("This is an automated assistant.") is True
    assert len(media.sink.frames) > 5


def test_speak_is_false_when_talked_over(make_env: Callable[..., Env]) -> None:
    env = make_env()
    handle = env.place()
    media = env.answer(handle)

    def talk_over(count: int) -> None:
        if count == 3:
            for _ in range(4):
                media.bridge.feed_audio(speech_payload())

    media.sink.on_send = talk_over
    assert handle.speak("one two three four five six seven eight nine ten eleven") is False


def test_hangup_is_idempotent_and_ends_the_call(make_env: Callable[..., Env]) -> None:
    env = make_env()
    handle = env.place()
    media = env.answer(handle)
    handle.hangup()
    handle.hangup()
    assert [c for c in env.ari.calls if c[0] == "DELETE" and handle.call_ref in c[1]]
    assert media.closed and env.dialer.active_calls == 0
    assert handle.speak("late") is False


# ------------------------------------------------------------------ events
def test_remote_end_before_and_after_answer(make_env: Callable[..., Env]) -> None:
    cases = [(17, MediaEventKind.BUSY), (19, MediaEventKind.NO_ANSWER), (41, MediaEventKind.FAILED)]
    for cause, expected in cases:
        env = make_env()
        handle = env.place()
        env.event({"type": "ChannelDestroyed", "cause": cause, "channel": {"id": handle.call_ref}})
        ev = handle.poll()
        assert ev is not None and ev.kind is expected
    env = make_env()
    handle = env.place()
    env.answer(handle)
    env.event({"type": "ChannelDestroyed", "cause": 16, "channel": {"id": handle.call_ref}})
    kinds = [handle.poll(), handle.poll(), handle.poll()]
    assert [k.kind if k else None for k in kinds] == [
        MediaEventKind.ANSWERED,
        MediaEventKind.HANGUP,
        None,
    ]


def test_ari_stream_loss_during_a_call_is_stream_lost(make_env: Callable[..., Env]) -> None:
    env = make_env()
    handle = env.place()
    env.answer(handle)
    env.on_loop(env.dialer.on_disconnect)
    kinds = []
    while (ev := handle.poll()) is not None:
        kinds.append(ev.kind)
    assert kinds == [MediaEventKind.ANSWERED, MediaEventKind.STREAM_LOST]
    wait_until_sync(lambda: env.dialer.active_calls == 0)


def test_events_for_unknown_channels_are_ignored(make_env: Callable[..., Env]) -> None:
    env = make_env()
    env.place()
    env.event({"type": "StasisStart", "channel": {"id": "someone-else"}})
    env.event({"type": "StasisStart", "channel": {}})
    assert not env.media


def test_poll_is_thread_safe_and_ordered(make_env: Callable[..., Env]) -> None:
    env = make_env()
    handle = env.place()
    env.answer(handle)
    assert handle.poll() is not None  # ANSWERED
    origin = time.monotonic() - 50.0

    def produce() -> None:
        for i in range(50):
            event = final(f"say {i}", i, i + 0.5)
            env.on_loop(functools.partial(handle.on_stt, [event], origin))

    producer = threading.Thread(target=produce)
    producer.start()
    seen: list[str] = []
    deadline = time.monotonic() + 5
    while len(seen) < 50 and time.monotonic() < deadline:
        ev = handle.poll()
        if ev is not None:
            seen.append(ev.text)
    producer.join()
    assert seen == [f"say {i}" for i in range(50)]


def test_poll_blocks_for_events_then_returns_none_after_the_pause(
    make_env: Callable[..., Env],
) -> None:
    env = make_env()
    handle = env.place()
    env.answer(handle)
    assert handle.poll() is not None
    started = time.monotonic()
    assert handle.poll() is None  # no speech at all: waits no_speech_s (0.3)
    assert 0.25 <= time.monotonic() - started < 2.0
    origin = time.monotonic() - 1.0
    env.on_loop(
        lambda: handle.on_stt([final("Hello, this is Dave from Acme Solar", 0.0, 1.0)], origin)
    )
    ev = handle.poll()
    assert ev is not None and ev.kind is MediaEventKind.SPEECH and ev.duration_seconds == 1.0
    started = time.monotonic()
    assert handle.poll() is None  # a business pause (0.1 s) ends the wait
    assert time.monotonic() - started < 1.0


# ------------------------------------------------------------------ engines & consent
def test_engine_failure_ends_the_call_with_stream_lost(make_env: Callable[..., Env]) -> None:
    env = make_env()
    env.stt.fail = True
    handle = env.place()
    media = env.answer(handle)
    assert handle.poll() is not None
    for _ in range(30):
        env.on_loop(lambda: media.bridge.feed_audio(speech_payload()))
    ev = None
    deadline = time.monotonic() + 3
    while (ev is None or ev.kind is not MediaEventKind.STREAM_LOST) and time.monotonic() < deadline:
        ev = handle.poll()
    assert ev is not None and ev.kind is MediaEventKind.STREAM_LOST
    wait_until_sync(
        lambda: any(m == "DELETE" and handle.call_ref in p for m, p, _ in env.ari.calls)
    )


def test_consent_denied_sends_no_audio_to_stt(make_env: Callable[..., Env]) -> None:
    env = make_env(gate=False)
    env.stt.queued = [final("should never be heard")]
    handle = env.place()
    media = env.answer(handle)
    for _ in range(40):
        env.on_loop(lambda: media.bridge.feed_audio(speech_payload()))
    time.sleep(0.3)
    assert env.stt.streams == []
    kinds = []
    while (ev := handle.poll()) is not None:
        kinds.append(ev.kind)
    assert MediaEventKind.SPEECH not in kinds


# ------------------------------------------------------------------ recording
def test_recording_only_after_the_disclosure_and_hash_recorded(
    make_env: Callable[..., Env], tmp_path: Path
) -> None:
    env = make_env(recording_dir=tmp_path / "rec")
    handle = env.place()
    media = env.answer(handle)
    with pytest.raises(PolicyBlockedError) as err:
        handle.start_recording()
    assert err.value.code == "RECORDING_BEFORE_DISCLOSURE" and not media.bridge.recording
    assert handle.speak("This is an automated assistant.") is True
    handle.start_recording()
    assert media.bridge.recording
    env.on_loop(lambda: media.bridge.feed_audio(speech_payload()))
    time.sleep(0.1)
    wav = handle.stop_recording()
    info = handle.recording_info
    assert info is not None and info.sha256 == hashlib.sha256(wav).hexdigest()
    assert info.path is not None and info.path.read_bytes() == wav


# ------------------------------------------------------------------ with the real session
def test_real_callback_session_drives_the_handle(make_env: Callable[..., Env]) -> None:
    env = make_env()
    handle = env.place()
    media = env.answer(handle)
    config = SessionConfig(
        template_id="v1",
        first_name="Jack",
        call_time=datetime(2026, 10, 27, 14, 5),
        outbound_recording=False,
        listen_window_seconds=10,
        bare_greeting_disclose_ms=1500,
        max_question_turns=4,
        max_call_seconds=120,
    )

    def counterparty() -> None:
        origin = time.monotonic() - 2.0
        env.on_loop(
            lambda: handle.on_stt([final("Hello, this is Dave from Acme Solar", 0, 2.0)], origin)
        )
        wait_until_sync(lambda: len(media.sink.frames) > 0)  # the disclosure is being spoken
        time.sleep(0.2)
        late = time.monotonic() - 1.0
        env.on_loop(lambda: handle.on_stt([final("Please stop calling this number", 0, 1.0)], late))

    thread = threading.Thread(target=counterparty)
    thread.start()
    result = CallbackSession(handle, config).run()
    thread.join()
    assert result.disclosed and result.outcome is Outcome.HARD_STOP and result.stop_requested
    assert result.listen is not None and result.listen.company_claimed
    assert handle.speak("late") is False  # hung up
