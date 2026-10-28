# Asterisk integration (provider neutral)

Configuration templates for the CallShield telephony runner. Works with any SIP trunk: put
the trunk values in `.env`; nothing in these files is provider specific.

See **docs/deployment/asterisk.md** for the architecture, ports, firewall rules and
troubleshooting. **None of this has been run against a real Asterisk or a real trunk.**

| File | Purpose |
|---|---|
| `pjsip.conf` | Generic trunk (registration or IP auth), optional user endpoint |
| `extensions.conf` | Inbound -> Stasis app; hand-off targets `ring-user` and `voicemail`; fail-open fallback |
| `ari.conf` | ARI user for CallShield (password from env) |
| `http.conf` | ARI HTTP listener (bind to localhost / internal network only) |
| `rtp.conf` | RTP range; match the compose publish range |
| `modules.conf` | Module notes and exclusions |
| `r0-sample/` | The earlier R0 synthetic harness (mock IVR), kept for reference |

Placeholders `${NAME}` are rendered from the container environment by
`infra/docker/asterisk-entrypoint.sh` (only the variables it lists, so Asterisk's own
`${EXTEN}` survives).

```bash
docker compose -f infra/docker/docker-compose.yml --profile asterisk up --build
```

## Environment variables

CallShield reads `CALLSHIELD_ASTERISK_*` (`ARI_URL`, `ARI_USERNAME`, `ARI_PASSWORD`, `APP_NAME`, `TRUNK_ENDPOINT`, ...) and `CALLSHIELD_VOICE_*`. The older `CALLSHIELD_ARI_URL/USERNAME/PASSWORD/APP` names are accepted only as fallbacks. Full list: [`docs/deployment/configuration-reference.md`](../../docs/deployment/configuration-reference.md).
