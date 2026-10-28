# Local voice engines (STT, TTS, endpointing)

Callbacks use speech engines that run on your own host. Nothing here calls a network service, downloads a model at runtime, or stores audio or transcripts. Everything is off by default.

## Install

```
pip install 'callshield[voice]'
```

Pulls numpy, onnxruntime, faster-whisper, vosk and piper-tts. Install only what you use if you prefer (for example `pip install faster-whisper piper-tts`). The built-in energy VAD and the G.711 / resampling code need no extra packages.

## Configure (environment, prefix `CALLSHIELD_VOICE_`)

| Variable | Default | Meaning |
|---|---|---|
| `STT_ENGINE` | `none` | `faster-whisper`, `vosk`, or `none` |
| `STT_MODEL` | `small.en` (whisper), `vosk-model-small-en-us-0.15` (vosk) | model name; for vosk, the folder name |
| `STT_MODEL_DIR` | unset | folder holding downloaded models (required for vosk) |
| `STT_DEVICE` | `cpu` | `cpu` or `cuda` |
| `STT_COMPUTE_TYPE` | `int8` (cpu) / `float16` (cuda) | CTranslate2 compute type |
| `TTS_ENGINE` | `none` | `piper` or `none` |
| `TTS_VOICE` | `en_US-lessac-medium` | piper voice name |
| `TTS_MODEL_DIR` / `TTS_MODEL_PATH` | unset | folder with `<voice>.onnx` + `.onnx.json`, or explicit `.onnx` path |
| `VAD` | `energy` | `energy` or `silero` (needs `VAD_MODEL_PATH`) |
| `SAMPLE_RATE` | `16000` | `8000` or `16000` |

Misconfiguration fails at startup with a message saying what to install or set.

## Models

Download once, then run offline. `callshield.voice.models.recommended_models()` lists names, approximate sizes and official URLs (data only; nothing is fetched).

- faster-whisper: `https://huggingface.co/Systran/faster-whisper-small.en` (also tiny.en, base.en, medium.en). Place the files in `<STT_MODEL_DIR>/small.en/`. Models are loaded with `local_files_only=True`.
- vosk: `https://alphacephei.com/vosk/models` (for example `vosk-model-small-en-us-0.15`, about 40 MB). Unzip into `STT_MODEL_DIR`.
- piper: `https://huggingface.co/rhasspy/piper-voices` (for example `en_US-lessac-medium.onnx` and `.onnx.json`).
- silero VAD (optional): `silero_vad.onnx` from `https://github.com/snakers4/silero-vad`.

## Which engine

| Engine | Streaming | Latency | Notes |
|---|---|---|---|
| vosk | true partial/final | low | lowest CPU, lower accuracy on noisy phone audio |
| faster-whisper | approximation: VAD-segmented, partial about every 1 s, final on a 500 ms pause | final arrives after the pause plus decode time | best accuracy |

## Hardware sizing (rough estimates, not benchmarks; measure on your host)

| Setup | Approx. memory | Expect |
|---|---|---|
| vosk small | 300 to 500 MB | well under real time on 1 modern core per call |
| faster-whisper tiny.en/base.en int8, CPU | 0.5 to 1 GB | decode in a fraction of the utterance length on 2+ cores |
| faster-whisper small.en int8, CPU | 1 to 2 GB | about real time on 2 to 4 cores; budget 1 s extra for finals |
| faster-whisper medium.en | 3 to 5 GB | GPU recommended |
| piper medium voice | 150 to 300 MB | a short sentence synthesizes in well under a second on CPU |

Concurrency scales roughly linearly with cores; callbacks are capped per user and per instance by the callback spec, so one small host is typically enough.

## How it plugs into callbacks

- `voice.callback_bridge.CallbackSpeechBridge` turns final STT results plus endpointer pauses into the `MediaEvent`s the callback session already consumes, with the same timing semantics as `callback.mock_scenarios`. The session applies the 800 ms business-pause rule, the 1.5 s bare-greeting rule and the listen window ceiling; the endpointer's `pause` and `bare_greeting_silence` events are available for a live driver.
- `G.711` (mu-law / A-law) and 8k/16k conversion live in `voice.resample` for RTP.

## Privacy

- Every built engine must declare `local_only=True`; the registry refuses anything else (and any engine exposing a non-loopback `endpoint`).
- No audio or transcript is logged or written; logs carry counts and durations only. The listen-window rules in `docs/prd/CHANGE_callback_flow.md` are enforced by the session, not here.
- No voice biometrics: no speaker embeddings, voice matching, or speaker models. A repo test audits dependencies and source for this.
- No network access at import or inference time. Model download is always an explicit, separate step.
- Offline operation: after models are on disk, the host needs no internet for speech.

## Verification status

Engine wrappers are tested with fake third-party modules only. No real model has been run in CI; validate each engine on your host with a recorded test call before enabling callbacks.
