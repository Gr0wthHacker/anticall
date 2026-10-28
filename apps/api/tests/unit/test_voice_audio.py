"""Voice audio primitives: G.711 vectors, resampling, energy VAD, endpointing (synthetic PCM)."""

from __future__ import annotations

import pytest

from callshield.voice import pcm
from callshield.voice.base import EndpointEvent
from callshield.voice.endpoint import VadEndpointer
from callshield.voice.fakes import chunked, silence_pcm, sine_pcm
from callshield.voice.resample import (
    alaw_decode,
    alaw_encode,
    downsample_16k_to_8k,
    resample_pcm16,
    ulaw_decode,
    ulaw_encode,
    upsample_8k_to_16k,
)
from callshield.voice.vad import EnergyVad


def _values(data: bytes) -> list[int]:
    return list(pcm.to_samples(data))


# ------------------------------------------------------------------ G.711


@pytest.mark.parametrize(
    ("byte", "expected"), [(0xFF, 0), (0x7F, 0), (0x00, -32124), (0x80, 32124), (0xBF, 1980)]
)
def test_ulaw_decode_known_vectors(byte: int, expected: int) -> None:
    assert _values(ulaw_decode(bytes([byte]))) == [expected]


@pytest.mark.parametrize(
    ("byte", "expected"), [(0xD5, 8), (0x55, -8), (0xAA, 32256), (0x2A, -32256), (0xD4, 24)]
)
def test_alaw_decode_known_vectors(byte: int, expected: int) -> None:
    assert _values(alaw_decode(bytes([byte]))) == [expected]


def test_ulaw_encode_known_vectors() -> None:
    assert ulaw_encode(pcm.from_samples([0, 32124, -32124, 32767])) == bytes(
        [0xFF, 0x80, 0x00, 0x80]
    )


def test_alaw_encode_known_vectors() -> None:
    assert alaw_encode(pcm.from_samples([8, -8, 32256])) == bytes([0xD5, 0x55, 0xAA])


@pytest.mark.parametrize(("enc", "dec"), [(ulaw_encode, ulaw_decode), (alaw_encode, alaw_decode)])
def test_g711_roundtrip_error_is_bounded(enc, dec) -> None:  # type: ignore[no-untyped-def]
    samples = list(range(-32768, 32768, 257))
    back = _values(dec(enc(pcm.from_samples(samples))))
    for original, restored in zip(samples, back, strict=True):
        assert abs(original - restored) <= max(40, abs(original) * 0.04)


@pytest.mark.parametrize("decode", [ulaw_decode, alaw_decode])
def test_g711_decode_is_idempotent_through_reencode(decode) -> None:  # type: ignore[no-untyped-def]
    every_code = bytes(range(256))
    encode = ulaw_encode if decode is ulaw_decode else alaw_encode
    again = decode(encode(decode(every_code)))
    assert again == decode(every_code)


# ------------------------------------------------------------------ resampling


def test_resample_lengths_and_identity() -> None:
    tone = sine_pcm(0.5, 8000)
    assert resample_pcm16(tone, 8000, 8000) == tone
    assert len(upsample_8k_to_16k(tone)) == 2 * len(tone)
    assert len(downsample_16k_to_8k(sine_pcm(0.5, 16000))) == len(tone)
    assert len(resample_pcm16(sine_pcm(1.0, 22050), 22050, 16000)) == 16000 * 2
    assert resample_pcm16(b"", 8000, 16000) == b""


def test_resample_preserves_tone_energy_and_rejects_bad_rates() -> None:
    tone = sine_pcm(0.5, 16000, freq=300)
    down = downsample_16k_to_8k(tone)
    assert pcm.rms(down) == pytest.approx(pcm.rms(tone), rel=0.1)
    with pytest.raises(ValueError):
        resample_pcm16(tone, 0, 8000)


def test_pcm_rms_matches_pure_python_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    tone = sine_pcm(0.1)
    with_numpy = pcm.rms(tone)
    monkeypatch.setattr(pcm, "_numpy", lambda: None)
    assert pcm.rms(tone) == pytest.approx(with_numpy, rel=1e-6)
    assert pcm.rms(b"") == 0.0


# ------------------------------------------------------------------ VAD / endpointer


def test_energy_vad_separates_tone_from_silence() -> None:
    vad = EnergyVad()
    frame = vad.frame_samples
    assert vad.is_speech(sine_pcm(0.02)[: frame * 2]) is True
    assert vad.is_speech(silence_pcm(0.02)) is False


def _endpoints(audio: bytes, chunk_ms: int = 20, **kw: int) -> list[EndpointEvent]:
    ep = VadEndpointer(EnergyVad(), 16000, **kw)
    events: list[EndpointEvent] = []
    for piece in chunked(audio, 16000, chunk_ms):
        events += ep.feed(piece)
    return events


def _kinds(events: list[EndpointEvent]) -> list[tuple[str, float, int]]:
    return [(e.kind, e.at_s, e.pause_ms) for e in events]


def test_short_utterance_emits_pause_then_bare_greeting_silence() -> None:
    audio = silence_pcm(0.5) + sine_pcm(1.0) + silence_pcm(2.0)
    assert _kinds(_endpoints(audio)) == [
        ("speech_start", 0.5, 0),
        ("pause", 2.3, 800),
        ("bare_greeting_silence", 3.0, 1500),
    ]


def test_long_utterance_emits_pause_only() -> None:
    audio = silence_pcm(0.2) + sine_pcm(3.0) + silence_pcm(2.0)
    kinds = [e.kind for e in _endpoints(audio)]
    assert kinds == ["speech_start", "pause"]


def test_gap_shorter_than_pause_keeps_one_utterance() -> None:
    audio = sine_pcm(0.6) + silence_pcm(0.5) + sine_pcm(0.6) + silence_pcm(1.0)
    kinds = [e.kind for e in _endpoints(audio)]
    assert kinds == ["speech_start", "pause"]


def test_speech_resuming_cancels_bare_greeting_event() -> None:
    audio = sine_pcm(0.5) + silence_pcm(1.0) + sine_pcm(0.5) + silence_pcm(2.0)
    kinds = [e.kind for e in _endpoints(audio)]
    assert kinds == ["speech_start", "pause", "speech_start", "pause", "bare_greeting_silence"]


def test_blip_shorter_than_min_speech_is_ignored() -> None:
    assert _endpoints(silence_pcm(0.2) + sine_pcm(0.02) + silence_pcm(2.0)) == []


def test_pause_threshold_is_configurable_and_bare_threshold_too() -> None:
    audio = sine_pcm(0.5) + silence_pcm(2.0)
    events = _endpoints(audio, pause_ms=400, bare_greeting_ms=600)
    assert _kinds(events)[1:] == [("pause", 0.9, 400), ("bare_greeting_silence", 1.1, 600)]


def test_events_do_not_depend_on_chunk_size_or_wall_clock() -> None:
    audio = silence_pcm(0.3) + sine_pcm(0.9) + silence_pcm(2.0)
    reference = _endpoints(audio, chunk_ms=20)
    assert _endpoints(audio, chunk_ms=7) == reference
    assert _endpoints(audio, chunk_ms=500) == reference


def test_reset_restarts_the_audio_clock() -> None:
    ep = VadEndpointer(EnergyVad(), 16000)
    audio = sine_pcm(0.4) + silence_pcm(1.0)
    first = ep.feed(audio)
    ep.reset()
    assert ep.feed(audio) == first


def test_odd_byte_chunks_are_buffered() -> None:
    ep = VadEndpointer(EnergyVad(), 16000)
    audio = sine_pcm(0.4) + silence_pcm(1.0)
    events: list[EndpointEvent] = []
    for i in range(0, len(audio), 3):
        events += ep.feed(audio[i : i + 3])
    assert [e.kind for e in events] == ["speech_start", "pause"]
