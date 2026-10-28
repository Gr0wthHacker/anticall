"""Runner wiring, health endpoint, dialer selection (default off), entry point refusal."""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import aiohttp
import pytest

from callshield.callback.dialer_port import DisabledCallbackDialer
from callshield.core.config import Settings
from callshield.resolve.dialer import DisabledDialer
from callshield.telephony.asterisk import factory
from callshield.telephony.asterisk.ari_client import AriClient
from callshield.telephony.asterisk.events import IngestResult
from callshield.telephony.asterisk.runner import TelephonyRunner, main
from callshield.telephony.asterisk.settings import AsteriskSettings
from callshield.telephony.base import ProviderCallEvent, ProviderEventType
from tests.asterisk_support import PASSWORD, FakeAriServer, ari_start, run, wait_until


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class RecordingController:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.closed = False

    async def handle_event(self, data: dict[str, Any]) -> None:
        self.events.append(data)

    async def close(self) -> None:
        self.closed = True


class RecordingIngestor:
    def __init__(self) -> None:
        self.events: list[ProviderCallEvent] = []

    async def aingest(self, event: ProviderCallEvent) -> IngestResult:
        self.events.append(event)
        return IngestResult("accepted")


def test_runner_routes_events_serves_health_and_stops_cleanly() -> None:
    controller, ingestor = RecordingController(), RecordingIngestor()
    port = free_port()

    async def scenario() -> tuple[int, dict[str, Any]]:
        server = FakeAriServer()
        url = await server.start()
        settings = AsteriskSettings(
            enabled=True,
            ari_url=url,
            ari_password=PASSWORD,
            health_port=port,
            reconnect_min_seconds=0.01,
        )
        client = AriClient(settings)
        runner = TelephonyRunner(settings, client, ingestor, controller)  # type: ignore[arg-type]
        stop = asyncio.Event()
        task = asyncio.create_task(runner.run(stop))
        await wait_until(lambda: server.ws_connections == 1)
        await server.push(ari_start("c1"))
        await server.push({"type": "StasisStart", "channel": {}})  # no channel id: ignored
        await server.push(
            {
                "type": "PeerStatusChange",
                "timestamp": "2026-10-27T12:00:00.000+0000",
                "endpoint": {"resource": "trunk"},
                "peer": {"peer_status": "Unreachable"},
            }
        )
        await wait_until(lambda: bool(controller.events) and bool(ingestor.events))
        async with (
            aiohttp.ClientSession() as http,
            http.get(f"http://127.0.0.1:{port}/healthz") as r,
        ):
            status, body = r.status, await r.json()
        stop.set()
        await server.drop_clients()
        await asyncio.wait_for(task, 5)
        await server.stop()
        return status, body

    status, body = run(scenario())
    assert status == 200 and body["status"] == "OK"
    assert [e["channel"]["id"] for e in controller.events] == ["c1"]
    assert ingestor.events[0].event_type is ProviderEventType.REGISTRATION_FAILED
    assert controller.closed


def test_entry_point_refuses_to_start_unless_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CALLSHIELD_ASTERISK_ENABLED", raising=False)
    assert main() == 2


def test_everything_defaults_off() -> None:
    s = AsteriskSettings()
    assert s.enabled is False and s.max_call_seconds_cap <= 120
    assert s.ari_url.startswith("http://127.0.0.1")  # ARI is loopback by default
    assert s.external_media_bind == "127.0.0.1"


# ------------------------------------------------------------------ dialer selection
@pytest.fixture(autouse=True)
def _reset_factory(monkeypatch: pytest.MonkeyPatch) -> Any:
    factory.reset_asterisk_dialer()
    monkeypatch.delenv("CALLSHIELD_DIALER", raising=False)
    monkeypatch.delenv("CALLSHIELD_ASTERISK_ENABLED", raising=False)
    yield
    factory.reset_asterisk_dialer()


def test_selection_is_disabled_by_default_and_unless_every_switch_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[object] = []

    def fake_build(*_a: Any, **_k: Any) -> object:
        built.append(1)
        return object()

    monkeypatch.setattr(factory, "build_asterisk_dialer", fake_build)
    on = Settings(db_url="sqlite://", environment="test", outbound_enabled=True)
    off = Settings(db_url="sqlite://", environment="test", outbound_enabled=False)
    assert isinstance(factory.select_callback_dialer(on), DisabledCallbackDialer)  # no env
    monkeypatch.setenv("CALLSHIELD_DIALER", "asterisk")
    assert isinstance(factory.select_callback_dialer(off), DisabledCallbackDialer)  # outbound off
    assert isinstance(factory.select_callback_dialer(on), DisabledCallbackDialer)  # not enabled
    assert built == []
    monkeypatch.setenv("CALLSHIELD_ASTERISK_ENABLED", "true")
    sentinel = factory.select_callback_dialer(on)
    assert not isinstance(sentinel, DisabledCallbackDialer) and built == [1]
    assert factory.select_callback_dialer(on) is sentinel  # process-wide singleton


def test_resolve_deps_picks_asterisk_only_when_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from callshield.resolve.deps import get_dialer

    def request() -> Any:
        state = SimpleNamespace(
            settings=Settings(db_url="sqlite://", environment="prod", outbound_enabled=True)
        )
        return SimpleNamespace(app=SimpleNamespace(state=state))

    assert isinstance(get_dialer(request()), DisabledDialer)
    sentinel = object()
    monkeypatch.setattr(factory, "get_asterisk_dialer", lambda _s=None: sentinel)
    assert isinstance(get_dialer(request()), DisabledDialer)  # env not set
    monkeypatch.setenv("CALLSHIELD_DIALER", "asterisk")
    assert get_dialer(request()) is sentinel


def test_callback_runtime_defaults_to_the_disabled_dialer() -> None:
    from callshield.callback.runtime import build_default_runtime

    assert isinstance(build_default_runtime().dialer, DisabledCallbackDialer)


def test_legacy_ari_env_names_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLSHIELD_ARI_URL", "http://127.0.0.1:8088/ari")
    monkeypatch.setenv("CALLSHIELD_ARI_PASSWORD", "legacy-pw")
    monkeypatch.setenv("CALLSHIELD_ARI_APP", "legacyapp")
    monkeypatch.setenv("CALLSHIELD_ARI_ENABLED", "true")  # never enables the runner by itself
    s = AsteriskSettings()
    assert s.ari_url == "http://127.0.0.1:8088" and s.app_name == "legacyapp"
    assert s.ari_password.get_secret_value() == "legacy-pw" and s.enabled is False
    monkeypatch.setenv("CALLSHIELD_ASTERISK_ARI_URL", "http://127.0.0.1:9999")
    assert AsteriskSettings().ari_url == "http://127.0.0.1:9999"  # the new name wins
