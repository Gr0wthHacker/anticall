"""Small PCM16 helpers (pure Python; numpy used only as an optional fast path)."""

from __future__ import annotations

import importlib
import math
import sys
from array import array
from typing import Any

BYTES_PER_SAMPLE = 2


def _numpy() -> Any | None:
    try:
        return importlib.import_module("numpy")
    except ImportError:
        return None


def to_samples(pcm16: bytes) -> array[int]:
    """Decode little-endian PCM16 (a trailing odd byte is ignored)."""
    samples: array[int] = array("h")
    samples.frombytes(pcm16[: len(pcm16) - len(pcm16) % BYTES_PER_SAMPLE])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def from_samples(samples: array[int] | list[int]) -> bytes:
    out: array[int] = array("h", samples)
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


def clamp16(value: int) -> int:
    return max(-32768, min(32767, value))


def rms(pcm16: bytes) -> float:
    """Root-mean-square amplitude on the int16 scale."""
    np = _numpy()
    if np is not None:
        data = np.frombuffer(pcm16[: len(pcm16) - len(pcm16) % 2], dtype="<i2")
        if data.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(data.astype(np.float64)))))
    samples = to_samples(pcm16)
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def duration_s(pcm16: bytes, sample_rate: int) -> float:
    return len(pcm16) // BYTES_PER_SAMPLE / sample_rate
