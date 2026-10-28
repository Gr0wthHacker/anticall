"""Voice activity detection: dependency-free energy VAD and an optional Silero ONNX VAD.

A VAD judges one fixed-size frame at a time. It only detects "is there speech"; it never
extracts anything about who is speaking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from callshield.voice.errors import VoiceEngineUnavailable, import_optional
from callshield.voice.pcm import BYTES_PER_SAMPLE, rms

SILERO_FRAME_SAMPLES = {16000: 512, 8000: 256}
SILERO_CONTEXT_SAMPLES = {16000: 64, 8000: 32}


class Vad(Protocol):
    frame_samples: int

    def is_speech(self, frame: bytes) -> bool: ...

    def reset(self) -> None: ...


class EnergyVad:
    """RMS threshold with a short hangover so brief dips inside words do not read as silence."""

    def __init__(
        self,
        sample_rate: int = 16000,
        *,
        frame_ms: int = 20,
        threshold_rms: float = 400.0,
        hangover_frames: int = 0,
    ) -> None:
        self.frame_samples = sample_rate * frame_ms // 1000
        self._threshold = threshold_rms
        self._hangover = hangover_frames
        self._remaining = 0

    def is_speech(self, frame: bytes) -> bool:
        if rms(frame) >= self._threshold:
            self._remaining = self._hangover
            return True
        if self._remaining > 0:
            self._remaining -= 1
            return True
        return False

    def reset(self) -> None:
        self._remaining = 0


class SileroVad:
    """Silero VAD v5 via onnxruntime. The model file is supplied by the operator (no download).

    Frames are 512 samples at 16 kHz or 256 at 8 kHz. Unverified against a real model in CI.
    """

    def __init__(self, model_path: str, sample_rate: int = 16000, threshold: float = 0.5) -> None:
        if sample_rate not in SILERO_FRAME_SAMPLES:
            raise VoiceEngineUnavailable("silero VAD supports only 8000 or 16000 Hz audio")
        path = Path(model_path)
        if not path.is_file():
            raise VoiceEngineUnavailable(
                f"silero VAD model not found at {path}. Download silero_vad.onnx "
                "(see `callshield.voice.models`) and set CALLSHIELD_VOICE_VAD_MODEL_PATH."
            )
        self._np: Any = import_optional("numpy", purpose="silero VAD")
        ort = import_optional("onnxruntime", purpose="silero VAD")
        self._session: Any = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self.frame_samples = SILERO_FRAME_SAMPLES[sample_rate]
        self._rate = sample_rate
        self._threshold = threshold
        self.reset()

    def reset(self) -> None:
        np = self._np
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, SILERO_CONTEXT_SAMPLES[self._rate]), dtype=np.float32)

    def is_speech(self, frame: bytes) -> bool:
        np = self._np
        usable = frame[: len(frame) - len(frame) % BYTES_PER_SAMPLE]
        data = np.frombuffer(usable, dtype="<i2").astype(np.float32) / 32768.0
        window = np.concatenate([self._context, data.reshape(1, -1)], axis=1)
        out, self._state = self._session.run(
            None,
            {"input": window, "state": self._state, "sr": np.array(self._rate, dtype=np.int64)},
        )
        self._context = window[:, -SILERO_CONTEXT_SAMPLES[self._rate] :]
        return bool(float(out[0][0]) >= self._threshold)
