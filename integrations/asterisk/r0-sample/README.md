# Asterisk R0 Test Harness

Sample configuration for the R0 milestone (PRD 15): Asterisk SIP, simulated provider, mock IVR with "press 2", user-owned destination, ARI control, minimal agent, clean termination, safe fallback.

> **Synthetic only.** All numbers are fictional (555-01xx). Credentials are `CHANGE_ME` placeholders. The dialplan contains no external dialing. Do not point a real SIP trunk at this sample, and do not expose SIP (5060) or ARI (8088) to the internet.

## Files

| File | Purpose |
|---|---|
| `pjsip.conf` | UDP transport, `provider-sim` endpoint, user softphone `5550102` |
| `ari.conf` | ARI user `callshield` |
| `http.conf` | ARI HTTP listener (8088) |
| `extensions.conf` | Mock IVR, mock agent, inbound test, harness-only internal context |

## Numbers

| Number | Role |
|---|---|
| 5550100 | Mock IVR entry for "Example Lending" (fictional) |
| 5550101 | Sales stub (option 1) |
| 5550102 | User-owned test softphone / protected line endpoint |
| 5550103 | Mock agent stub |

## Mock IVR behavior

Dial `5550100` from the softphone or the provider simulator:

1. Menu: "Press 1 for sales. Press 2 to speak with an agent."
2. `1` plays a sales stub and hangs up.
3. `2` goes to the mock agent: scripted greeting, wait for the request, then a scripted "removal request received" acknowledgment, then hangup.
4. Invalid input retries once, then hangs up. Timeout hangs up.

The acknowledgment is **claimed** acknowledgment only. Tests must keep the case `UNRESOLVED` / `REQUEST_ACKNOWLEDGED`, never "suppressed" (PRD 5.2.1).

## Inbound test

Calls to `5550102` enter Stasis app `callshield` over ARI. If the ARI app is down (`STASISSTATUS=FAILED`), the dialplan falls back to ringing the softphone. That demonstrates the safe-fallback requirement.

## Prompt audio

Generate synthetic audio yourself (any local TTS) as 8 kHz mono WAV under `/var/lib/asterisk/sounds/en/callshield/`: `ivr-menu`, `agent-greeting`, `removal-ack`, `sales-stub`. Missing files only produce warnings. Do not record real people or real businesses.

## Run

```bash
docker compose -f infra/docker/docker-compose.yml --profile asterisk up asterisk
```

Ports are published on 127.0.0.1 only. The image tag in compose is not digest-pinned; pin before anything beyond local use.

## Not verified

These configs were written without a running Asterisk and have not been tested. Expect to adjust syntax for your Asterisk version (config targeted at Asterisk 20-ish PJSIP).

## Checklist (R0)

- [ ] Softphone registers as 5550102
- [ ] Dial 5550100, press 2, hear agent greeting and acknowledgment
- [ ] Inbound to 5550102 reaches ARI app; stopping the app triggers fallback ring
- [ ] Duplicate events from ARI do not duplicate ledger entries
