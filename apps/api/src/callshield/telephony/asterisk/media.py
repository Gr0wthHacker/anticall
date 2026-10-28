"""External-media (RTP) bridge between Asterisk and the local voice engines.

Rules enforced in this module, not left to callers:

* Caller audio reaches an STT engine ONLY while `AudioGate.allows_stt()` is true (the Defend
  processing-consent decision). A denied gate drops frames before any engine, queue or buffer
  sees them and never creates an STT stream.
* Audio is processed in RAM only. Nothing is written anywhere unless `start_recording()` is
  called, which callers do only after the disclosure has been spoken; the recorder holds no
  pre-start audio.
* Callbacks construct the bridge with `require_local=True`: any engine that is not local-only
  is refused (`callback.locality.assert_local_only` for endpoint-bearing engines).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import struct
import time
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from callshield.callback.locality import assert_local_only
from callshield.core.errors import PolicyBlockedError
from callshield.telephony.asterisk import audio
from callshield.telephony.asterisk._voice_ports import (
    Endpointer,
    SttEngine,
    SttEvent,
    SttStream,
    TtsEngine,
)
from callshield.telephony.asterisk.ari_client import AriApi, AriError
from callshield.telephony.asterisk.settings import AsteriskSettings

logger = logging.getLogger("callshield.asterisk.media")

FRAME_MS = 20
STT_CHUNK_BYTES = 3200  # 100 ms of 16 kHz PCM16
RTP_HEADER = 12
SttCallback = Callable[[list[SttEvent], float], None]  # (events, clock value at stream origin)
FailureCallback = Callable[[str], None]


class AudioGate(Protocol):
    def allows_stt(self) -> bool: ...


class StaticGate:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    def allows_stt(self) -> bool:
        return self.allowed


class PayloadSink(Protocol):
    def send(self, payload: bytes) -> None: ...


@dataclass(frozen=True)
class Codec:
    name: Literal["ulaw", "slin16"]
    wire_rate: int

    @property
    def frame_bytes_wire(self) -> int:
        per_sample = 1 if self.name == "ulaw" else 2
        return self.wire_rate * FRAME_MS // 1000 * per_sample

    def decode(self, payload: bytes) -> bytes:
        """RTP payload -> PCM16 LE at wire_rate."""
        return audio.ulaw_decode(payload) if self.name == "ulaw" else audio.swap_endian(payload)

    def encode(self, pcm16: bytes) -> bytes:
        return audio.ulaw_encode(pcm16) if self.name == "ulaw" else audio.swap_endian(pcm16)


def codec_for(name: Literal["ulaw", "slin16"]) -> Codec:
    return Codec("ulaw", 8000) if name == "ulaw" else Codec("slin16", 16000)


def assert_engines_local(*engines: object) -> None:
    """Callbacks: refuse any engine that is not explicitly local."""
    for engine in engines:
        if engine is None:
            continue
        if getattr(engine, "local_only", False) is not True:
            raise PolicyBlockedError(
                "callbacks may only use local speech processing",
                code="CALLBACK_REMOTE_PROCESSING_BLOCKED",
            )
        endpoint = getattr(engine, "endpoint", None)
        if endpoint is not None:
            assert_local_only(str(endpoint))


# ------------------------------------------------------------------ recording
class PostDisclosureRecorder:
    """Holds nothing until constructed; constructed only by `start_recording()`."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._t0 = clock()
        self._tracks: dict[str, bytearray] = {"callee": bytearray(), "assistant": bytearray()}

    def add(self, track: str, pcm16k: bytes, at: float | None = None) -> None:
        buf = self._tracks[track]
        position = int(((self._clock() if at is None else at) - self._t0) * audio.STT_RATE) * 2
        if position > len(buf):
            buf.extend(b"\x00" * (position - len(buf)))
        buf.extend(pcm16k)

    def wav_bytes(self) -> bytes:
        mixed = audio.mix_pcm16(bytes(self._tracks["callee"]), bytes(self._tracks["assistant"]))
        out = io.BytesIO()
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(audio.STT_RATE)
            wav.writeframes(mixed)
        return out.getvalue()


# ------------------------------------------------------------------ bridge
@dataclass
class _PlayItem:
    text: str
    done: asyncio.Future[bool]


class MediaBridge:
    def __init__(
        self,
        *,
        codec: Codec,
        stt: SttEngine | None,
        tts: TtsEngine | None,
        gate: AudioGate,
        on_stt: SttCallback,
        on_failure: FailureCallback | None = None,
        require_local: bool = False,
        endpointer: Endpointer | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
        paced: bool = True,
        barge_rms: float = 800.0,
        barge_frames: int = 3,
    ) -> None:
        if require_local:
            assert_engines_local(stt, tts)
        self._codec, self._stt, self._tts, self._gate = codec, stt, tts, gate
        self._on_stt, self._on_failure = on_stt, on_failure
        self._endpointer, self._clock, self._sleep, self._paced = endpointer, clock, sleep, paced
        self._barge_rms, self._barge_frames = barge_rms, barge_frames
        self._sink: PayloadSink | None = None
        self._pending = bytearray()
        self._stt_queue: asyncio.Queue[tuple[bytes, float] | None] | None = None
        self._play_queue: asyncio.Queue[_PlayItem] | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._stream: SttStream | None = None
        self._stream_origin: float | None = None
        self._pending_origin: float | None = None
        self._recorder: PostDisclosureRecorder | None = None
        self._playing = False
        self._interrupt = False
        self._loud = 0
        self._failed = False
        self.dropped_frames = 0

    # ------------------------------------------------------------------ lifecycle
    def attach_sink(self, sink: PayloadSink) -> None:
        self._sink = sink

    async def start(self) -> None:
        self._stt_queue = asyncio.Queue(maxsize=200)
        self._play_queue = asyncio.Queue()
        self._tasks = [
            asyncio.create_task(self._stt_worker()),
            asyncio.create_task(self._player()),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._fail_pending_playback()
        self._pending.clear()
        self._stream = None

    def _fail(self, reason: str, exc: BaseException | None = None) -> None:
        if self._failed:
            return
        self._failed = True
        logger.warning("media engine failure reason=%s type=%s", reason, type(exc).__name__)
        if self._on_failure is not None:
            self._on_failure(reason)

    # ------------------------------------------------------------------ caller audio in
    def feed_audio(self, payload: bytes) -> None:
        """One RTP payload (called on the event loop)."""
        wire_pcm = self._codec.decode(payload)
        self._watch_barge_in(wire_pcm)
        pcm = audio.resample_pcm16(wire_pcm, self._codec.wire_rate, audio.STT_RATE)
        if self._recorder is not None:
            self._recorder.add("callee", pcm)
        if self._stt is None or self._failed or not self._gate.allows_stt():
            self._drop_stt_state()
            return
        if not self._pending:
            self._pending_origin = self._clock()
        self._pending.extend(pcm)
        if len(self._pending) >= STT_CHUNK_BYTES:
            self._queue_chunk()

    def _drop_stt_state(self) -> None:
        self._pending.clear()
        self._stream = None
        self._stream_origin = None

    def _queue_chunk(self) -> None:
        queue = self._stt_queue
        chunk, origin = bytes(self._pending), self._pending_origin or self._clock()
        self._pending.clear()
        if queue is None or queue.full():
            self.dropped_frames += 1
            return
        queue.put_nowait((chunk, origin))

    async def _stt_worker(self) -> None:
        queue = self._stt_queue
        if queue is None or self._stt is None:
            return
        while True:
            item = await queue.get()
            if item is None:
                return
            chunk, origin = item
            if not self._gate.allows_stt():
                continue
            try:
                if self._stream is None:
                    self._stream = self._stt.new_stream(audio.STT_RATE)
                    self._stream_origin = origin
                events = await asyncio.to_thread(self._stream.feed, chunk)
            except Exception as exc:  # engine failure -> caller falls back; never raises here
                self._fail("stt_error", exc)
                return
            if events and self._gate.allows_stt() and self._stream_origin is not None:
                self._on_stt(events, self._stream_origin)

    def _watch_barge_in(self, wire_pcm: bytes) -> None:
        if not self._playing:
            self._loud = 0
            return
        self._loud = self._loud + 1 if audio.rms(wire_pcm) >= self._barge_rms else 0
        if self._loud >= self._barge_frames:
            self._interrupt = True

    # ------------------------------------------------------------------ playback out
    async def play(self, text: str) -> bool:
        """Queue `text` and wait. True only if it finished without barge-in or failure."""
        queue = self._play_queue
        if queue is None:
            return False
        done: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        queue.put_nowait(_PlayItem(text, done))
        return await done

    def enqueue(self, text: str) -> None:
        queue = self._play_queue
        if queue is not None:
            queue.put_nowait(_PlayItem(text, asyncio.get_running_loop().create_future()))

    def barge_in(self) -> None:
        """Abort the current utterance and drop everything queued behind it."""
        self._interrupt = True
        self._fail_pending_playback()

    def _fail_pending_playback(self) -> None:
        queue = self._play_queue
        while queue is not None and not queue.empty():
            item = queue.get_nowait()
            if not item.done.done():
                item.done.set_result(False)

    async def _player(self) -> None:
        queue = self._play_queue
        if queue is None:
            return
        while True:
            item = await queue.get()
            ok = False
            try:
                ok = await self._play_one(item.text)
            except Exception as exc:
                self._fail("tts_error", exc)
            finally:
                self._playing = False
            if not item.done.done():
                item.done.set_result(ok)

    async def _play_one(self, text: str) -> bool:
        if self._tts is None or self._sink is None or self._failed:
            return False
        pcm = await asyncio.to_thread(self._tts.synthesize, text)
        frames = self._frames(pcm)
        if self._recorder is not None:
            self._recorder.add(
                "assistant", audio.resample_pcm16(pcm, self._tts.sample_rate, audio.STT_RATE)
            )
        self._interrupt, self._playing, self._loud = False, True, 0
        next_at = self._clock()
        for frame in frames:
            if self._interrupt:
                return False
            self._sink.send(frame)
            next_at += FRAME_MS / 1000
            if self._paced:
                await self._sleep(max(next_at - self._clock(), 0.0))
        return not self._interrupt

    def _frames(self, pcm: bytes) -> list[bytes]:
        assert self._tts is not None
        wire = audio.resample_pcm16(pcm, self._tts.sample_rate, self._codec.wire_rate)
        payload = self._codec.encode(wire)
        size = self._codec.frame_bytes_wire
        return [payload[i : i + size] for i in range(0, len(payload), size)]

    # ------------------------------------------------------------------ recording
    def start_recording(self) -> None:
        if self._recorder is None:
            self._recorder = PostDisclosureRecorder(self._clock)

    def stop_recording(self) -> bytes:
        recorder, self._recorder = self._recorder, None
        return recorder.wav_bytes() if recorder is not None else b""

    @property
    def codec(self) -> Codec:
        return self._codec

    @property
    def recording(self) -> bool:
        return self._recorder is not None

    @property
    def failed(self) -> bool:
        return self._failed


# ------------------------------------------------------------------ RTP transport
class RtpEndpoint(asyncio.DatagramProtocol):
    """Minimal RTP: parse inbound payloads, build outbound packets. Learns the peer from the
    first packet (symmetric RTP) unless one was configured."""

    def __init__(self, codec: Codec, on_payload: Callable[[bytes], None]) -> None:
        self._codec = codec
        self._on_payload = on_payload
        self._transport: asyncio.DatagramTransport | None = None
        self.remote: tuple[str, int] | None = None
        self._seq = 0
        self._timestamp = 0
        self._ssrc = uuid.uuid4().int & 0xFFFFFFFF
        self._payload_type = 0 if codec.name == "ulaw" else 118

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) <= RTP_HEADER or data[0] >> 6 != 2:
            return
        if self.remote is None:
            self.remote = (addr[0], addr[1])
        elif addr[0] != self.remote[0]:
            return  # RTP from anyone but the media peer is ignored
        csrc = (data[0] & 0x0F) * 4
        self._payload_type = data[1] & 0x7F
        offset = RTP_HEADER + csrc
        if data[0] & 0x10 and len(data) >= offset + 4:
            offset += 4 + struct.unpack("!H", data[offset + 2 : offset + 4])[0] * 4
        self._on_payload(data[offset:])

    def send(self, payload: bytes) -> None:
        if self._transport is None or self.remote is None:
            return
        header = struct.pack(
            "!BBHII", 0x80, self._payload_type & 0x7F, self._seq, self._timestamp, self._ssrc
        )
        self._seq = (self._seq + 1) & 0xFFFF
        step = self._codec.wire_rate * FRAME_MS // 1000
        self._timestamp = (self._timestamp + step) & 0xFFFFFFFF
        self._transport.sendto(header + payload, self.remote)

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()


class MediaPortsExhaustedError(RuntimeError):
    pass


class PortAllocator:
    """Even UDP ports from base..base+2*count (odd ports are left for RTCP)."""

    def __init__(self, base: int, count: int) -> None:
        self._free = [base + 2 * i for i in range(count)]

    def acquire(self) -> int:
        if not self._free:
            raise MediaPortsExhaustedError("no free RTP ports")
        return self._free.pop(0)

    def release(self, port: int) -> None:
        if port not in self._free:
            self._free.insert(0, port)


class CallMedia(Protocol):
    bridge: MediaBridge

    async def open(self, channel_id: str) -> None: ...

    async def close(self) -> None: ...


class ExternalMediaSession:
    """Wires one call: mixing bridge + externalMedia channel <-> a local UDP endpoint."""

    def __init__(
        self,
        ari: AriApi,
        settings: AsteriskSettings,
        bridge: MediaBridge,
        ports: PortAllocator,
        app: str,
    ) -> None:
        self.bridge = bridge
        self._ari, self._settings, self._ports, self._app = ari, settings, ports, app
        self._rtp: RtpEndpoint | None = None
        self._port: int | None = None
        self._bridge_id = f"cs-br-{uuid.uuid4().hex[:12]}"
        self._ext_id = f"cs-em-{uuid.uuid4().hex[:12]}"

    async def open(self, channel_id: str) -> None:
        cfg = self._settings
        self._port = self._ports.acquire()
        try:
            rtp = RtpEndpoint(self.bridge.codec, self.bridge.feed_audio)
            self._rtp = rtp
            loop = asyncio.get_running_loop()
            await loop.create_datagram_endpoint(
                lambda: rtp, local_addr=(cfg.external_media_bind, self._port)
            )
            self.bridge.attach_sink(rtp)
            await self._ari.request(
                "POST", "/bridges", params={"type": "mixing", "bridgeId": self._bridge_id}
            )
            await self._ari.request(
                "POST",
                "/channels/externalMedia",
                params={
                    "app": self._app,
                    "external_host": f"{cfg.external_media_host}:{self._port}",
                    "format": cfg.codec,
                    "channelId": self._ext_id,
                },
            )
            await self._ari.request(
                "POST",
                f"/bridges/{self._bridge_id}/addChannel",
                params={"channel": f"{channel_id},{self._ext_id}"},
            )
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        for method, path in (
            ("DELETE", f"/channels/{self._ext_id}"),
            ("DELETE", f"/bridges/{self._bridge_id}"),
        ):
            with contextlib.suppress(AriError):  # already gone
                await self._ari.request(method, path)
        if self._rtp is not None:
            self._rtp.close()
            self._rtp = None
        if self._port is not None:
            self._ports.release(self._port)
            self._port = None
