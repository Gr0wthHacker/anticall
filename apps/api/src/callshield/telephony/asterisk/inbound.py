"""Inbound call control for protected lines.

No decision logic lives here. The Defend pipeline (`AriEventIngestor` -> routing policy ->
screening flow) returns `RouteInstruction`s; this controller only EXECUTES them over ARI:

* transfer  -> set the dial string and `continue` into the dialplan to ring the user's endpoint
* voicemail -> `continue` into the voicemail extension
* hangup    -> hang up
* answer+play -> answer, open the media bridge, speak the fixed disclosure, feed STT, and
                 turn each final utterance into a CALLER_UTTERANCE event for the pipeline

Safety net: any ARI/ingest/engine failure ends in `_fallback`, which hands the call to the
dialplan's ring-the-user extension. A call is never left to ring out or be dropped by us.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from callshield.telephony.asterisk._voice_ports import SttEngine, SttEvent, TtsEngine
from callshield.telephony.asterisk.ari_client import AriApi, AriError
from callshield.telephony.asterisk.events import (
    PROVIDER,
    AriEventIngestor,
    IngestResult,
    channel_id,
    fetch_attestation,
    is_inbound_start,
    map_ari_event,
)
from callshield.telephony.asterisk.loopthread import spawn
from callshield.telephony.asterisk.media import (
    CallMedia,
    ExternalMediaSession,
    MediaBridge,
    PortAllocator,
    codec_for,
)
from callshield.telephony.asterisk.settings import AsteriskSettings
from callshield.telephony.base import (
    MalformedEventError,
    ProviderCallEvent,
    ProviderEventType,
    RouteInstruction,
)

logger = logging.getLogger("callshield.asterisk.inbound")

_TARGET = re.compile(r"^\+?[0-9]{3,20}$")
# (tenant_id, line_id) -> (allow_live_stt, allow_remote_stt); evaluated by Defend consent.
ConsentProbe = Callable[[uuid.UUID, uuid.UUID], tuple[bool, bool]]
MediaFactory = Callable[[MediaBridge], CallMedia]


class ConsentAudioGate:
    """Caches Defend's processing decision; refreshes it off-loop so frames never block."""

    def __init__(
        self,
        probe: Callable[[], tuple[bool, bool]],
        stt: SttEngine | None,
        *,
        ttl: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._probe, self._stt, self._ttl, self._clock = probe, stt, ttl, clock
        self._value: tuple[bool, bool] = (False, False)  # default deny
        self._at = -1e9
        self._refreshing = False

    async def prime(self) -> None:
        self._value = await asyncio.to_thread(self._probe)
        self._at = self._clock()

    def allows_stt(self) -> bool:
        if self._clock() - self._at > self._ttl and not self._refreshing:
            self._refreshing = True
            spawn(self._refresh())
        allow_live, allow_remote = self._value
        local = bool(self._stt is not None and self._stt.local_only)
        return allow_live and (local or allow_remote)

    async def _refresh(self) -> None:
        try:
            await self.prime()
        except Exception as exc:  # keep the previous decision; the TTL retries
            logger.warning("consent refresh failed type=%s", type(exc).__name__)
            self._value = (False, False)  # fail closed
        finally:
            self._refreshing = False


@dataclass
class _Call:
    channel_id: str
    tenant_id: uuid.UUID | None = None
    line_id: uuid.UUID | None = None
    bridge: MediaBridge | None = None
    media: CallMedia | None = None
    watcher: asyncio.Task[None] | None = None
    utterances: int = 0
    closed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class InboundCallController:
    def __init__(
        self,
        ari: AriApi,
        ingestor: AriEventIngestor,
        settings: AsteriskSettings,
        *,
        stt: SttEngine | None,
        tts: TtsEngine | None,
        consent_probe: ConsentProbe,
        media_factory: MediaFactory | None = None,
    ) -> None:
        self._ari, self._ingestor, self._s = ari, ingestor, settings
        self._stt, self._tts, self._probe = stt, tts, consent_probe
        self._ports = PortAllocator(
            settings.external_media_port, settings.external_media_port_count
        )
        self._media_factory = media_factory or self._default_media
        self._calls: dict[str, _Call] = {}
        self._tracked: set[str] = set()

    def _default_media(self, bridge: MediaBridge) -> CallMedia:
        return ExternalMediaSession(self._ari, self._s, bridge, self._ports, self._s.app_name)

    def tracks(self, cid: str | None) -> bool:
        return cid is not None and cid in self._tracked

    # ------------------------------------------------------------------ entry
    async def handle_event(self, data: dict[str, Any]) -> None:
        cid = channel_id(data)
        if cid is None:
            return
        if is_inbound_start(data):
            self._tracked.add(cid)
            await self._on_start(cid, data)
        elif cid in self._tracked:
            await self._on_tracked_event(cid, data)

    async def _on_start(self, cid: str, data: dict[str, Any]) -> None:
        try:
            event = map_ari_event(data, await fetch_attestation(self._ari, cid))
            if event is None:
                return await self._fallback(cid, "unmapped_start")
            result = await self._ingestor.aingest(event)
            if result.outcome is None:
                return await self._fallback(cid, f"ingest_{result.reason or result.status}")
            call = self._calls.setdefault(cid, _Call(cid, result.tenant_id, result.line_id))
            if result.status == "duplicate" and call.bridge is not None:
                return  # redelivery of a call we are already driving
            await self._execute(call, result.outcome.instructions)
        except MalformedEventError:
            await self._fallback(cid, "malformed_event")
        except Exception as exc:
            logger.warning("inbound start failed call=%s type=%s", cid, type(exc).__name__)
            await self._fallback(cid, "controller_error")

    async def _on_tracked_event(self, cid: str, data: dict[str, Any]) -> None:
        try:
            event = map_ari_event(data)
        except MalformedEventError:
            return
        if event is None:
            return
        result = await self._ingestor.aingest(event)
        logger.debug("tracked event %s -> %s", event.event_type, result.status)
        if event.event_type == ProviderEventType.CALL_ENDED:
            await self._release(cid)
            self._tracked.discard(cid)
            self._calls.pop(cid, None)

    # ------------------------------------------------------------------ executing instructions
    async def _execute(self, call: _Call, instructions: list[RouteInstruction]) -> None:
        for instruction in instructions:
            if call.closed:
                return
            if await self._apply(call, instruction):
                return  # a terminal op handed the call off

    async def _apply(self, call: _Call, ins: RouteInstruction) -> bool:
        """Returns True when the call left our control (transfer/voicemail/hangup)."""
        if ins.op == "answer":
            await self._start_screening(call)
        elif ins.op == "play":
            if call.bridge is not None and ins.text:
                await call.bridge.play(ins.text)
        elif ins.op == "transfer":
            await self._transfer(call, ins.target)
        elif ins.op == "voicemail":
            await self._hand_off(call, self._s.voicemail_context, self._s.voicemail_extension)
        elif ins.op == "hangup":
            await self._hangup(call)
        else:
            await self._fallback(call.channel_id, "unknown_op")
        return ins.op in ("transfer", "voicemail", "hangup")

    async def _transfer(self, call: _Call, target: str | None) -> None:
        if target is None or not _TARGET.match(target):
            return await self._fallback(call.channel_id, "bad_transfer_target")
        number = target.lstrip("+") if self._s.dial_strip_plus else target
        dial = self._s.user_dial_format.format(number=number, trunk=self._s.trunk_endpoint)
        await self._hand_off(
            call,
            self._s.fallback_context,
            self._s.fallback_extension,
            variables={"CALLSHIELD_DIAL": dial},
        )

    async def _hand_off(
        self, call: _Call, context: str, extension: str, variables: dict[str, str] | None = None
    ) -> None:
        cid = call.channel_id
        await self._release(cid)
        try:
            await self._subscribe(cid)
            for name, value in (variables or {}).items():
                await self._ari.request(
                    "POST", f"/channels/{cid}/variable", params={"variable": name, "value": value}
                )
            params = {"context": context, "extension": extension, "priority": "1"}
            await self._ari.request("POST", f"/channels/{cid}/continue", params=params)
        except AriError:
            await self._fallback(cid, "handoff_failed")

    async def _subscribe(self, cid: str) -> None:
        """Keep receiving this channel's events after it leaves Stasis (answer/hangup)."""
        try:
            await self._ari.request(
                "POST",
                f"/applications/{self._s.app_name}/subscription",
                params={"eventSource": f"channel:{cid}"},
            )
        except AriError:
            logger.warning("could not subscribe to channel events call=%s", cid)

    async def _hangup(self, call: _Call) -> None:
        await self._release(call.channel_id)
        try:
            await self._ari.request("DELETE", f"/channels/{call.channel_id}")
        except AriError:
            logger.warning("hangup failed call=%s", call.channel_id)

    async def _fallback(self, cid: str, reason: str) -> None:
        """Ring the user (dialplan). Two attempts; never raises."""
        logger.warning("inbound fallback call=%s reason=%s", cid, reason)
        await self._release(cid)
        params = {
            "context": self._s.fallback_context,
            "extension": self._s.fallback_extension,
            "priority": "1",
        }
        for attempt in range(2):
            try:
                await self._ari.request("POST", f"/channels/{cid}/continue", params=params)
                return
            except AriError:
                await asyncio.sleep(0.2 * (attempt + 1))
        logger.error("fallback failed; Asterisk dialplan must catch this call=%s", cid)

    # ------------------------------------------------------------------ screening
    async def _start_screening(self, call: _Call) -> None:
        if self._stt is None or self._tts is None or call.tenant_id is None or call.line_id is None:
            return await self._screening_failed(call, "no_engines")
        tenant, line = call.tenant_id, call.line_id
        gate = ConsentAudioGate(lambda: self._probe(tenant, line), self._stt)
        await gate.prime()
        if not gate.allows_stt():
            return await self._screening_failed(call, "consent_denied")
        bridge = MediaBridge(
            codec=codec_for(self._s.codec),
            stt=self._stt,
            tts=self._tts,
            gate=gate,
            on_stt=lambda events, _origin: self._on_stt(call, events),
            on_failure=lambda reason: self._on_media_failure(call, reason),
        )
        call.bridge, call.media = bridge, self._media_factory(bridge)
        try:
            await self._ari.request("POST", f"/channels/{call.channel_id}/answer")
            await bridge.start()
            await call.media.open(call.channel_id)
        except Exception as exc:
            logger.warning("screening setup failed type=%s", type(exc).__name__)
            return await self._screening_failed(call, "setup_error")
        call.watcher = asyncio.ensure_future(self._no_speech_watch(call))

    async def _no_speech_watch(self, call: _Call) -> None:
        await asyncio.sleep(self._s.screening_no_speech_seconds)
        if call.utterances == 0 and not call.closed:
            await self._screening_failed(call, "no_speech")

    def _on_media_failure(self, call: _Call, reason: str) -> None:
        spawn(self._screening_failed(call, reason))

    def _on_stt(self, call: _Call, events: list[SttEvent]) -> None:
        for ev in events:
            if ev.is_final and ev.text.strip():
                spawn(self._utterance(call, ev.text.strip()))

    async def _utterance(self, call: _Call, text: str) -> None:
        async with call.lock:
            if call.closed:
                return
            call.utterances += 1
            event = _synthetic(ProviderEventType.CALLER_UTTERANCE, call, f"utt{call.utterances}")
            try:
                result = await self._ingestor.aingest(
                    event.model_copy(update={"text": text[:4000]})
                )
                await self._follow_up(call, result)
            except Exception as exc:
                logger.warning("utterance handling failed type=%s", type(exc).__name__)
                await self._fallback(call.channel_id, "utterance_error")

    async def _screening_failed(self, call: _Call, reason: str) -> None:
        async with call.lock:
            if call.closed:
                return
            logger.warning("screening failed call=%s reason=%s", call.channel_id, reason)
            event = _synthetic(ProviderEventType.SCREENING_TIMEOUT, call, "timeout")
            try:
                await self._follow_up(call, await self._ingestor.aingest(event))
            except Exception as exc:
                logger.warning("screening fallback failed type=%s", type(exc).__name__)
                await self._fallback(call.channel_id, "screening_error")

    async def _follow_up(self, call: _Call, result: IngestResult) -> None:
        if result.outcome is None or not result.outcome.instructions:
            return await self._fallback(call.channel_id, f"follow_up_{result.status}")
        await self._execute(call, result.outcome.instructions)

    async def _release(self, cid: str) -> None:
        call = self._calls.get(cid)
        if call is None:
            return
        call.closed = True
        if call.watcher is not None and call.watcher is not asyncio.current_task():
            call.watcher.cancel()
        if call.bridge is not None:
            await call.bridge.stop()
        if call.media is not None:
            await call.media.close()
        call.bridge = call.media = None

    async def close(self) -> None:
        for cid in list(self._calls):
            await self._release(cid)


def _synthetic(kind: ProviderEventType, call: _Call, tag: str) -> ProviderCallEvent:
    return ProviderCallEvent(
        provider=PROVIDER,
        event_id=f"{kind.value}:{call.channel_id}:{tag}",
        event_type=kind,
        call_id=call.channel_id,
    )
