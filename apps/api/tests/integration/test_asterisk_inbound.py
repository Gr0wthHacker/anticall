# ruff: noqa: F811
"""InboundCallController: executes Defend's routing instructions over a fake ARI, and never
drops a call when ARI, the pipeline or an engine fails."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from callshield.core.config import Settings
from callshield.db.models.defend import CallSession
from callshield.db.models.defend_ext import CallRouting
from callshield.db.session import make_session_factory
from callshield.defend.verification import InMemoryOutboxChannel
from callshield.screening.templates import DISCLOSURE
from callshield.telephony.asterisk.consent import db_consent_probe
from callshield.telephony.asterisk.events import AriEventIngestor, IngestResult
from callshield.telephony.asterisk.inbound import InboundCallController
from callshield.telephony.asterisk.media import MediaBridge
from callshield.telephony.asterisk.settings import AsteriskSettings
from callshield.telephony.base import ProviderCallEvent
from tests.asterisk_support import (
    FakeAri,
    FakeMedia,
    ScriptedStt,
    ari_start,
    final,
    make_tts,
    run,
    speech_payload,
    wait_until,
)
from tests.conftest import TenantUser, TenantUserFactory
from tests.defend_support import (
    FICTIONAL_USER,
    LineHandle,
    defend_reset,  # noqa: F401
    enable_live_processing,
    enroll_line,
    rows,
    screening_settings,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("defend_reset")
SALES = "Hi this is Dave from Sunshine Solar, special offer on solar panels, limited time"
TRUSTED = "+14155550111"


@pytest.fixture
def settings(screening_settings: Settings) -> Settings:
    return screening_settings


@dataclass
class Rig:
    ari: FakeAri
    stt: ScriptedStt
    controller: InboundCallController
    media: list[FakeMedia] = field(default_factory=list)

    def continues(self) -> list[dict[str, str]]:
        return self.ari.find("POST", "/continue")

    def dial_variables(self) -> list[str]:
        return [p["value"] for p in self.ari.find("POST", "/variable")]


@pytest.fixture
def make_rig(engine: Engine, settings: Settings) -> Callable[..., Rig]:
    def build(
        *,
        probe: Callable[[Any, Any], tuple[bool, bool]] | None = None,
        ingestor: Any = None,
        no_speech: float = 5.0,
        tts: Any = None,
    ) -> Rig:
        ari, stt = FakeAri(), ScriptedStt()
        factory = make_session_factory(engine)
        media: list[FakeMedia] = []

        def media_factory(bridge: MediaBridge) -> FakeMedia:
            media.append(FakeMedia(bridge))
            return media[-1]

        controller = InboundCallController(
            ari,
            ingestor or AriEventIngestor(factory, settings),
            AsteriskSettings(enabled=True, screening_no_speech_seconds=no_speech),
            stt=stt,
            tts=tts or make_tts(),
            consent_probe=probe or db_consent_probe(factory, settings),
            media_factory=media_factory,
        )
        return Rig(ari, stt, controller, media)

    return build


@pytest.fixture
def owner(make_tenant_user: TenantUserFactory) -> TenantUser:
    return make_tenant_user("Owner")


@pytest.fixture
def plain_line(
    client: TestClient, owner: TenantUser, defend_reset: InMemoryOutboxChannel
) -> LineHandle:
    return enroll_line(client, owner, defend_reset, provider="ari")


@pytest.fixture
def screened_line(
    client: TestClient, owner: TenantUser, defend_reset: InMemoryOutboxChannel
) -> LineHandle:
    line = enroll_line(client, owner, defend_reset, provider="ari")
    enable_live_processing(client, line)
    resp = client.post(f"/v1/lines/{line.id}/screening:enable", headers=owner.headers)
    assert resp.status_code == 200, resp.text
    return line


class Boom:
    async def aingest(self, event: ProviderCallEvent) -> IngestResult:
        raise RuntimeError("database is on fire")


# ------------------------------------------------------------------ plain routing
def test_trusted_caller_rings_the_user_through_the_dialplan(
    client: TestClient, plain_line: LineHandle, make_rig: Callable[..., Rig]
) -> None:
    resp = client.post(
        "/v1/contacts",
        headers=plain_line.user.headers,
        json={"line_id": str(plain_line.id), "e164": "+14155550123", "kind": "TRUSTED"},
    )
    assert resp.status_code == 201
    rig = make_rig()
    run(rig.controller.handle_event(ari_start("c1", to=plain_line.e164)))
    assert rig.dial_variables() == [f"PJSIP/{FICTIONAL_USER}@trunk"]
    assert rig.continues() == [
        {"context": "callshield-route", "extension": "ring-user", "priority": "1"}
    ]
    assert rig.ari.find("POST", "/subscription")  # keeps receiving the channel's end events
    assert not rig.media and not rig.ari.find("POST", "/answer")


def test_unknown_caller_without_consent_goes_to_voicemail_without_audio(
    plain_line: LineHandle, make_rig: Callable[..., Rig], engine: Engine
) -> None:
    rig = make_rig()
    run(rig.controller.handle_event(ari_start("c1", to=plain_line.e164)))
    assert rig.continues() == [
        {"context": "callshield-route", "extension": "voicemail", "priority": "1"}
    ]
    assert not rig.media and rig.stt.streams == []
    assert rows(engine, CallSession)[0].provider_call_id == "c1"


def test_call_end_is_ingested_and_untracked(
    plain_line: LineHandle, make_rig: Callable[..., Rig], engine: Engine
) -> None:
    rig = make_rig()

    async def scenario() -> None:
        await rig.controller.handle_event(ari_start("c1", to=plain_line.e164))
        assert rig.controller.tracks("c1")
        await rig.controller.handle_event(
            {
                "type": "ChannelDestroyed",
                "timestamp": "2026-10-27T12:05:00.000+0000",
                "channel": {"id": "c1"},
            }
        )
        assert not rig.controller.tracks("c1")

    run(scenario())
    assert rows(engine, CallSession)[0].ended_at is not None


# ------------------------------------------------------------------ screening
def test_screening_discloses_feeds_stt_and_routes_on_the_utterance(
    screened_line: LineHandle, make_rig: Callable[..., Rig], engine: Engine
) -> None:
    rig = make_rig()
    rig.stt.queued = [final(SALES, 0.0, 3.0)]

    async def scenario() -> None:
        await rig.controller.handle_event(ari_start("c1", to=screened_line.e164))
        (media,) = rig.media
        assert rig.ari.find("POST", "/answer") and media.opened_for == "c1"
        assert len(media.sink.frames) > 10  # the fixed disclosure was spoken
        for _ in range(12):
            media.bridge.feed_audio(speech_payload())
        await wait_until(lambda: bool(rig.continues()))
        assert media.closed  # audio path torn down before handing the call off

    run(scenario())
    assert rig.continues()[0]["extension"] == "voicemail"  # UNSOLICITED_SALES -> voicemail
    assert rig.stt.bytes_fed > 0
    routing = rows(engine, CallRouting)[0]
    assert not routing.screening_active


def test_disclosure_text_is_the_fixed_template(
    screened_line: LineHandle, make_rig: Callable[..., Rig]
) -> None:
    spoken: list[str] = []
    tts = make_tts()
    original = tts.synthesize

    def spy(text: str) -> bytes:
        spoken.append(text)
        return original(text)

    tts.synthesize = spy  # type: ignore[method-assign]
    rig = make_rig(tts=tts)
    run(rig.controller.handle_event(ari_start("c1", to=screened_line.e164)))
    assert spoken == [DISCLOSURE]


def test_consent_denied_at_the_gate_means_no_answer_no_audio_and_a_fallback_route(
    screened_line: LineHandle, make_rig: Callable[..., Rig]
) -> None:
    rig = make_rig(probe=lambda _t, _l: (False, False))
    run(rig.controller.handle_event(ari_start("c1", to=screened_line.e164)))
    assert not rig.ari.find("POST", "/answer") and not rig.media
    assert rig.stt.streams == [] and rig.stt.bytes_fed == 0
    assert rig.continues()  # handed to ordinary routing, not dropped
    assert not rig.ari.find("DELETE", "c1")


def test_stt_engine_failure_falls_back_to_ringing_the_user(
    screened_line: LineHandle, make_rig: Callable[..., Rig]
) -> None:
    rig = make_rig()
    rig.stt.fail = True

    async def scenario() -> None:
        await rig.controller.handle_event(ari_start("c1", to=screened_line.e164))
        (media,) = rig.media
        for _ in range(12):
            media.bridge.feed_audio(speech_payload())
        await wait_until(lambda: bool(rig.continues()))

    run(scenario())
    assert rig.continues()[0]["extension"] == "ring-user"  # ai_failure_fallback = TRANSFER
    assert rig.dial_variables() == [f"PJSIP/{FICTIONAL_USER}@trunk"]
    assert not rig.ari.find("DELETE", "c1")


def test_ari_answer_failure_still_routes_the_call(
    screened_line: LineHandle, make_rig: Callable[..., Rig]
) -> None:
    rig = make_rig()
    rig.ari.fail_substrings.add("/answer")
    run(rig.controller.handle_event(ari_start("c1", to=screened_line.e164)))
    assert rig.continues() and rig.continues()[0]["extension"] == "ring-user"


def test_silent_caller_times_out_into_the_configured_fallback(
    screened_line: LineHandle, make_rig: Callable[..., Rig]
) -> None:
    rig = make_rig(no_speech=1.0)

    async def scenario() -> None:
        await rig.controller.handle_event(ari_start("c1", to=screened_line.e164))
        await wait_until(lambda: bool(rig.continues()), timeout=4)

    run(scenario())
    assert rig.continues()[0]["extension"] == "ring-user"


# ------------------------------------------------------------------ never drop a call
def test_pipeline_exception_rings_the_user(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(ingestor=Boom())
    run(rig.controller.handle_event(ari_start("c1")))
    assert rig.continues() == [
        {"context": "callshield-route", "extension": "ring-user", "priority": "1"}
    ]
    assert not rig.ari.find("DELETE", "c1")


def test_call_to_an_unprotected_number_is_handed_to_the_dialplan(
    plain_line: LineHandle, make_rig: Callable[..., Rig], engine: Engine
) -> None:
    rig = make_rig()
    run(rig.controller.handle_event(ari_start("c9", to="+14155550177")))
    assert rig.continues()[0]["extension"] == "ring-user"
    assert not rows(engine, CallSession)


def test_malformed_start_is_handed_to_the_dialplan(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    data = ari_start("c1")
    data["args"] = ["inbound", "not-a-number"]
    run(rig.controller.handle_event(data))
    assert rig.continues()[0]["extension"] == "ring-user"


def test_fallback_retries_once_when_ari_blips(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(ingestor=Boom())
    calls = {"n": 0}
    original = rig.ari.request

    async def flaky(method: str, path: str, **kwargs: Any) -> Any:
        if path.endswith("/continue"):
            calls["n"] += 1
            if calls["n"] == 1:
                from callshield.telephony.asterisk.ari_client import AriError

                raise AriError(503, path)
        return await original(method, path, **kwargs)

    rig.ari.request = flaky  # type: ignore[method-assign]
    run(rig.controller.handle_event(ari_start("c1")))
    assert calls["n"] == 2


def test_duplicate_start_while_screening_is_not_redriven(
    screened_line: LineHandle, make_rig: Callable[..., Rig]
) -> None:
    rig = make_rig()

    async def scenario() -> None:
        await rig.controller.handle_event(ari_start("c1", to=screened_line.e164))
        await rig.controller.handle_event(ari_start("c1", to=screened_line.e164))
        await asyncio.sleep(0.05)

    run(scenario())
    assert len(rig.ari.find("POST", "/answer")) == 1 and len(rig.media) == 1


def test_logs_carry_no_raw_numbers_or_speech(
    screened_line: LineHandle, make_rig: Callable[..., Rig], caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    rig = make_rig()
    rig.stt.queued = [final(SALES, 0.0, 3.0)]

    async def scenario() -> None:
        await rig.controller.handle_event(ari_start("c1", to=screened_line.e164))
        for _ in range(12):
            rig.media[0].bridge.feed_audio(speech_payload())
        await wait_until(lambda: bool(rig.continues()))

    run(scenario())
    for secret in ("4155550123", "4155550100", "Sunshine", "solar panels"):
        assert secret not in caplog.text
