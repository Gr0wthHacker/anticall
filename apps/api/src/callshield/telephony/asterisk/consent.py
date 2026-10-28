"""Bridges the Defend processing-consent module to the media audio gates (DB-backed)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from callshield.callback.dialer_port import VerifiedCaller
from callshield.core.config import Settings
from callshield.db.models.defend import ProtectedLine
from callshield.defend.lifecycle import processing_for
from callshield.policy.config import parse_routing_config
from callshield.telephony.asterisk._voice_ports import SttEngine
from callshield.telephony.asterisk.inbound import ConsentAudioGate, ConsentProbe
from callshield.telephony.asterisk.media import AudioGate


def _decision(session: Session, settings: Settings, line: ProtectedLine) -> tuple[bool, bool]:
    if not line.screening_enabled:
        return False, False
    decision = processing_for(session, settings, line, parse_routing_config(line.routing_config))
    return decision.allow_live_stt, decision.allow_remote_stt


def db_consent_probe(factory: sessionmaker[Session], settings: Settings) -> ConsentProbe:
    """(tenant_id, line_id) -> (allow_live_stt, allow_remote_stt), re-read on every call."""

    def probe(tenant_id: uuid.UUID, line_id: uuid.UUID) -> tuple[bool, bool]:
        with factory() as session:
            line = session.execute(
                select(ProtectedLine).where(
                    ProtectedLine.tenant_id == tenant_id, ProtectedLine.id == line_id
                )
            ).scalar_one_or_none()
            return (False, False) if line is None else _decision(session, settings, line)

    return probe


def callback_gate_factory(
    factory: sessionmaker[Session], settings: Settings, stt: SttEngine | None
) -> Callable[[VerifiedCaller], AudioGate]:
    """Gate for a callback leg: the verified caller's line must allow live STT."""

    def build(caller: VerifiedCaller) -> AudioGate:
        def probe() -> tuple[bool, bool]:
            with factory() as session:
                line = session.execute(
                    select(ProtectedLine).where(ProtectedLine.id == caller.line_id)
                ).scalar_one_or_none()
                return (False, False) if line is None else _decision(session, settings, line)

        return ConsentAudioGate(probe, stt)

    return build
