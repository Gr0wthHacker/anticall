"""PCM helpers: G.711 u-law, byte order, resampling, energy. Stdlib only (numpy is optional).

`audioop` is not used: it is removed in Python 3.13.
"""

from __future__ import annotations

import array
import sys
from typing import Any

try:  # optional accelerator; every function has a pure-python path
    import numpy as _np

    np: Any = _np
except ImportError:  # pragma: no cover - exercised only without numpy
    np = None

STT_RATE = 16000
_BIAS = 0x84
_CLIP = 32635


def _ulaw_to_linear(byte: int) -> int:
    byte = ~byte & 0xFF
    sign = byte & 0x80
    exponent = (byte >> 4) & 0x07
    mantissa = byte & 0x0F
    sample = ((mantissa << 3) + _BIAS) << exponent
    sample -= _BIAS
    return -sample if sign else sample


_DECODE = array.array("h", (_ulaw_to_linear(b) for b in range(256)))


def _linear_to_ulaw(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    sample = min(abs(sample), _CLIP) + _BIAS
    exponent = max(sample.bit_length() - 8, 0)
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def _to_le(samples: array.array[int]) -> bytes:
    if sys.byteorder == "big":  # pragma: no cover
        samples.byteswap()
    return samples.tobytes()


def _from_le(pcm16: bytes) -> array.array[int]:
    samples = array.array("h")
    samples.frombytes(pcm16[: len(pcm16) - len(pcm16) % 2])
    if sys.byteorder == "big":  # pragma: no cover
        samples.byteswap()
    return samples


def ulaw_decode(payload: bytes) -> bytes:
    """u-law bytes -> PCM16 little-endian (same sample rate)."""
    return _to_le(array.array("h", (_DECODE[b] for b in payload)))


def ulaw_encode(pcm16: bytes) -> bytes:
    return bytes(_linear_to_ulaw(s) for s in _from_le(pcm16))


def swap_endian(pcm16: bytes) -> bytes:
    """slin16 on RTP is network (big-endian) order; swap to/from little-endian."""
    samples = array.array("h")
    samples.frombytes(pcm16[: len(pcm16) - len(pcm16) % 2])
    samples.byteswap()
    return samples.tobytes()


def resample_pcm16(pcm16: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Mono PCM16 LE resample. Linear interpolation; box-average when downsampling."""
    if src_rate == dst_rate or not pcm16:
        return pcm16
    samples = _from_le(pcm16)
    if np is not None:
        return _resample_np(samples, src_rate, dst_rate)
    return _to_le(_resample_py(samples, src_rate, dst_rate))


def _resample_np(samples: array.array[int], src: int, dst: int) -> bytes:
    data = np.frombuffer(samples, dtype=np.int16).astype(np.float64)
    if dst < src:  # crude anti-alias: moving average over the decimation ratio
        width = max(round(src / dst), 1)
        if width > 1:
            data = np.convolve(data, np.ones(width) / width, mode="same")
    count = max(int(len(data) * dst / src), 1)
    positions = np.arange(count) * (src / dst)
    out = np.interp(positions, np.arange(len(data)), data)
    return bytes(np.clip(out, -32768, 32767).astype(np.int16).tobytes())


def _resample_py(samples: array.array[int], src: int, dst: int) -> array.array[int]:
    n = len(samples)
    step = src / dst
    width = max(round(step), 1) if dst < src else 1
    out = array.array("h")
    pos = 0.0
    for _ in range(max(int(n * dst / src), 1)):
        i = int(pos)
        if width > 1:
            window = samples[i : i + width] or samples[-1:]
            value = sum(window) / len(window)
        else:
            nxt = samples[min(i + 1, n - 1)]
            frac = pos - i
            value = samples[min(i, n - 1)] * (1 - frac) + nxt * frac
        out.append(max(-32768, min(32767, int(value))))
        pos += step
    return out


def rms(pcm16: bytes) -> float:
    samples = _from_le(pcm16)
    if not samples:
        return 0.0
    return float((sum(s * s for s in samples) / len(samples)) ** 0.5)


def mix_pcm16(a: bytes, b: bytes) -> bytes:
    """Sample-wise sum with clipping; the shorter input is zero-padded."""
    xs, ys = _from_le(a), _from_le(b)
    length = max(len(xs), len(ys))
    out = array.array("h")
    for i in range(length):
        total = (xs[i] if i < len(xs) else 0) + (ys[i] if i < len(ys) else 0)
        out.append(max(-32768, min(32767, total)))
    return _to_le(out)
