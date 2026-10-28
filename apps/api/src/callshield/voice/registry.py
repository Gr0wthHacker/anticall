"""Engine factories. Lazy imports, friendly errors, and a hard local-only requirement."""

from __future__ import annotations

from callshield.callback.locality import assert_local_only
from callshield.core.errors import PolicyBlockedError
from callshield.voice.base import Endpointer, SttEngine, TtsEngine
from callshield.voice.config import VoiceSettings
from callshield.voice.endpoint import VadEndpointer
from callshield.voice.errors import VoiceEngineUnavailable
from callshield.voice.vad import EnergyVad, SileroVad, Vad

__all__ = [
    "VoiceEngineUnavailable",
    "assert_engine_local_only",
    "build_endpointer",
    "build_stt",
    "build_tts",
    "build_vad",
]


def assert_engine_local_only(engine: object) -> None:
    """Refuse any engine that does not declare `local_only=True` or points at a remote host."""
    if getattr(engine, "local_only", False) is not True:
        raise PolicyBlockedError(
            "callbacks may only use local speech/language processing",
            code="CALLBACK_REMOTE_PROCESSING_BLOCKED",
        )
    endpoint = getattr(engine, "endpoint", None)
    if endpoint is not None:
        assert_local_only(str(endpoint))


def build_vad(settings: VoiceSettings, sample_rate: int | None = None) -> Vad:
    rate = sample_rate or settings.sample_rate
    if settings.vad == "silero":
        return SileroVad(settings.vad_model_path or "", rate)
    return EnergyVad(rate, threshold_rms=settings.vad_energy_threshold)


def build_stt(settings: VoiceSettings) -> SttEngine:
    if settings.stt_engine == "none":
        raise VoiceEngineUnavailable(
            "speech-to-text is disabled. Set CALLSHIELD_VOICE_STT_ENGINE to 'faster-whisper' "
            "or 'vosk' (after `pip install 'callshield[voice]'` and downloading a model)."
        )
    engine: SttEngine
    if settings.stt_engine == "vosk":
        from callshield.voice.stt_vosk import VoskStt

        path = settings.stt_model_path()
        assert path is not None  # guaranteed by VoiceSettings validation
        engine = VoskStt.load(path)
    else:
        from callshield.voice.stt_faster_whisper import FasterWhisperStt

        engine = FasterWhisperStt.load(
            _whisper_model_ref(settings),
            vad_factory=lambda rate: build_vad(settings, rate),
            device=settings.stt_device,
            compute_type=settings.effective_compute_type,
            download_root=settings.stt_model_dir,
            language=settings.stt_language,
        )
    assert_engine_local_only(engine)
    return engine


def _whisper_model_ref(settings: VoiceSettings) -> str:
    local = settings.stt_model_path()
    if local is not None and local.is_dir():
        return str(local)
    return settings.effective_stt_model


def build_tts(settings: VoiceSettings) -> TtsEngine:
    if settings.tts_engine == "none":
        raise VoiceEngineUnavailable(
            "text-to-speech is disabled. Set CALLSHIELD_VOICE_TTS_ENGINE=piper (after "
            "`pip install 'callshield[voice]'` and downloading a piper voice)."
        )
    from callshield.voice.tts_piper import PiperTts

    path = settings.piper_model_path()
    assert path is not None  # guaranteed by VoiceSettings validation
    engine: TtsEngine = PiperTts.load(
        path, sample_rate=settings.sample_rate, use_cuda=settings.stt_device == "cuda"
    )
    assert_engine_local_only(engine)
    return engine


def build_endpointer(
    settings: VoiceSettings, bare_greeting_ms: int, pause_ms: int = 800
) -> Endpointer:
    return VadEndpointer(
        build_vad(settings),
        settings.sample_rate,
        pause_ms=pause_ms,
        bare_greeting_ms=bare_greeting_ms,
    )
