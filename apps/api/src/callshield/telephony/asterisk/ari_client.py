"""Async ARI client: REST + events WebSocket with reconnect/backoff and a health status.

Credentials travel only in the HTTP Basic auth header, never in a URL, and are never logged.
Log lines carry exception TYPE names only (exception text can embed URLs or headers).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

import aiohttp

from callshield.telephony.asterisk.settings import AsteriskSettings

logger = logging.getLogger("callshield.asterisk.ari")

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]
HealthListener = Callable[["AriHealth"], None]
Sleeper = Callable[[float], Awaitable[None]]

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"
STATUS_UNKNOWN = "UNKNOWN"


class AriError(Exception):
    """An ARI request failed. `status` 0 means the server was unreachable."""

    def __init__(self, status: int, path: str) -> None:
        super().__init__(f"ARI {status} {path}")
        self.status = status
        self.path = path


class AriApi(Protocol):
    """What the rest of the package needs from ARI (fakeable in tests)."""

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
    ) -> Any: ...


@dataclass(frozen=True)
class AriHealth:
    status: str = STATUS_UNKNOWN
    detail: str = "not connected yet"
    connected: bool = False
    reconnects: int = 0
    last_event_at: float | None = None  # monotonic seconds


def backoff_delay(attempt: int, minimum: float, maximum: float) -> float:
    return float(min(maximum, minimum * (2 ** max(attempt, 0))))


def _detail_for(status: int) -> str:
    if status in (401, 403):
        return "ari_auth_failed"
    return "ari_unreachable" if status == 0 else f"ari_http_{status}"


class AriClient:
    def __init__(
        self,
        settings: AsteriskSettings,
        apps: Sequence[str] | None = None,
        *,
        http: aiohttp.ClientSession | None = None,
        sleep: Sleeper = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._apps = tuple(apps or (settings.app_name,))
        self._http = http
        self._owns_http = http is None
        self._sleep = sleep
        self._clock = clock
        token = f"{settings.ari_username}:{settings.ari_password.get_secret_value()}"
        self._headers = {"Authorization": "Basic " + base64.b64encode(token.encode()).decode()}
        self._health = AriHealth()
        self._health_listeners: list[HealthListener] = []
        self._disconnect_hooks: list[Callable[[], None]] = []

    # ------------------------------------------------------------------ health
    @property
    def health(self) -> AriHealth:
        return self._health

    def add_health_listener(self, listener: HealthListener) -> None:
        self._health_listeners.append(listener)

    def add_disconnect_hook(self, hook: Callable[[], None]) -> None:
        """Called (sync, from the loop) whenever an established event stream is lost."""
        self._disconnect_hooks.append(hook)

    def _set_health(self, status: str, detail: str, connected: bool) -> None:
        changed = (status, detail) != (self._health.status, self._health.detail)
        self._health = replace(self._health, status=status, detail=detail, connected=connected)
        if changed:
            for listener in list(self._health_listeners):
                try:
                    listener(self._health)
                except Exception as exc:  # a broken listener must not break the link
                    logger.warning("health listener failed type=%s", type(exc).__name__)

    # ------------------------------------------------------------------ REST
    async def _session(self) -> aiohttp.ClientSession:
        if self._http is None:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self._http

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
    ) -> Any:
        session = await self._session()
        url = f"{self._settings.ari_url}/ari{path}"
        try:
            async with session.request(
                method, url, params=params, json=json, headers=self._headers
            ) as resp:
                if resp.status >= 400:
                    raise AriError(resp.status, path)
                if resp.status == 204 or resp.content_length == 0:
                    return None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            logger.warning("ARI request failed path=%s type=%s", path, type(exc).__name__)
            raise AriError(0, path) from exc

    async def probe(self) -> bool:
        """GET /asterisk/info; updates health. Used by the runner's periodic check."""
        try:
            await self.request("GET", "/asterisk/info")
        except AriError as exc:
            self._set_health(STATUS_DEGRADED, _detail_for(exc.status), False)
            return False
        if not self._health.connected:
            self._set_health(STATUS_DEGRADED, "ari_rest_ok_events_down", False)
        return True

    # ------------------------------------------------------------------ events
    async def run(self, handler: EventHandler, stop: asyncio.Event) -> None:
        """Consume events until `stop` is set, reconnecting with exponential backoff."""
        attempt = 0
        while not stop.is_set():
            try:
                await self._consume(handler, stop)
                attempt = 0
            except aiohttp.WSServerHandshakeError as exc:
                self._set_health(STATUS_DEGRADED, _detail_for(exc.status), False)
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                logger.warning("ARI events unavailable type=%s", type(exc).__name__)
                self._set_health(STATUS_DEGRADED, "ari_unreachable", False)
            if stop.is_set():
                break
            self._health = replace(self._health, reconnects=self._health.reconnects + 1)
            await self._wait(stop, backoff_delay(attempt, *self._backoff()))
            attempt += 1
        self._set_health(STATUS_DEGRADED, "stopped", False)

    def _backoff(self) -> tuple[float, float]:
        return self._settings.reconnect_min_seconds, self._settings.reconnect_max_seconds

    async def _wait(self, stop: asyncio.Event, delay: float) -> None:
        waiter = asyncio.ensure_future(stop.wait())
        sleeper = asyncio.ensure_future(self._sleep(delay))
        try:
            await asyncio.wait({waiter, sleeper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (waiter, sleeper):
                task.cancel()

    async def _consume(self, handler: EventHandler, stop: asyncio.Event) -> None:
        session = await self._session()
        async with session.ws_connect(
            self._settings.ws_url(self._apps), headers=self._headers, heartbeat=20
        ) as ws:
            self._set_health(STATUS_OK, "connected", True)
            try:
                async for msg in ws:
                    if stop.is_set():
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._deliver(handler, msg.data)
                    elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        break
            finally:
                if not stop.is_set():
                    self._set_health(STATUS_DEGRADED, "ari_events_closed", False)
                    self._notify_disconnect()

    def _notify_disconnect(self) -> None:
        for hook in list(self._disconnect_hooks):
            try:
                hook()
            except Exception as exc:
                logger.warning("disconnect hook failed type=%s", type(exc).__name__)

    async def _deliver(self, handler: EventHandler, raw: str) -> None:
        try:
            data = json.loads(raw)
        except ValueError:
            logger.warning("ARI sent a non-JSON frame")
            return
        if not isinstance(data, dict):
            return
        self._health = replace(self._health, last_event_at=self._clock())
        try:
            await handler(data)
        except Exception as exc:  # one bad event must never stop the stream
            logger.warning("ARI event handler failed type=%s", type(exc).__name__)

    async def aclose(self) -> None:
        if self._http is not None and self._owns_http:
            await self._http.close()
        self._http = None
