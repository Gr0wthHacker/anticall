"""Selecting and building the Asterisk dialer. Import-light on purpose: aiohttp and the heavy
modules load only inside the builder functions, so `callback.runtime` can import this freely.

The dialer is built ONLY when all hold: `CALLSHIELD_DIALER=asterisk`, core `outbound_enabled`,
and `CALLSHIELD_ASTERISK_ENABLED=true`. Otherwise callers get the disabled dialer.
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
from typing import TYPE_CHECKING

from callshield.callback.dialer_port import CallbackDialer, DisabledCallbackDialer
from callshield.core.config import Settings
from callshield.telephony.asterisk.settings import AsteriskSettings

if TYPE_CHECKING:
    from callshield.telephony.asterisk._voice_ports import SttEngine, TtsEngine
    from callshield.telephony.asterisk.dialer import AsteriskDialer

logger = logging.getLogger("callshield.asterisk.factory")
DIALER_ENV = "CALLSHIELD_DIALER"
_lock = threading.Lock()
_dialer: AsteriskDialer | None = None


def asterisk_selected() -> bool:
    return os.environ.get(DIALER_ENV, "").strip().lower() == "asterisk"


def load_voice_engines() -> tuple[SttEngine | None, TtsEngine | None]:
    """Local engines from `callshield.voice` settings; (None, None) parts when unavailable."""
    from callshield.voice.config import VoiceSettings
    from callshield.voice.errors import VoiceEngineUnavailable
    from callshield.voice.registry import build_stt, build_tts

    settings = VoiceSettings()
    stt: SttEngine | None = None
    tts: TtsEngine | None = None
    try:
        stt = build_stt(settings)
    except VoiceEngineUnavailable:
        logger.warning("no STT engine configured; screening falls back to ring/voicemail")
    try:
        tts = build_tts(settings)
    except VoiceEngineUnavailable:
        logger.warning("no TTS engine configured; the assistant cannot speak")
    return stt, tts


def build_asterisk_dialer(
    ast: AsteriskSettings | None = None, core: Settings | None = None
) -> AsteriskDialer:
    from callshield.core.config import get_settings
    from callshield.db.session import make_engine, make_session_factory
    from callshield.telephony.asterisk.ari_client import AriClient
    from callshield.telephony.asterisk.consent import callback_gate_factory
    from callshield.telephony.asterisk.dialer import AsteriskDialer
    from callshield.telephony.asterisk.loopthread import LoopThread

    ast, core = ast or AsteriskSettings(), core or get_settings()
    stt, tts = load_voice_engines()
    session_factory = make_session_factory(make_engine(core.db_url))
    loop = LoopThread()
    loop.start()
    client = AriClient(ast, apps=(ast.outbound_app_name,))
    dialer = AsteriskDialer(
        ast,
        client,
        loop,
        stt=stt,
        tts=tts,
        gate_factory=callback_gate_factory(session_factory, core, stt),
    )
    dialer.start()
    atexit.register(_shutdown, dialer, loop)
    return dialer


def _shutdown(dialer: AsteriskDialer, loop: object) -> None:
    dialer.stop()
    stop = getattr(loop, "stop", None)
    if callable(stop):
        stop(2.0)


def get_asterisk_dialer(core: Settings | None = None) -> AsteriskDialer | None:
    """Process-wide dialer, or None when the Asterisk dialer is not selected/enabled."""
    global _dialer
    from callshield.core.config import get_settings

    core = core or get_settings()
    if not (asterisk_selected() and core.outbound_enabled):
        return None
    with _lock:
        if _dialer is None:
            ast = AsteriskSettings()
            if not ast.enabled:
                return None
            _dialer = build_asterisk_dialer(ast, core)
    return _dialer


def reset_asterisk_dialer() -> None:
    global _dialer
    with _lock:
        _dialer = None


def select_callback_dialer(core: Settings) -> CallbackDialer | DisabledCallbackDialer:
    """The callback runtime's dialer: Asterisk when selected+enabled, else disabled."""
    try:
        dialer = get_asterisk_dialer(core)
    except ImportError:
        logger.error("CALLSHIELD_DIALER=asterisk needs the 'asterisk' extra (aiohttp)")
        return DisabledCallbackDialer()
    return dialer if dialer is not None else DisabledCallbackDialer()
