"""Media bridge: codec/resample, consent gate before STT, local-only, playback, barge-in,
post-disclosure-only recording, RTP framing."""

from __future__ import annotations

import asyncio
import io
import struct
import wave

import pytest

from callshield.core.errors import PolicyBlockedError
from callshield.telephony.asterisk import audio
from callshield.telephony.asterisk._voice_ports import SttEvent
from callshield.telephony.asterisk.media import (
    MediaBridge,
    MediaPortsExhaustedError,
    PortAllocator,
    RtpEndpoint,
    StaticGate,
    codec_for,
)
from callshield.voice.fakes import sine_pcm
from tests.asterisk_support import (
    ListSink,
    ScriptedStt,
    final,
    make_tts,
    run,
    speech_payload,
    wait_until,
)


def make_bridge(
    stt: ScriptedStt | None,
    gate: StaticGate,
    got: list[list[SttEvent]] | None = None,
    **kwargs: object,
) -> MediaBridge:
    sink_events = got if got is not None else []
    return MediaBridge(
        codec=codec_for("ulaw"),
        stt=stt,
        tts=make_tts(),
        gate=gate,
        on_stt=lambda events, _origin: sink_events.append(events),
        paced=False,
        **kwargs,  # type: ignore[arg-type]
    )


def test_ulaw_roundtrip_is_close() -> None:
    pcm = sine_pcm(0.02, 8000, amplitude=6000)
    back = audio.ulaw_decode(audio.ulaw_encode(pcm))
    assert len(back) == len(pcm)
    assert audio.rms(back) == pytest.approx(audio.rms(pcm), rel=0.05)


@pytest.mark.parametrize(("src", "dst"), [(8000, 16000), (22050, 8000), (16000, 16000)])
def test_resample_lengths(src: int, dst: int) -> None:
    pcm = sine_pcm(0.1, src)
    out = audio.resample_pcm16(pcm, src, dst)
    assert len(out) // 2 == pytest.approx(len(pcm) // 2 * dst / src, abs=2)


def test_slin16_is_network_byte_order() -> None:
    little = struct.pack("<hh", 1, -2)
    assert audio.swap_endian(little) == struct.pack(">hh", 1, -2)


def test_consent_denied_means_no_audio_reaches_stt() -> None:
    stt = ScriptedStt()

    async def scenario() -> None:
        bridge = make_bridge(stt, StaticGate(False))
        await bridge.start()
        for _ in range(30):
            bridge.feed_audio(speech_payload())
        await asyncio.sleep(0.2)
        await bridge.stop()

    run(scenario())
    assert stt.streams == [] and stt.bytes_fed == 0  # no stream was even created


def test_consent_allowed_feeds_16k_pcm_and_returns_events() -> None:
    stt = ScriptedStt()
    stt.queued = [final("hello", 0.0, 0.5)]
    got: list[list[SttEvent]] = []

    async def scenario() -> None:
        bridge = make_bridge(stt, StaticGate(True), got)
        await bridge.start()
        for _ in range(12):  # 240 ms of 8 kHz -> more than one 100 ms chunk at 16 kHz
            bridge.feed_audio(speech_payload())
        await wait_until(lambda: bool(got))
        await bridge.stop()

    run(scenario())
    assert got[0][0].text == "hello"
    assert stt.bytes_fed >= 3200


def test_gate_revoked_mid_call_stops_feeding() -> None:
    stt = ScriptedStt()
    gate = StaticGate(True)

    async def scenario() -> int:
        bridge = make_bridge(stt, gate)
        await bridge.start()
        for _ in range(12):
            bridge.feed_audio(speech_payload())
        await wait_until(lambda: stt.bytes_fed > 0)
        gate.allowed = False
        before = stt.bytes_fed
        for _ in range(30):
            bridge.feed_audio(speech_payload())
        await asyncio.sleep(0.2)
        await bridge.stop()
        return before

    before = run(scenario())
    assert stt.bytes_fed == before


def test_stt_failure_is_reported_once_not_raised() -> None:
    stt = ScriptedStt()
    stt.fail = True
    reasons: list[str] = []

    async def scenario() -> None:
        bridge = make_bridge(stt, StaticGate(True), on_failure=reasons.append)
        await bridge.start()
        for _ in range(30):
            bridge.feed_audio(speech_payload())
        await wait_until(lambda: bool(reasons))
        await bridge.stop()

    run(scenario())
    assert reasons == ["stt_error"]


def test_callbacks_refuse_non_local_engines() -> None:
    with pytest.raises(PolicyBlockedError) as err:
        make_bridge(ScriptedStt(local_only=False), StaticGate(True), require_local=True)
    assert err.value.code == "CALLBACK_REMOTE_PROCESSING_BLOCKED"
    make_bridge(ScriptedStt(local_only=True), StaticGate(True), require_local=True)  # ok

    class RemoteEndpoint(ScriptedStt):
        endpoint = "https://stt.example.test/v1"

    with pytest.raises(PolicyBlockedError):
        make_bridge(RemoteEndpoint(), StaticGate(True), require_local=True)


def test_playback_sends_whole_frames_and_completes() -> None:
    async def scenario() -> tuple[bool, ListSink]:
        bridge = make_bridge(None, StaticGate(False))
        sink = ListSink()
        bridge.attach_sink(sink)
        await bridge.start()
        ok = await bridge.play("this is the fixed disclosure text")
        await bridge.stop()
        return ok, sink

    ok, sink = run(scenario())
    assert ok and len(sink.frames) > 5
    assert all(len(f) == 160 for f in sink.frames[:-1])  # 20 ms of u-law


def test_barge_in_interrupts_playback() -> None:
    async def scenario() -> tuple[bool, ListSink]:
        bridge = make_bridge(None, StaticGate(False))
        sink = ListSink()
        bridge.attach_sink(sink)

        def talk_over(count: int) -> None:
            if count == 3:
                for _ in range(4):
                    bridge.feed_audio(speech_payload())

        sink.on_send = talk_over
        await bridge.start()
        ok = await bridge.play("one two three four five six seven eight nine ten")
        await bridge.stop()
        return ok, sink

    ok, sink = run(scenario())
    assert ok is False and len(sink.frames) < 40


def test_play_without_sink_is_false() -> None:
    async def scenario() -> bool:
        bridge = make_bridge(None, StaticGate(False))
        await bridge.start()
        ok = await bridge.play("hi")
        await bridge.stop()
        return ok

    assert run(scenario()) is False


def test_recorder_holds_nothing_before_start_and_only_post_start_audio() -> None:
    clock = [0.0]

    async def scenario() -> bytes:
        bridge = make_bridge(None, StaticGate(False), clock=lambda: clock[0])
        assert bridge.stop_recording() == b""
        for _ in range(10):  # pre-disclosure audio
            bridge.feed_audio(speech_payload())
            clock[0] += 0.02
        assert not bridge.recording
        bridge.start_recording()
        for _ in range(5):
            bridge.feed_audio(speech_payload())
            clock[0] += 0.02
        return bridge.stop_recording()

    wav_bytes = run(scenario())
    with wave.open(io.BytesIO(wav_bytes)) as wav:
        assert wav.getframerate() == 16000
        assert wav.getnframes() <= 5 * 320 + 40  # five 20 ms frames, not the earlier ten


def test_rtp_endpoint_parses_and_builds_packets() -> None:
    received: list[bytes] = []
    rtp = RtpEndpoint(codec_for("ulaw"), received.append)
    sent: list[tuple[bytes, tuple[str, int]]] = []

    class Capture(asyncio.DatagramTransport):
        def sendto(self, data: bytes, addr: object = None) -> None:  # type: ignore[override]
            sent.append((data, addr))  # type: ignore[arg-type]

    rtp.connection_made(Capture())
    header = struct.pack("!BBHII", 0x80, 0, 1, 160, 7)
    rtp.datagram_received(header + b"\x01" * 160, ("127.0.0.1", 4001))
    rtp.datagram_received(header + b"\x02" * 160, ("10.9.9.9", 4001))  # other host: ignored
    rtp.datagram_received(b"\x00" * 5, ("127.0.0.1", 4001))  # runt: ignored
    assert received == [b"\x01" * 160]
    rtp.send(b"\x7f" * 160)
    packet, addr = sent[0]
    assert addr == ("127.0.0.1", 4001) and packet[12:] == b"\x7f" * 160 and packet[0] >> 6 == 2


def test_port_allocator_exhausts_and_recycles() -> None:
    ports = PortAllocator(4000, 2)
    a, b = ports.acquire(), ports.acquire()
    assert (a, b) == (4000, 4002)
    with pytest.raises(MediaPortsExhaustedError):
        ports.acquire()
    ports.release(a)
    assert ports.acquire() == 4000
