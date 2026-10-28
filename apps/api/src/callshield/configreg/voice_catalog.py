"""Recommended local voice engines/models. Data only.

``sha256`` is empty for every shipped entry: no checksum has been pinned, so the CLI will
never download these automatically. Download manually, check the publisher's checksum, and
place the files under ``<model_dir>/<engine>/``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceModel:
    engine: str
    kind: str  # stt | tts
    model: str
    pip_extra: str
    url: str
    page: str
    sha256: str = ""
    note: str = "UNVERIFIED recommendation: check size, license and accuracy yourself."


CATALOG: tuple[VoiceModel, ...] = (
    VoiceModel(
        "vosk",
        "stt",
        "vosk-model-small-en-us-0.15",
        "voice",
        "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        "https://alphacephei.com/vosk/models",
    ),
    VoiceModel(
        "faster-whisper",
        "stt",
        "small.en (CTranslate2 format)",
        "voice",
        "",
        "https://huggingface.co/Systran/faster-whisper-small.en",
        note="UNVERIFIED. Download the repository files into the engine folder.",
    ),
    VoiceModel(
        "piper",
        "tts",
        "en_US-lessac-medium (.onnx + .onnx.json)",
        "voice",
        "",
        "https://huggingface.co/rhasspy/piper-voices",
    ),
)


def catalog_for(engine: str) -> VoiceModel | None:
    for entry in CATALOG:
        if entry.engine == engine:
            return entry
    return None
