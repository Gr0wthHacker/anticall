"""Asterisk implementation of the callback dialer seam (`CallbackDialer`).

The callback session is synchronous and poll-based. This module bridges it to the asyncio ARI
world: an event loop runs on a background thread, ARI events become `MediaEvent`s in a
thread-safe queue stamped with REAL call-clock offsets, and `poll()` blocks in real time for
the next event, returning None only after the silence a session would act on (a business
pause, a bare greeting, an IVR gap, a missing reply, a missed answer).

Caller ID: the ONLY way to place a call is `place_callback(..., caller=VerifiedCaller)`. There
is no other caller-ID parameter or setting, and a non-`VerifiedCaller` is refused.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import hashlib
import logging
import queue
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from callshield.callback.dialer_port import MediaEvent, MediaEventKind, VerifiedCaller
from callshield.callback.extraction import GreetingType, classify_greeting
from callshield.core.errors import PolicyBlockedError
from callshield.ivr.parser import parse_menu
from callshield.resolve.dialer import CallHandle
from callshield.telephony.asterisk._voice_ports import SttEngine, SttEvent, TtsEngine
from callshield.telephony.asterisk.ari_client import AriApi, AriClient, AriError, EventHandler
from callshield.telephony.asterisk.events import channel_id
from callshield.telephony.asterisk.loopthread import LoopThread, spawn
from callshield.telephony.asterisk.media import (
    AudioGate,
    CallMedia,
    Codec,
    ExternalMediaSession,
    MediaBridge,
    PortAllocator,
    codec_for,
)
from callshield.telephony.asterisk.settings import AsteriskSettings

logger = logging.getLogger("callshield.asterisk.dialer")

_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_DTMF = re.compile(r"^[0-9A-D*#]{1,32}$")
_IVR_HINT = re.compile(
    r"\bpress\b|\bmenu\b|please hold|para espa|automated|this call (?:may be|is being) recorded",
    re.IGNORECASE,
)
_CAUSE_KIND = {
    16: MediaEventKind.NO_ANSWER,
    17: MediaEventKind.BUSY,
    18: MediaEventKind.NO_ANSWER,
    19: MediaEventKind.NO_ANSWER,
}
MediaFactory = Callable[[MediaBridge], CallMedia]
GateFactory = Callable[[VerifiedCaller], AudioGate]


@dataclass(frozen=True)
class HandleTiming:
    """Real-time silences that map to the session's "nothing more is coming" (poll -> None)."""

    business_pause_s: float = 0.8
    bare_greeting_s: float = 1.5
    no_speech_s: float = 10.0
    ivr_wait_s: float = 10.0
    reply_wait_s: float = 8.0
    ring_wait_s: float = 47.0
    speak_timeout_s: float = 60.0


@dataclass(frozen=True)
class RecordingInfo:
    path: Path | None
    sha256: str
    size_bytes: int


class AsteriskCallHandle:
    """`CallbackCallHandle` for one outbound leg. Methods without a leading underscore that
    are not `on_ari_event`/`on_disconnect` are called from the session thread."""

    def __init__(
        self,
        *,
        ari: AriApi,
        loop: LoopThread,
        channel_id: str,
        max_seconds: float,
        timing: HandleTiming | None = None,
        recording_dir: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_finished: Callable[[str], None] | None = None,
    ) -> None:
        self.call_ref = channel_id
        self.recording_info: RecordingInfo | None = None
        self._ari, self._loop, self._max = ari, loop, max_seconds
        self._timing = timing or HandleTiming()
        self._recording_dir, self._clock, self._on_finished = recording_dir, clock, on_finished
        self._events: queue.SimpleQueue[MediaEvent] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._t0 = clock()
        self._activity = self._t0
        self._answered = False
        self._finished = False
        self._speaks = 0
        self._disclosure_spoken = False
        self._dtmf_at: float | None = None
        self._last_text: str | None = None
        self._bridge: MediaBridge | None = None
        self._media: CallMedia | None = None
        self._media_factory: MediaFactory | None = None
        self._timer: asyncio.TimerHandle | None = None

    @property
    def max_seconds(self) -> float:
        return self._max

    # ------------------------------------------------------------------ wiring
    def bind_media(self, bridge: MediaBridge, factory: MediaFactory) -> None:
        self._bridge, self._media_factory = bridge, factory

    def _offset(self) -> float:
        return self._clock() - self._t0

    def _push(
        self,
        kind: MediaEventKind,
        text: str = "",
        duration: float = 0.0,
        offset: float | None = None,
    ) -> None:
        with self._lock:
            if self._finished and kind not in (MediaEventKind.HANGUP, MediaEventKind.STREAM_LOST):
                return
            at = self._offset() if offset is None else offset
            self._activity = self._clock()
            if kind is MediaEventKind.SPEECH:
                self._last_text = text
            self._events.put(MediaEvent(kind, text, at, duration))

    # ------------------------------------------------------------------ loop-thread side
    def arm_timer(self) -> None:
        self._timer = asyncio.get_running_loop().call_later(self._max, self._on_timeout)

    def _on_timeout(self) -> None:
        logger.info("callback hit max duration call=%s", self.call_ref)
        self._push(MediaEventKind.HANGUP, offset=self._max + 0.001)
        spawn(self._teardown(hangup=True))

    def on_stt(self, events: list[SttEvent], origin: float) -> None:
        for ev in events:
            if ev.is_final and ev.text.strip():
                self._push(
                    MediaEventKind.SPEECH,
                    ev.text.strip(),
                    max(ev.end_s - ev.start_s, 0.0),
                    offset=(origin - self._t0) + ev.end_s,
                )

    def on_media_failure(self, reason: str) -> None:
        logger.warning("callback media failure call=%s reason=%s", self.call_ref, reason)
        self._push(MediaEventKind.STREAM_LOST)
        spawn(self._teardown(hangup=True))

    def on_disconnect(self) -> None:
        """The ARI event stream dropped while this call was live: content is unknown now."""
        if not self._finished:
            self._push(MediaEventKind.STREAM_LOST)
            spawn(self._teardown(hangup=True))

    def on_ari_event(self, data: dict[str, Any]) -> None:
        kind = data.get("type")
        if kind == "StasisStart":
            self._answered = True
            self._push(MediaEventKind.ANSWERED)
            spawn(self._open_media())
        elif kind == "ChannelStateChange":
            if (data.get("channel") or {}).get("state") == "Ringing":
                self._push(MediaEventKind.RINGING)
        elif kind in ("ChannelDestroyed", "StasisEnd"):
            self._on_remote_end(data)

    def _on_remote_end(self, data: dict[str, Any]) -> None:
        if self._finished:
            return
        if self._answered:
            self._push(MediaEventKind.HANGUP)
        else:
            cause = data.get("cause")
            self._push(
                _CAUSE_KIND.get(cause, MediaEventKind.FAILED)
                if isinstance(cause, int)
                else MediaEventKind.FAILED
            )
        spawn(self._teardown(hangup=False))

    async def _open_media(self) -> None:
        if self._bridge is None or self._media_factory is None:
            return self.on_media_failure("no_media")
        try:
            self._media = self._media_factory(self._bridge)
            await self._bridge.start()
            await self._media.open(self.call_ref)
        except Exception as exc:
            logger.warning("media open failed type=%s", type(exc).__name__)
            self.on_media_failure("media_open")

    async def _teardown(self, *, hangup: bool) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
        if self._timer is not None:
            self._timer.cancel()
        if hangup:
            with contextlib.suppress(AriError):  # the channel may already be gone
                await self._ari.request("DELETE", f"/channels/{self.call_ref}")
        if self._bridge is not None:
            await self._bridge.stop()
        if self._media is not None:
            await self._media.close()
        if self._on_finished is not None:
            self._on_finished(self.call_ref)

    # ------------------------------------------------------------------ session-thread side
    def _idle_seconds(self) -> float:
        t = self._timing
        if not self._answered:
            return t.ring_wait_s
        if self._dtmf_at is not None and self._dtmf_at >= self._activity:
            return t.ivr_wait_s
        if self._speaks:
            return t.reply_wait_s
        text = self._last_text
        if text is None:
            return t.no_speech_s
        if parse_menu(text) or _IVR_HINT.search(text):
            return t.ivr_wait_s
        bare = classify_greeting(text) is GreetingType.BARE_GREETING
        return t.bare_greeting_s if bare else t.business_pause_s

    def poll(self) -> MediaEvent | None:
        while True:
            try:
                return self._events.get_nowait()
            except queue.Empty:
                pass
            if self._finished:
                return None
            remaining = self._activity + self._idle_seconds() - self._clock()
            if remaining <= 0:
                return None
            try:
                return self._events.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                continue

    def send_dtmf(self, digits: str) -> None:
        if not _DTMF.match(digits):
            raise ValueError("invalid DTMF digits")
        if self._finished:
            return
        params = {"dtmf": digits, "between": "120", "duration": "120"}
        self._run(self._ari.request("POST", f"/channels/{self.call_ref}/dtmf", params=params))
        self._dtmf_at = self._activity = self._clock()

    def speak(self, text: str) -> bool:
        bridge = self._bridge
        if self._finished or bridge is None:
            return False
        ok = False
        try:
            ok = bool(self._run(bridge.play(text), self._timing.speak_timeout_s))
        except Exception as exc:
            logger.warning("speak failed call=%s type=%s", self.call_ref, type(exc).__name__)
        self._speaks += 1
        self._activity = self._clock()
        if ok:
            self._disclosure_spoken = True
        return ok

    def hangup(self) -> None:
        if self._finished:
            return
        try:
            self._run(self._teardown(hangup=True), 10.0)
        except Exception as exc:
            logger.warning("hangup failed call=%s type=%s", self.call_ref, type(exc).__name__)

    def start_recording(self) -> None:
        if not self._disclosure_spoken or self._bridge is None:
            raise PolicyBlockedError(
                "recording may start only after the disclosure was spoken",
                code="RECORDING_BEFORE_DISCLOSURE",
            )
        self._bridge.start_recording()

    def stop_recording(self) -> bytes:
        wav = self._bridge.stop_recording() if self._bridge is not None else b""
        digest = hashlib.sha256(wav).hexdigest()
        path = self._write_recording(wav) if wav and self._recording_dir else None
        self.recording_info = RecordingInfo(path, digest, len(wav))
        return wav

    def _write_recording(self, wav: bytes) -> Path | None:
        assert self._recording_dir is not None
        try:
            self._recording_dir.mkdir(parents=True, exist_ok=True)
            path = self._recording_dir / f"{self.call_ref}.wav"
            path.write_bytes(wav)
            path.chmod(0o600)
            return path
        except OSError as exc:
            logger.warning("recording write failed type=%s", type(exc).__name__)
            return None

    def _run(self, coro: Any, timeout: float = 15.0) -> Any:
        future: concurrent.futures.Future[Any] = self._loop.submit(coro)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise


class AsteriskDialer:
    """`CallbackDialer` over ARI. Construct once per process; `start()` begins consuming
    events when the dialer owns an `AriClient`."""

    kind = "asterisk"
    configured = True

    def __init__(
        self,
        settings: AsteriskSettings,
        ari: AriApi,
        loop: LoopThread,
        *,
        stt: SttEngine | None,
        tts: TtsEngine | None,
        gate_factory: GateFactory,
        media_factory: Callable[[MediaBridge], CallMedia] | None = None,
        timing: HandleTiming | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._s, self._ari, self._loop = settings, ari, loop
        self._stt, self._tts, self._gate_factory = stt, tts, gate_factory
        self._timing = timing or HandleTiming(ring_wait_s=settings.ring_timeout_seconds + 2.0)
        self._ports = PortAllocator(
            settings.external_media_port, settings.external_media_port_count
        )
        self._media_factory = media_factory or self._default_media
        self._clock = clock
        self._handles: dict[str, AsteriskCallHandle] = {}
        self._stop: asyncio.Event | None = None
        self._task: concurrent.futures.Future[None] | None = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Run the ARI event loop (only when `ari` is an AriClient)."""
        client = self._ari
        if not isinstance(client, AriClient) or self._task is not None:
            return
        self._loop.start()
        client.add_disconnect_hook(self.on_disconnect)

        async def _go() -> None:
            self._stop = asyncio.Event()
            await client.run(self.dispatch_event, self._stop)

        self._task = self._loop.submit(_go())

    def close_all(self) -> None:
        """Hang up every live call (shutdown). Safe to call from any thread."""
        for handle in list(self._handles.values()):
            handle.hangup()

    def stop(self) -> None:
        if self._loop.running:
            self.close_all()
        if self._stop is not None and self._loop.running:
            self._loop.call_soon(self._stop.set)

    # ------------------------------------------------------------------ OutboundDialer seam
    def place_call(
        self, destination_e164: str, session_id: uuid.UUID, *, max_seconds: int
    ) -> CallHandle:
        raise PolicyBlockedError("use place_callback with a verified caller", code="NO_CALLER_ID")

    def place_callback(
        self,
        destination_e164: str,
        session_id: uuid.UUID,
        *,
        caller: VerifiedCaller,
        max_seconds: int,
    ) -> AsteriskCallHandle:
        if not isinstance(caller, VerifiedCaller):
            raise PolicyBlockedError("caller ID must be a VerifiedCaller", code="NO_CALLER_ID")
        if not _E164.match(destination_e164):
            raise PolicyBlockedError("destination is not E.164", code="BAD_DESTINATION")
        limit = float(max(1, min(max_seconds, self._s.max_call_seconds_cap)))
        self._loop.start()
        handle = self._build_handle(session_id, limit, caller)
        try:
            self._loop.submit(self._originate(handle, destination_e164, caller)).result(30)
        except Exception as exc:
            self._handles.pop(handle.call_ref, None)
            logger.warning("originate failed type=%s", type(exc).__name__)
            raise PolicyBlockedError("could not place the call", code="DIALER_UNAVAILABLE") from exc
        return handle

    def _build_handle(
        self, session_id: uuid.UUID, limit: float, caller: VerifiedCaller
    ) -> AsteriskCallHandle:
        cid = f"cs-cb-{session_id.hex[:16]}-{uuid.uuid4().hex[:6]}"
        handle = AsteriskCallHandle(
            ari=self._ari,
            loop=self._loop,
            channel_id=cid,
            max_seconds=limit,
            timing=self._timing,
            recording_dir=self._s.recording_dir,
            clock=self._clock,
            on_finished=self._forget,
        )
        bridge = MediaBridge(
            codec=codec_for(self._s.codec),
            stt=self._stt,
            tts=self._tts,
            gate=self._gate_factory(caller),
            on_stt=handle.on_stt,
            on_failure=handle.on_media_failure,
            require_local=True,  # callbacks: local engines only, else PolicyBlockedError
        )
        handle.bind_media(bridge, self._media_factory)
        self._handles[cid] = handle
        return handle

    def _forget(self, call_ref: str) -> None:
        self._handles.pop(call_ref, None)

    def _default_media(self, bridge: MediaBridge) -> CallMedia:
        return ExternalMediaSession(
            self._ari, self._s, bridge, self._ports, self._s.outbound_app_name
        )

    async def _originate(
        self, handle: AsteriskCallHandle, destination: str, caller: VerifiedCaller
    ) -> None:
        number = destination[1:] if self._s.dial_strip_plus else destination
        params = {
            "endpoint": self._s.dial_format.format(number=number, trunk=self._s.trunk_endpoint),
            "app": self._s.outbound_app_name,
            "appArgs": "outbound",
            "channelId": handle.call_ref,
            "callerId": caller.e164,  # the verified line, never a free parameter
            "timeout": str(self._s.ring_timeout_seconds),
        }
        handle.arm_timer()
        await self._ari.request("POST", "/channels", params=params)

    # ------------------------------------------------------------------ event routing
    async def dispatch_event(self, data: dict[str, Any]) -> None:
        cid = channel_id(data)
        handle = self._handles.get(cid) if cid else None
        if handle is not None:
            handle.on_ari_event(data)

    def on_disconnect(self) -> None:
        for handle in list(self._handles.values()):
            handle.on_disconnect()

    @property
    def active_calls(self) -> int:
        return len(self._handles)


__all__ = [
    "AsteriskCallHandle",
    "AsteriskDialer",
    "Codec",
    "EventHandler",
    "HandleTiming",
    "RecordingInfo",
]
