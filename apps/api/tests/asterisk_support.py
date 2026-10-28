"""Fakes for the Asterisk integration tests. Fictional 555-01xx numbers only; no real network
beyond localhost aiohttp test servers."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

from callshield.telephony.asterisk import audio
from callshield.telephony.asterisk._voice_ports import SttEvent
from callshield.telephony.asterisk.ari_client import AriError
from callshield.telephony.asterisk.media import MediaBridge, StaticGate
from callshield.voice.fakes import FakeTts, sine_pcm

T = TypeVar("T")
LINE_E164 = "+14155550100"
USER_E164 = "+14155550101"
CALLER_E164 = "+14155550123"
PASSWORD = "hunter2-ari-secret"


def run(coro: Awaitable[T]) -> T:
    async def _wrapper() -> T:
        return await coro

    return asyncio.run(_wrapper())


async def wait_until(cond: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def wait_until_sync(cond: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.01)


class FakeAri:
    """In-process `AriApi`: records calls, serves channel variables, can fail on demand."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.variables: dict[str, str] = {}
        self.fail_substrings: set[str] = set()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
    ) -> Any:
        recorded = dict(params or {})
        self.calls.append((method, path, recorded))
        if any(needle in f"{method} {path}" for needle in self.fail_substrings):
            raise AriError(500, path)
        if path.endswith("/variable") and method == "GET":
            name = recorded.get("variable", "")
            if name not in self.variables:
                raise AriError(404, path)
            return {"value": self.variables[name]}
        return {}

    def find(self, method: str, suffix: str) -> list[dict[str, str]]:
        return [p for m, path, p in self.calls if m == method and path.endswith(suffix)]


class ScriptedStream:
    def __init__(self, engine: ScriptedStt) -> None:
        self.engine = engine
        self.fed = bytearray()

    def feed(self, pcm16: bytes) -> list[SttEvent]:
        if self.engine.fail:
            raise RuntimeError("engine exploded")
        self.fed.extend(pcm16)
        out, self.engine.queued = self.engine.queued, []
        return out

    def finish(self) -> list[SttEvent]:
        return []


class ScriptedStt:
    name = "scripted-stt"

    def __init__(self, local_only: bool = True) -> None:
        self.local_only = local_only
        self.streams: list[ScriptedStream] = []
        self.queued: list[SttEvent] = []
        self.fail = False

    def new_stream(self, sample_rate: int = 16000) -> ScriptedStream:
        stream = ScriptedStream(self)
        self.streams.append(stream)
        return stream

    @property
    def bytes_fed(self) -> int:
        return sum(len(s.fed) for s in self.streams)


def final(text: str, start: float = 0.0, end: float = 1.0) -> SttEvent:
    return SttEvent(text, True, start, end)


def make_tts() -> FakeTts:
    return FakeTts(sample_rate=16000, seconds_per_word=0.04)


class ListSink:
    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.on_send: Callable[[int], None] | None = None

    def send(self, payload: bytes) -> None:
        self.frames.append(payload)
        if self.on_send is not None:
            self.on_send(len(self.frames))


class FakeMedia:
    """CallMedia that attaches a ListSink instead of opening RTP."""

    def __init__(self, bridge: MediaBridge) -> None:
        self.bridge = bridge
        self.sink = ListSink()
        self.opened_for: str | None = None
        self.closed = False

    async def open(self, channel_id: str) -> None:
        self.opened_for = channel_id
        self.bridge.attach_sink(self.sink)

    async def close(self) -> None:
        self.closed = True


def speech_payload(ms: int = 20, loud: bool = True) -> bytes:
    """One u-law RTP payload (8 kHz): a tone (loud) or silence."""
    pcm = sine_pcm(ms / 1000, 8000, amplitude=9000 if loud else 0)
    return audio.ulaw_encode(pcm)


def static_gate(allowed: bool) -> StaticGate:
    return StaticGate(allowed)


def ari_start(
    cid: str, to: str = LINE_E164, caller: str = CALLER_E164, trunk: str = "trunk"
) -> dict[str, Any]:
    return {
        "type": "StasisStart",
        "timestamp": "2026-10-27T12:00:00.000+0000",
        "application": "callshield",
        "args": ["inbound", to],
        "channel": {
            "id": cid,
            "name": f"PJSIP/{trunk}-00000001",
            "state": "Ring",
            "caller": {"number": caller},
            "dialplan": {"exten": to},
        },
    }


def passed_identity(attest: str = "A") -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"attest": attest}).encode()).decode()
    return f"hdr.{claims.rstrip('=')}.sig;info=<https://example.test/cert>;ppt=shaken"


class FakeAriServer:
    """A localhost aiohttp ARI: Basic auth, a few REST routes, an events WebSocket."""

    def __init__(self, username: str = "callshield", password: str = PASSWORD) -> None:
        self.expected = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        self.requests: list[tuple[str, str]] = []
        self.ws_connections = 0
        self.sockets: list[web.WebSocketResponse] = []
        app = web.Application()
        app.router.add_get("/ari/asterisk/info", self._info)
        app.router.add_post("/ari/channels", self._originate)
        app.router.add_get("/ari/events", self._events)
        self.server = TestServer(app)

    def _authorized(self, request: web.Request) -> bool:
        return request.headers.get("Authorization") == self.expected

    async def _info(self, request: web.Request) -> web.Response:
        self.requests.append(("GET", request.path))
        if not self._authorized(request):
            return web.Response(status=401)
        return web.json_response({"system": {"version": "20.0.0"}})

    async def _originate(self, request: web.Request) -> web.Response:
        self.requests.append(("POST", request.path_qs))
        if not self._authorized(request):
            return web.Response(status=401)
        return web.json_response({"id": request.query.get("channelId", "x")})

    async def _events(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request):
            return web.Response(status=401)
        self.ws_connections += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
        return ws

    async def start(self) -> str:
        await self.server.start_server()
        return str(self.server.make_url("")).rstrip("/")

    async def stop(self) -> None:
        await self.server.close()

    async def push(self, event: dict[str, Any]) -> None:
        for ws in self.sockets:
            if not ws.closed:
                await ws.send_str(json.dumps(event))

    async def drop_clients(self) -> None:
        for ws in self.sockets:
            await ws.close()
        self.sockets.clear()
