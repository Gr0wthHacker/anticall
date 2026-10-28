"""Engine wrappers tested with fake third-party modules (no models, no network)."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from callshield.core.errors import PolicyBlockedError
from callshield.voice import models
from callshield.voice.base import SttEngine, SttEvent, TtsEngine
from callshield.voice.config import VoiceSettings
from callshield.voice.errors import VoiceEngineUnavailable
from callshield.voice.fakes import (
    FakeStt,
    FakeTts,
    ScriptedEvent,
    chunked,
    silence_pcm,
    sine_pcm,
)
from callshield.voice.registry import (
    assert_engine_local_only,
    build_endpointer,
    build_stt,
    build_tts,
)
from callshield.voice.stt_faster_whisper import FasterWhisperStt
from callshield.voice.vad import EnergyVad

# ------------------------------------------------------------------ settings


def test_everything_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    s = VoiceSettings()
    assert (s.stt_engine, s.tts_engine, s.vad, s.sample_rate) == ("none", "none", "energy", 16000)
    with pytest.raises(VoiceEngineUnavailable, match="disabled"):
        build_stt(s)
    with pytest.raises(VoiceEngineUnavailable, match="disabled"):
        build_tts(s)


def test_env_prefix_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLSHIELD_VOICE_STT_ENGINE", "faster-whisper")
    monkeypatch.setenv("CALLSHIELD_VOICE_STT_DEVICE", "cuda")
    s = VoiceSettings()
    assert s.effective_stt_model == "small.en"
    assert s.effective_compute_type == "float16"


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"stt_engine": "vosk"}, "STT_MODEL_DIR"),
        ({"tts_engine": "piper"}, "TTS_MODEL_DIR"),
        ({"vad": "silero"}, "VAD_MODEL_PATH"),
    ],
)
def test_validation_errors_say_what_to_set(kwargs: dict[str, Any], needle: str) -> None:
    with pytest.raises(ValueError, match=needle) as err:
        VoiceSettings(**kwargs)
    assert "callshield[voice]" in str(err.value)


def test_unknown_engine_names_are_rejected() -> None:
    with pytest.raises(ValueError):
        VoiceSettings(stt_engine="cloud-stt")


# ------------------------------------------------------------------ fakes and local-only


def test_fakes_satisfy_the_protocols() -> None:
    assert isinstance(FakeStt(), SttEngine)
    assert isinstance(FakeTts(), TtsEngine)


def test_fake_stt_releases_events_by_audio_clock() -> None:
    event = SttEvent("hello", True, 0.0, 1.0)
    stream = FakeStt(
        [event, ScriptedEvent(SttEvent("late", True, 1.0, 2.0), release_s=5.0)]
    ).new_stream()
    out: list[SttEvent] = []
    for piece in chunked(silence_pcm(1.5)):
        out += stream.feed(piece)
    assert out == [event]
    assert [e.text for e in stream.finish()] == ["late"]


def test_fake_tts_is_deterministic_pcm() -> None:
    tts = FakeTts(sample_rate=8000)
    assert tts.synthesize("one two three") == tts.synthesize("one two three")
    assert len(tts.synthesize("one two")) == round(0.6 * 8000) * 2


def test_non_local_engines_are_refused() -> None:
    class Remote:
        name = "remote"
        local_only = False

    class SneakyRemote:
        name = "sneaky"
        local_only = True
        endpoint = "https://stt.example.com/v1"

    with pytest.raises(PolicyBlockedError):
        assert_engine_local_only(Remote())
    with pytest.raises(PolicyBlockedError):
        assert_engine_local_only(SneakyRemote())
    assert_engine_local_only(FakeStt())


# ------------------------------------------------------------------ faster-whisper


class _Word:
    def __init__(self, start: float, end: float) -> None:
        self.start, self.end = start, end


class _Segment:
    def __init__(self, text: str, start: float, end: float, words: list[_Word] | None) -> None:
        self.text, self.start, self.end, self.words = text, start, end, words


class _FakeWhisperModel:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.init_args, self.init_kwargs = args, kwargs
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[Any, Any]:
        self.calls.append({"audio_len": len(audio), **kwargs})
        seg = _Segment(" hello there", 0.0, 1.0, [_Word(0.1, 0.9)])
        return iter([seg]), object()


def _speech_audio() -> bytes:
    return silence_pcm(0.5) + sine_pcm(1.0) + silence_pcm(1.5)


def _whisper(model: _FakeWhisperModel, **kw: Any) -> FasterWhisperStt:
    return FasterWhisperStt(model, lambda rate: EnergyVad(rate), audio_converter=lambda b: b, **kw)


def test_whisper_stream_emits_partials_then_one_final_with_word_times() -> None:
    model = _FakeWhisperModel()
    stream = _whisper(model, partial_interval_s=0.4).new_stream()
    events: list[SttEvent] = []
    for piece in chunked(_speech_audio()):
        events += stream.feed(piece)
    events += stream.finish()
    partials = [e for e in events if not e.is_final]
    finals = [e for e in events if e.is_final]
    assert len(partials) == 1  # identical partial text is de-duplicated
    assert len(finals) == 1
    assert finals[0].text == "hello there"
    assert finals[0].start_s == pytest.approx(0.25 + 0.1)  # 0.25 s preroll before speech at 0.5
    assert finals[0].end_s == pytest.approx(0.25 + 0.9)
    assert all(c["word_timestamps"] and c["vad_filter"] is False for c in model.calls)


def test_whisper_silence_never_calls_the_model() -> None:
    model = _FakeWhisperModel()
    stream = _whisper(model).new_stream()
    for piece in chunked(silence_pcm(3.0)):
        assert stream.feed(piece) == []
    assert stream.finish() == [] and model.calls == []


def test_whisper_finish_flushes_open_utterance() -> None:
    model = _FakeWhisperModel()
    stream = _whisper(model, partial_interval_s=60).new_stream()
    for piece in chunked(silence_pcm(0.3) + sine_pcm(0.5)):
        assert stream.feed(piece) == []
    assert [e.is_final for e in stream.finish()] == [True]


def test_whisper_resamples_8k_input_to_16k() -> None:
    model = _FakeWhisperModel()
    stream = _whisper(model, partial_interval_s=60).new_stream(8000)
    for piece in chunked(silence_pcm(0.3, 8000) + sine_pcm(0.5, 8000), 8000):
        stream.feed(piece)
    stream.finish()
    assert model.calls[0]["audio_len"] > 8000 * 0.5 * 2 * 0.9  # 16 kHz bytes, not 8 kHz


def test_whisper_load_is_offline_and_registry_wires_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = _FakeWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    settings = VoiceSettings(stt_engine="faster-whisper", stt_model_dir=str(tmp_path))
    engine = build_stt(settings)
    assert engine.name == "faster-whisper" and engine.local_only is True
    model = engine._model  # type: ignore[attr-defined]
    assert model.init_kwargs["local_files_only"] is True
    assert model.init_kwargs["compute_type"] == "int8"
    assert model.init_args == ("small.en",)
    (tmp_path / "small.en").mkdir()
    local = build_stt(settings)
    assert local._model.init_args == (str(tmp_path / "small.en"),)  # type: ignore[attr-defined]


def test_missing_libraries_give_install_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("faster_whisper", "vosk", "piper"):
        monkeypatch.setitem(sys.modules, name, None)
    (tmp_path / "v.onnx").write_text("x")
    cases = [
        VoiceSettings(stt_engine="faster-whisper"),
        VoiceSettings(stt_engine="vosk", stt_model_dir=str(tmp_path)),
        VoiceSettings(tts_engine="piper", tts_model_path=str(tmp_path / "v.onnx")),
    ]
    for settings in cases[:2]:
        with pytest.raises(VoiceEngineUnavailable, match=r"callshield\[voice\]"):
            build_stt(settings)
    with pytest.raises(VoiceEngineUnavailable, match=r"callshield\[voice\]"):
        build_tts(cases[2])


def test_whisper_model_load_failure_explains_offline_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Boom:
        def __init__(self, *a: Any, **k: Any) -> None:
            raise OSError("not cached")

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = Boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    with pytest.raises(VoiceEngineUnavailable, match="offline"):
        build_stt(VoiceSettings(stt_engine="faster-whisper"))


# ------------------------------------------------------------------ vosk


class _FakeRecognizer:
    def __init__(self, model: Any, rate: int) -> None:
        self.rate, self.calls, self.words = rate, 0, False

    def SetWords(self, on: bool) -> None:  # noqa: N802
        self.words = on

    def AcceptWaveform(self, data: bytes) -> bool:  # noqa: N802
        self.calls += 1
        return self.calls % 3 == 0

    def PartialResult(self) -> str:  # noqa: N802
        return json.dumps({"partial": "hello" if self.calls < 3 else "hello there"})

    def Result(self) -> str:  # noqa: N802
        words = [
            {"word": "hello", "start": 0.2, "end": 0.5},
            {"word": "there", "start": 0.6, "end": 1.0},
        ]
        return json.dumps({"text": "hello there", "result": words})

    def FinalResult(self) -> str:  # noqa: N802
        return json.dumps({"text": ""})


def _fake_vosk() -> types.ModuleType:
    mod = types.ModuleType("vosk")
    mod.Model = lambda model_path: ("model", model_path)  # type: ignore[attr-defined]
    mod.KaldiRecognizer = _FakeRecognizer  # type: ignore[attr-defined]
    mod.SetLogLevel = lambda level: None  # type: ignore[attr-defined]
    return mod


def test_vosk_streams_partials_and_finals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "vosk", _fake_vosk())
    (tmp_path / "vosk-model-small-en-us-0.15").mkdir()
    engine = build_stt(VoiceSettings(stt_engine="vosk", stt_model_dir=str(tmp_path)))
    stream = engine.new_stream()
    out: list[SttEvent] = []
    for piece in chunked(silence_pcm(0.12)):
        out += stream.feed(piece)
    out += stream.finish()
    assert [(e.text, e.is_final) for e in out] == [
        ("hello", False),
        ("hello there", True),
        ("hello there", False),
        ("hello there", True),
    ]
    finals = [e for e in out if e.is_final]
    assert finals[0].start_s == 0.2 and finals[0].end_s == 1.0


def test_vosk_missing_model_folder_is_friendly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(sys.modules, "vosk", _fake_vosk())
    with pytest.raises(VoiceEngineUnavailable, match="model folder not found"):
        build_stt(VoiceSettings(stt_engine="vosk", stt_model_dir=str(tmp_path)))


# ------------------------------------------------------------------ piper


class _FakeVoiceNew:
    class config:  # noqa: N801
        sample_rate = 22050

    def synthesize(self, text: str) -> Any:
        chunk = types.SimpleNamespace(audio_int16_bytes=sine_pcm(0.5, 22050))
        return iter([chunk, chunk])


class _FakeVoiceOld(_FakeVoiceNew):
    def synthesize_stream_raw(self, text: str) -> Any:
        return iter([sine_pcm(0.5, 22050), sine_pcm(0.5, 22050)])


@pytest.mark.parametrize("voice_cls", [_FakeVoiceNew, _FakeVoiceOld])
def test_piper_resamples_to_engine_rate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, voice_cls: type
) -> None:
    mod = types.ModuleType("piper")
    mod.PiperVoice = types.SimpleNamespace(load=lambda *a, **k: voice_cls())  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "piper", mod)
    model = tmp_path / "en_US-lessac-medium.onnx"
    model.write_bytes(b"x")
    (tmp_path / "en_US-lessac-medium.onnx.json").write_text("{}")
    tts = build_tts(
        VoiceSettings(tts_engine="piper", tts_model_dir=str(tmp_path), sample_rate=8000)
    )
    pcm = tts.synthesize("hello")
    assert tts.sample_rate == 8000 and tts.local_only is True
    assert len(pcm) == 8000 * 2  # one second of audio at 8 kHz, 16-bit


def test_piper_missing_voice_files_are_friendly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(sys.modules, "piper", types.ModuleType("piper"))
    with pytest.raises(VoiceEngineUnavailable, match="piper voice not found"):
        build_tts(VoiceSettings(tts_engine="piper", tts_model_dir=str(tmp_path)))


# ------------------------------------------------------------------ vad / models data


def test_silero_requires_model_file_and_supported_rate(tmp_path: Path) -> None:
    from callshield.voice.vad import SileroVad

    with pytest.raises(VoiceEngineUnavailable, match="not found"):
        SileroVad(str(tmp_path / "missing.onnx"))
    with pytest.raises(VoiceEngineUnavailable, match="8000 or 16000"):
        SileroVad(str(tmp_path / "missing.onnx"), sample_rate=44100)


def test_build_endpointer_uses_requested_thresholds() -> None:
    ep = build_endpointer(VoiceSettings(), bare_greeting_ms=600, pause_ms=400)
    events = ep.feed(sine_pcm(0.5) + silence_pcm(1.0))
    assert [(e.kind, e.pause_ms) for e in events] == [
        ("speech_start", 0),
        ("pause", 400),
        ("bare_greeting_silence", 600),
    ]


def test_models_listing_is_data_only() -> None:
    all_models = models.recommended_models()
    assert {m.kind for m in all_models} == {"stt", "tts", "vad"}
    assert all(m.url.startswith("https://") and m.approx_size_mb > 0 for m in all_models)
    assert models.models_for("stt", "vosk")
    assert {m.engine for m in models.models_for("tts")} == {"piper"}
