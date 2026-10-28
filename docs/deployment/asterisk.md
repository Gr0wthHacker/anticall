# Asterisk integration (any SIP trunk)

> **Not verified against a real Asterisk or a real trunk.** Everything here was written and unit-tested against fakes (an in-process fake ARI, fake audio engines). Expect to adjust configs for your Asterisk version (targeted at Asterisk 20 with PJSIP). Test with a number you own before relying on it. Not legal advice.

## How it fits together

```
 SIP trunk (any provider)
      |  SIP + RTP
      v
 +-----------+  ARI REST + events WebSocket (Basic auth, localhost/internal only)
 | Asterisk  |<--------------------------------------------+
 | PJSIP     |                                             |
 +-----------+  RTP (externalMedia, one UDP port per call) |
      |                                       +------------+-------------+
      +-------------------------------------->| telephony runner         |
                                              | python -m                |
   dialplan: Stasis(callshield,inbound,${EXTEN})   callshield.telephony.asterisk |
   ring-user / voicemail (continue targets)   |  ARI events -> Defend    |
                                              |  ingest -> instructions  |
                                              |  -> call control + media |
                                              +------+----------+--------+
                                                     |          |
                                              Postgres/SQLite  local STT/TTS
```

* **Inbound.** The dialplan sends a call on a protected line into the Stasis app `callshield`. The runner maps the ARI event to a normalized `ProviderCallEvent` and runs it through the same pipeline webhooks use (idempotency, ledger, incident, job, routing policy, screening). It then **executes** the returned instruction: ring the user, voicemail, hang up, or answer-and-screen. It decides nothing itself.
* **Tenancy.** ARI is authenticated by the local WebSocket login, not by an HMAC. The tenant is resolved **only** from the protected line that matches the dialed number (and the endpoint, when the line has one). Payload fields cannot name a tenant. Later events (answer, end) resolve through the call row stored at the start.
* **Never drop a call.** Any ARI, database, pipeline or speech-engine failure ends in `continue` to the dialplan's `ring-user`. If the runner is down, `STASISSTATUS=FAILED` takes the same path.
* **Callbacks.** The worker process holds an `AsteriskDialer` (selected with `CALLSHIELD_DIALER=asterisk`). It originates through the PJSIP trunk with the caller ID taken **only** from the `VerifiedCaller`; there is no caller-ID parameter, setting or env var. A hard timer (`max_call_seconds`, capped at 120) hangs the call up. It uses a separate Stasis app (`callshield-out`) so inbound and outbound events never mix.
* **Audio.** Caller audio is resampled to 16 kHz PCM16 and given to a local STT stream **only while Defend's processing-consent decision allows live STT for that line** (re-checked every few seconds; revocation stops audio immediately). Callbacks additionally require every engine to be local (`local_only` and a loopback endpoint). Audio is kept in RAM only. A recording exists only after `start_recording()`, which refuses to run before the disclosure has been spoken, and the file's sha256 is recorded.

## Enabling it (all off by default)

| Setting | Where | Purpose |
|---|---|---|
| `CALLSHIELD_ASTERISK_ENABLED=true` | `.env` | The runner refuses to start without it |
| `CALLSHIELD_ASTERISK_ARI_URL` | `.env` | `http://127.0.0.1:8088` (no credentials in the URL) |
| `CALLSHIELD_ASTERISK_ARI_USERNAME`, `..._ARI_PASSWORD` | `.env` | Must match `ari.conf`. The older `CALLSHIELD_ARI_URL/USERNAME/PASSWORD/APP` names written by `callshield init` are accepted as fallbacks (a trailing `/ari` is stripped) |
| `CALLSHIELD_ASTERISK_TRUNK_ENDPOINT` | `.env` | PJSIP endpoint name of the trunk (`trunk`) |
| `CALLSHIELD_ASTERISK_DIAL_FORMAT` | `.env` | Outbound dial string, default `PJSIP/{number}@{trunk}`; `..._DIAL_STRIP_PLUS=true` if your provider wants digits only |
| `CALLSHIELD_ASTERISK_USER_DIAL_FORMAT` | `.env` | How "ring the user" dials (default same shape; use `PJSIP/user` style for a softphone) |
| `CALLSHIELD_ASTERISK_CODEC` | `.env` | `ulaw` (8 kHz, default) or `slin16` |
| `CALLSHIELD_ASTERISK_EXTERNAL_MEDIA_HOST/BIND/PORT/PORT_COUNT` | `.env` | Where Asterisk sends RTP for the speech engines |
| `CALLSHIELD_ASTERISK_RECORDING_DIR` | `.env` | Callback recordings (post-disclosure only); unset = nothing written |
| `CALLSHIELD_DIALER=asterisk` + `CALLSHIELD_OUTBOUND_ENABLED=true` | worker/API `.env` | Selects the Asterisk dialer for callbacks. Otherwise the disabled dialer is used |
| `TRUNK_HOST`, `TRUNK_PORT`, `TRUNK_USERNAME`, `TRUNK_PASSWORD`, `TRUNK_TRANSPORT`, `TRUNK_CODECS`, `USER_ENDPOINT_PASSWORD`, `EXTERNAL_IP` | `.env` | Rendered into `pjsip.conf` by the Asterisk container entrypoint |

Voice engines come from `CALLSHIELD_VOICE_*` (see [`voice.md`](voice.md)). With no STT engine configured, unknown callers fall back to ring/voicemail and callbacks cannot hear anyone.

```bash
docker compose -f infra/docker/docker-compose.yml --profile asterisk up --build
# or without Docker, on a host that already runs Asterisk:
pip install 'callshield[asterisk]'
CALLSHIELD_ASTERISK_ENABLED=true python -m callshield.telephony.asterisk
```

The runner serves `GET http://127.0.0.1:8089/healthz` (200 `OK`, 503 `DEGRADED`) and writes the ARI link state into the telephony health rows read by `GET /v1/health/telephony`. A SIP registration failure recorded by the ingest path is never overwritten by "the ARI link is up". SIGTERM stops it cleanly.

## Ports and firewall

| Port | Proto | Who | Exposure |
|---|---|---|---|
| 8088 | TCP | ARI (HTTP + WebSocket) | **localhost / internal network only.** Never public. Plain HTTP carrying a password and live call control. Use a VPN or a TLS reverse proxy for anything beyond the host |
| 8089 | TCP | runner health | loopback only |
| 5060 (5061 TLS) | UDP/TCP | SIP | Allow **only your provider's addresses** |
| 10000-10100 | UDP | Asterisk RTP (`rtp.conf`) | Allow your provider's media addresses; keep equal to the compose publish range |
| 4000-4199 | UDP | externalMedia (runner side, `EXTERNAL_MEDIA_PORT`, 2 ports per concurrent call) | Internal only: Asterisk to runner. Never open to the internet |

Compose publishes everything on `127.0.0.1`. That is enough for the synthetic harness, not for a real trunk (the provider must reach SIP and RTP). Moving SIP/RTP to a public interface is a deliberate step: firewall by source address first. Behind NAT set `EXTERNAL_IP` and the `external_*_address` lines in `pjsip.conf`, or you will get one-way audio.

The Asterisk container reads the project `.env` (for its secrets), so it also receives unrelated variables. Use a dedicated env file for it if that matters to you.

## STIR/SHAKEN

CallShield trusts an attestation level **only when Asterisk's own verification reports success** (`verstat`/`STIR_SHAKEN(0,verify_result)`). An `Identity` header alone is never enough, because anyone can forge one. Without verification the level is `NONE`, which keeps callbacks blocked (the callback gate requires `A`). To get real values: install the certificate and trust-store setup for `res_stir_shaken` (a `stir_shaken.conf` with a CA list), keep `stir_shaken=verify` on the trunk, and confirm your provider forwards the `Identity` header. How each provider presents this varies and is unverified here.

## Troubleshooting

| Symptom | Check |
|---|---|
| Runner exits with code 2 | `CALLSHIELD_ASTERISK_ENABLED` is not `true` |
| Health `DEGRADED`, detail `ari_auth_failed` | ARI password differs between `.env` and `ari.conf`; the container re-renders `ari.conf` at start |
| `ari_unreachable` | Wrong URL/port, Asterisk down, `http.conf` bind address, container network name |
| Every inbound call rings the user directly | The Stasis app is not connected (`STASISSTATUS=FAILED`), or no protected line matches the dialed number. Check the DID format in `${EXTEN}` against the line's E.164; the log says `NO_UNIQUE_LINE` |
| `LINE_MISMATCH` in the log | The line's endpoint id (set at enrolment) differs from the trunk endpoint name that delivered the call |
| `LINE_NOT_VERIFIED` | Finish line verification first |
| Screening never starts | No consent (`NO_CONSENT`), `live_stt_enabled` is off, no STT/TTS engine, or screening not enabled on the line. All of these deliberately fall back to ring/voicemail |
| One-way or no audio | NAT settings, RTP range not open, `direct_media=no`, runner not reachable from Asterisk on `EXTERNAL_MEDIA_HOST:PORT`, wrong `CODEC` |
| Callback never dials | Needs `CALLSHIELD_DIALER=asterisk`, `OUTBOUND_ENABLED`, `ASTERISK_ENABLED`, a verified line with outbound A-attestation, and local engines |
| Callback ends `STREAM_LOST` | ARI event stream dropped during the call, or the speech engine failed mid-call (the call is hung up, never left open) |
| Attestation always `NONE` | STIR/SHAKEN verification is not configured in Asterisk (see above) |

Logs carry masked numbers and exception type names only; passwords, speech text and raw numbers are never logged. Run at `INFO` normally; there is no debug mode that logs speech.

## Known gaps

* Written without a running Asterisk: ARI parameter names, `externalMedia` behavior, the `slin16` byte order, how outbound originate events reach the app, and the dialplan are all unverified.
* RTP is plain UDP between Asterisk and the runner on an internal network; there is no SRTP on that leg.
* The speech-engine call is made per 100 ms chunk on a worker thread; heavy models on slow CPUs will lag and the bridge drops chunks when its queue is full.
* Voicemail/IVR classification for callbacks stays in the callback session (from transcribed text), as designed.
