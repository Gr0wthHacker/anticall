"""The long-running telephony process: ARI events -> Defend pipeline -> call control.

`python -m callshield.telephony.asterisk` refuses to start unless
`CALLSHIELD_ASTERISK_ENABLED=true`. Everything is injectable (`TelephonyRunner`) so tests
drive `route()` and `run()` with fakes; the real wiring lives in `build_runner`.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable
from typing import Any

from aiohttp import web

from callshield.core.config import Settings, get_settings
from callshield.db.session import make_engine, make_session_factory
from callshield.telephony.asterisk.ari_client import AriClient, AriError, AriHealth
from callshield.telephony.asterisk.consent import db_consent_probe
from callshield.telephony.asterisk.events import AriEventIngestor, channel_id, map_ari_event
from callshield.telephony.asterisk.factory import load_voice_engines
from callshield.telephony.asterisk.health import HealthPublisher
from callshield.telephony.asterisk.inbound import InboundCallController
from callshield.telephony.asterisk.settings import AsteriskSettings
from callshield.telephony.base import MalformedEventError

logger = logging.getLogger("callshield.asterisk.runner")
HEALTH_REPUBLISH_SECONDS = 30.0


class TelephonyRunner:
    def __init__(
        self,
        settings: AsteriskSettings,
        client: AriClient,
        ingestor: AriEventIngestor,
        controller: InboundCallController,
        publisher: HealthPublisher | None = None,
    ) -> None:
        self._s, self.client, self._ingestor = settings, client, ingestor
        self._controller, self._publisher = controller, publisher
        client.add_health_listener(self._on_health)

    def _on_health(self, health: AriHealth) -> None:
        if self._publisher is not None:
            self._publisher.on_health(health)

    async def route(self, data: dict[str, Any]) -> None:
        """One ARI event. Inbound calls go to the controller; link/registration events are
        ingested directly; everything else (e.g. callback legs of another app) is ignored."""
        etype = str(data.get("type"))
        if etype in ("PeerStatusChange", "EndpointStateChange"):
            await self._ingest_direct(data)
            return
        cid = channel_id(data)
        if cid is not None:
            await self._controller.handle_event(data)

    async def _ingest_direct(self, data: dict[str, Any]) -> None:
        try:
            event = map_ari_event(data)
        except MalformedEventError:
            logger.warning("malformed registration event dropped")
            return
        if event is not None:
            await self._ingestor.aingest(event)

    def health_snapshot(self) -> dict[str, Any]:
        h = self.client.health
        return {"status": h.status, "detail": h.detail, "reconnects": h.reconnects}

    async def run(self, stop: asyncio.Event) -> None:
        for warning in self._s.exposure_warnings():
            logger.warning("deployment warning: %s", warning)
        server = await self._start_health_server()
        beat = asyncio.ensure_future(self._heartbeat(stop))
        try:
            await self.client.run(self.route, stop)
        finally:
            beat.cancel()
            await self._controller.close()
            if server is not None:
                await server.cleanup()
            await self.client.aclose()

    async def _heartbeat(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self._subscribe_trunk()
            await self.client.probe()
            if self._publisher is not None:
                await self._publisher.publish(self.client.health)
            try:
                await asyncio.wait_for(stop.wait(), HEALTH_REPUBLISH_SECONDS)
            except TimeoutError:
                continue

    async def _subscribe_trunk(self) -> None:
        """Registration/endpoint state events only flow for endpoints the app subscribed to."""
        try:
            await self.client.request(
                "POST",
                f"/applications/{self._s.app_name}/subscription",
                params={"eventSource": f"endpoint:PJSIP/{self._s.trunk_endpoint}"},
            )
        except AriError:
            logger.warning("could not subscribe to trunk endpoint events")

    async def _start_health_server(self) -> web.AppRunner | None:
        if self._s.health_port == 0:
            return None

        async def healthz(_request: web.Request) -> web.Response:
            snap = self.health_snapshot()
            return web.json_response(snap, status=200 if snap["status"] == "OK" else 503)

        app = web.Application()
        app.router.add_get("/healthz", healthz)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, self._s.health_host, self._s.health_port).start()
        return runner


def build_runner(ast: AsteriskSettings, core: Settings) -> TelephonyRunner:
    factory = make_session_factory(make_engine(core.db_url))
    client = AriClient(ast, apps=(ast.app_name,))
    stt, tts = load_voice_engines()
    ingestor = AriEventIngestor(factory, core)
    controller = InboundCallController(
        client,
        ingestor,
        ast,
        stt=stt,
        tts=tts,
        consent_probe=db_consent_probe(factory, core),
    )
    return TelephonyRunner(ast, client, ingestor, controller, HealthPublisher(factory))


def _install_signals(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # Windows / non-main thread
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def amain(runner: TelephonyRunner, install: Callable[..., None] = _install_signals) -> None:
    stop = asyncio.Event()
    install(asyncio.get_running_loop(), stop)
    await runner.run(stop)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ast = AsteriskSettings()
    if not ast.enabled:
        logger.error("CALLSHIELD_ASTERISK_ENABLED is not true; the telephony runner will not start")
        return 2
    asyncio.run(amain(build_runner(ast, get_settings())))
    return 0
