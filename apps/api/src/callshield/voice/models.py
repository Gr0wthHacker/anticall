"""Recommended local models as DATA. Nothing here downloads anything.

Sizes are approximate. Users fetch files themselves (or via the explicit CLI command) and
point the settings at the folder. All listed models run fully offline once downloaded.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    kind: str  # "stt" | "tts" | "vad"
    engine: str
    name: str
    approx_size_mb: int
    url: str
    instructions: str
    note: str = ""


_WHISPER = (
    "Download the repository files into <stt_model_dir>/{name} (git lfs clone or huggingface-cli)."
)


def recommended_models() -> tuple[ModelInfo, ...]:
    hf = "https://huggingface.co/Systran/faster-whisper-"
    return (
        ModelInfo(
            "stt",
            "faster-whisper",
            "tiny.en",
            75,
            hf + "tiny.en",
            _WHISPER.format(name="tiny.en"),
            "fastest, weakest accuracy",
        ),
        ModelInfo(
            "stt", "faster-whisper", "base.en", 145, hf + "base.en", _WHISPER.format(name="base.en")
        ),
        ModelInfo(
            "stt",
            "faster-whisper",
            "small.en",
            480,
            hf + "small.en",
            _WHISPER.format(name="small.en"),
            "recommended default for phone audio",
        ),
        ModelInfo(
            "stt",
            "faster-whisper",
            "medium.en",
            1500,
            hf + "medium.en",
            _WHISPER.format(name="medium.en"),
            "needs a strong CPU or a GPU",
        ),
        ModelInfo(
            "stt",
            "vosk",
            "vosk-model-small-en-us-0.15",
            40,
            "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
            "Unzip into <stt_model_dir>; folder name must equal the model name.",
            "true streaming, lowest CPU use",
        ),
        ModelInfo(
            "stt",
            "vosk",
            "vosk-model-en-us-0.22-lgraph",
            130,
            "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22-lgraph.zip",
            "Unzip into <stt_model_dir>; folder name must equal the model name.",
        ),
        ModelInfo(
            "tts",
            "piper",
            "en_US-lessac-medium",
            65,
            "https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/lessac/medium",
            "Download en_US-lessac-medium.onnx and .onnx.json into <tts_model_dir>.",
            "clear US voice",
        ),
        ModelInfo(
            "tts",
            "piper",
            "en_US-amy-low",
            60,
            "https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/amy/low",
            "Download the .onnx and .onnx.json into <tts_model_dir>.",
            "faster, lower quality",
        ),
        ModelInfo(
            "vad",
            "silero",
            "silero_vad.onnx",
            2,
            "https://github.com/snakers4/silero-vad/tree/master/src/silero_vad/data",
            "Download silero_vad.onnx and set CALLSHIELD_VOICE_VAD_MODEL_PATH.",
            "optional; the built-in energy VAD needs no model",
        ),
    )


def models_for(kind: str, engine: str | None = None) -> tuple[ModelInfo, ...]:
    return tuple(
        m for m in recommended_models() if m.kind == kind and (engine is None or m.engine == engine)
    )
