"""ARI client against a localhost aiohttp fake: auth, events, reconnect/backoff, health, logs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from pydantic import ValidationError

from callshield.telephony.asterisk.ari_client import (
    STATUS_DEGRADED,
    STATUS_OK,
    AriClient,
    AriError,
    AriHealth,
    backoff_delay,
)
from callshield.telephony.asterisk.settings import AsteriskSettings
from tests.asterisk_support import PASSWORD, FakeAriServer, run, wait_until


def settings_for(url: str, password: str = PASSWORD) -> AsteriskSettings:
    return AsteriskSettings(
        enabled=True,
        ari_url=url,
        ari_password=password,
        reconnect_min_seconds=0.01,
        reconnect_max_seconds=0.04,
    )


async def instant_sleep(_delay: float) -> None:
    await asyncio.sleep(0.005)


def test_settings_defaults_are_off_and_secret_is_masked() -> None:
    s = AsteriskSettings(ari_password="hunter2")
    assert s.enabled is False
    assert "hunter2" not in repr(s) and "hunter2" not in str(s.model_dump())
    assert s.ws_url(("a", "b")) == "ws://127.0.0.1:8088/ari/events?app=a,b"
    assert any("empty" in w for w in AsteriskSettings().exposure_warnings())
    assert any(
        "plain http" in w
        for w in AsteriskSettings(ari_url="http://10.0.0.5:8088").exposure_warnings()
    )


def test_settings_reject_credentials_in_url_and_bad_dial_format() -> None:
    with pytest.raises(ValidationError):
        AsteriskSettings(ari_url="http://user:pw@127.0.0.1:8088")
    with pytest.raises(ValidationError):
        AsteriskSettings(dial_format="PJSIP/nonumber@trunk")
    with pytest.raises(ValidationError):
        AsteriskSettings(ari_url="ftp://x")


def test_backoff_is_exponential_and_capped() -> None:
    assert [backoff_delay(i, 0.5, 4.0) for i in range(5)] == [0.5, 1.0, 2.0, 4.0, 4.0]


def test_rest_with_auth_and_wrong_password_degrades_health(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    async def scenario() -> tuple[Any, AriHealth]:
        server = FakeAriServer()
        url = await server.start()
        good = AriClient(settings_for(url))
        info = await good.request("GET", "/asterisk/info")
        bad = AriClient(settings_for(url, "wrong-password-xyz"))
        with pytest.raises(AriError) as err:
            await bad.request("GET", "/asterisk/info")
        assert err.value.status == 401
        assert await bad.probe() is False
        health = bad.health
        await good.aclose()
        await bad.aclose()
        await server.stop()
        return info, health

    info, health = run(scenario())
    assert info["system"]["version"]
    assert (health.status, health.detail) == (STATUS_DEGRADED, "ari_auth_failed")
    assert PASSWORD not in caplog.text and "wrong-password-xyz" not in caplog.text


def test_events_are_delivered_and_health_turns_ok() -> None:
    got: list[dict[str, Any]] = []
    seen: list[str] = []

    async def scenario() -> None:
        server = FakeAriServer()
        url = await server.start()
        client = AriClient(settings_for(url), sleep=instant_sleep)
        client.add_health_listener(lambda h: seen.append(h.status))
        stop = asyncio.Event()

        async def handler(data: dict[str, Any]) -> None:
            got.append(data)
            if data.get("type") == "boom":
                raise RuntimeError("handler bug")  # must not kill the stream

        task = asyncio.create_task(client.run(handler, stop))
        await wait_until(lambda: client.health.connected)
        await server.push({"type": "boom"})
        await server.push({"type": "StasisStart", "n": 1})
        await wait_until(lambda: len(got) == 2)
        assert client.health.status == STATUS_OK and client.health.last_event_at is not None
        stop.set()
        await server.drop_clients()
        await asyncio.wait_for(task, 3)
        await client.aclose()
        await server.stop()

    run(scenario())
    assert [g["type"] for g in got] == ["boom", "StasisStart"]
    assert STATUS_OK in seen


def test_reconnects_after_drop_and_notifies_disconnect_hook() -> None:
    drops: list[bool] = []

    async def scenario() -> tuple[int, int]:
        server = FakeAriServer()
        url = await server.start()
        client = AriClient(settings_for(url), sleep=instant_sleep)
        client.add_disconnect_hook(lambda: drops.append(True))
        stop = asyncio.Event()

        async def handler(_data: dict[str, Any]) -> None:
            return None

        task = asyncio.create_task(client.run(handler, stop))
        await wait_until(lambda: server.ws_connections == 1 and client.health.connected)
        await server.drop_clients()
        await wait_until(lambda: server.ws_connections == 2 and client.health.connected)
        reconnects = client.health.reconnects
        stop.set()
        await server.drop_clients()
        await asyncio.wait_for(task, 3)
        await client.aclose()
        await server.stop()
        return server.ws_connections, reconnects

    connections, reconnects = run(scenario())
    assert connections >= 2 and reconnects >= 1 and drops


class RefusingSession:
    """Stands in for aiohttp.ClientSession when nothing listens (instant, any OS)."""

    def ws_connect(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionRefusedError("refused")

    async def close(self) -> None:
        return None


def test_unreachable_server_backs_off_and_reports_degraded() -> None:
    delays: list[float] = []

    async def scenario() -> AriHealth:
        stop = asyncio.Event()

        async def record(delay: float) -> None:
            delays.append(delay)
            if len(delays) >= 4:
                stop.set()
            await asyncio.sleep(0)

        client = AriClient(
            settings_for("http://127.0.0.1:9"),
            http=RefusingSession(),  # type: ignore[arg-type]
            sleep=record,
        )

        async def handler(_data: dict[str, Any]) -> None:
            return None

        await asyncio.wait_for(client.run(handler, stop), 5)
        return client.health

    health = run(scenario())
    assert delays[:3] == [0.01, 0.02, 0.04] and delays[3] == 0.04
    assert health.status == STATUS_DEGRADED and health.reconnects >= 3


def test_secrets_never_reach_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        server = FakeAriServer()
        url = await server.start()
        client = AriClient(settings_for(url, "totally-wrong"), sleep=instant_sleep)
        stop = asyncio.Event()

        async def handler(_data: dict[str, Any]) -> None:
            return None

        task = asyncio.create_task(client.run(handler, stop))
        await wait_until(lambda: client.health.detail == "ari_auth_failed")
        stop.set()
        await asyncio.wait_for(task, 3)
        await client.aclose()
        await server.stop()

    run(scenario())
    assert "totally-wrong" not in caplog.text and PASSWORD not in caplog.text
