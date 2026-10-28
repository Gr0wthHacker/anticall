"""Sample-rate conversion and G.711 (mu-law / A-law) codecs in pure Python.

Resampling is linear interpolation with a box low-pass when downsampling: good enough for
8 kHz telephony and speech recognition, not for music. G.711 follows the ITU reference
algorithm (bias 0x84, clip 32635 for mu-law).
"""

from __future__ import annotations

from functools import lru_cache

from callshield.voice.pcm import clamp16, from_samples, to_samples

ULAW_BIAS = 0x84
ULAW_CLIP = 32635


def resample_pcm16(pcm16: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Convert PCM16 mono between sample rates (stateless per call)."""
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("sample rates must be positive")
    samples = list(to_samples(pcm16))
    if src_rate == dst_rate or not samples:
        return from_samples(samples)
    if src_rate > dst_rate:
        samples = _box_lowpass(samples, max(1, round(src_rate / dst_rate)))
    count = round(len(samples) * dst_rate / src_rate)
    last = len(samples) - 1
    out: list[int] = []
    for i in range(count):
        pos = i * src_rate / dst_rate
        lo = min(int(pos), last)
        hi = min(lo + 1, last)
        frac = pos - lo
        out.append(clamp16(round(samples[lo] * (1 - frac) + samples[hi] * frac)))
    return from_samples(out)


def _box_lowpass(samples: list[int], width: int) -> list[int]:
    if width <= 1:
        return samples
    prefix = [0]
    for s in samples:
        prefix.append(prefix[-1] + s)
    half, n = width // 2, len(samples)
    out: list[int] = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i - half + width)
        out.append((prefix[hi] - prefix[lo]) // max(1, hi - lo))
    return out


def upsample_8k_to_16k(pcm16: bytes) -> bytes:
    return resample_pcm16(pcm16, 8000, 16000)


def downsample_16k_to_8k(pcm16: bytes) -> bytes:
    return resample_pcm16(pcm16, 16000, 8000)


# ---------------------------------------------------------------- G.711 mu-law


def _ulaw_decode_byte(byte: int) -> int:
    u = ~byte & 0xFF
    t = (((u & 0x0F) << 3) + ULAW_BIAS) << ((u & 0x70) >> 4)
    return (ULAW_BIAS - t) if u & 0x80 else (t - ULAW_BIAS)


def _ulaw_encode_sample(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    magnitude = min(abs(sample), ULAW_CLIP) + ULAW_BIAS
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not magnitude & mask:
        exponent -= 1
        mask >>= 1
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


@lru_cache(maxsize=1)
def _ulaw_decode_table() -> tuple[int, ...]:
    return tuple(_ulaw_decode_byte(b) for b in range(256))


@lru_cache(maxsize=1)
def _ulaw_encode_table() -> bytes:
    return bytes(_ulaw_encode_sample(s) for s in range(-32768, 32768))


def ulaw_decode(data: bytes) -> bytes:
    """G.711 mu-law bytes to PCM16."""
    table = _ulaw_decode_table()
    return from_samples([table[b] for b in data])


def ulaw_encode(pcm16: bytes) -> bytes:
    """PCM16 to G.711 mu-law bytes."""
    table = _ulaw_encode_table()
    return bytes(table[s + 32768] for s in to_samples(pcm16))


# ---------------------------------------------------------------- G.711 A-law


def _alaw_decode_byte(byte: int) -> int:
    a = byte ^ 0x55
    segment = (a & 0x70) >> 4
    t = ((a & 0x0F) << 4) + 8
    if segment:
        t = (t + 0x100) << (segment - 1)
    return t if a & 0x80 else -t


def _alaw_encode_sample(sample: int) -> int:
    sign = 0x80 if sample >= 0 else 0
    magnitude = min(-sample - 1 if sample < 0 else sample, 32767)
    if magnitude < 256:
        code = magnitude >> 4
    else:
        segment = magnitude.bit_length() - 8
        code = (segment << 4) | ((magnitude >> (segment + 3)) & 0x0F)
    return (code | sign) ^ 0x55


@lru_cache(maxsize=1)
def _alaw_decode_table() -> tuple[int, ...]:
    return tuple(_alaw_decode_byte(b) for b in range(256))


@lru_cache(maxsize=1)
def _alaw_encode_table() -> bytes:
    return bytes(_alaw_encode_sample(s) for s in range(-32768, 32768))


def alaw_decode(data: bytes) -> bytes:
    """G.711 A-law bytes to PCM16."""
    table = _alaw_decode_table()
    return from_samples([table[b] for b in data])


def alaw_encode(pcm16: bytes) -> bytes:
    """PCM16 to G.711 A-law bytes."""
    table = _alaw_encode_table()
    return bytes(table[s + 32768] for s in to_samples(pcm16))
