# Self-Hosting

> **Pre-alpha. Synthetic/local mode only.** Real telephony, recording, live AI processing, outbound calling, and agency filing are not cleared (see [`docs/legal/release-gates.md`](../legal/release-gates.md)). Maintainers do not operate or supervise self-hosted instances. You are responsible for legal compliance, provider terms, and security. Not legal advice.

## Local synthetic mode

Nothing connects to a real carrier. Provider events are synthetic fixtures. Use fictional numbers (555-01xx).

```bash
callshield init            # writes .env with generated secrets (or: cp env.example .env)
# With the compose Postgres: set CALLSHIELD_DB_URL to the postgresql+psycopg URL shown in
# docs/deployment/configuration-reference.md and replace every CHANGE_ME. Leave all flags false.
docker compose -f infra/docker/docker-compose.yml --env-file .env up --build -d
docker compose -f infra/docker/docker-compose.yml ps
```

Services: `postgres` (16), `api`, `worker`, `dashboard` (once `apps/dashboard` exists). All ports bind to `127.0.0.1`. Optional profiles:

```bash
docker compose -f infra/docker/docker-compose.yml --profile redis up -d      # Redis (not needed; see ADR 0002)
docker compose -f infra/docker/docker-compose.yml --profile asterisk up -d   # R0 test harness
```

Development override (bind mounts, reload):

```bash
docker compose -f infra/docker/docker-compose.yml -f infra/docker/docker-compose.dev.yml up --build
```

Note: the compose files were authored without a Docker runtime available and have not been exercised end to end. Report issues.

## What is not supported yet

- Connecting a real SIP trunk to process real calls
- Recording or persistent transcription
- Outbound calls, including callbacks (see below; off by default, counsel gates G1-G5 apply)
- Programmatic complaint submission

Before enabling any of these you need the relevant gate passed, counsel review for your jurisdiction, and a provider that permits the use.

> `env.example` and [`configuration-reference.md`](configuration-reference.md) are generated from the settings classes (`apps/api/scripts/gen_config_reference.py`); a test fails if they go stale. Check an install with `callshield doctor`.

## Callback flow settings

The callback flow is off by default and needs real telephony plus counsel sign-off before use on real numbers ([`CHANGE_callback_flow.md`](../prd/CHANGE_callback_flow.md), ADR 0006). All keys use the `CALLSHIELD_CALLBACK_` prefix. Values outside the allowed range are rejected at startup.

| Variable | Default | Range |
|---|---|---|
| `CALLSHIELD_CALLBACK_CALLBACK_ENABLED` | `false` | true/false |
| `CALLSHIELD_CALLBACK_CALLBACK_WINDOW_HOURS` | `72` | 1-168 |
| `CALLSHIELD_CALLBACK_LISTEN_WINDOW_SECONDS` | `10` | 0-10 (0 = disclose on answer) |
| `CALLSHIELD_CALLBACK_BARE_GREETING_DISCLOSE_MS` | `1500` | 500-3000 |
| `CALLSHIELD_CALLBACK_MAX_ATTEMPTS_PER_INBOUND` | `2` | 1-2 |
| `CALLSHIELD_CALLBACK_MAX_ATTEMPTS_PER_NUMBER_30D` | `2` | 1-2 |
| `CALLSHIELD_CALLBACK_MAX_ATTEMPTS_PER_USER_DAY` | `5` | 1-5 |
| `CALLSHIELD_CALLBACK_MAX_QUESTION_TURNS` | `4` | 1-4 |
| `CALLSHIELD_CALLBACK_MAX_CALL_SECONDS` | `120` | 30-120 |
| `CALLSHIELD_CALLBACK_OUTBOUND_RECORDING` | `false` | true/false |
| `CALLSHIELD_CALLBACK_REMOTE_AI_PROCESSING` | `false` | locked to false |
| `CALLSHIELD_CALLBACK_ALLOWED_ATTESTATION` | `A` | locked to A |
| `CALLSHIELD_CALLBACK_DISCLOSURE_TEMPLATE_ID` | built-in | fixed template id |
| `CALLSHIELD_CALLBACK_HMAC_SECRET` | dev value | **set a random value in production** (`openssl rand -hex 32`) |
| `CALLSHIELD_CALLBACK_STORAGE_ROOT` | `./var/callback` | writable path |

## CORS

`CALLSHIELD_CORS_ALLOW_ORIGINS` is a JSON list of dashboard origins allowed to call the API from a browser (for example `["https://app.example.com"]`). Empty by default, meaning no CORS headers. Two supported setups:

1. Same origin (preferred): the reverse proxy serves the dashboard and `/v1`, or the dashboard proxies via `NEXT_PUBLIC_API_BASE_URL=/api-proxy`. No CORS needed.
2. Direct: the dashboard is built with `NEXT_PUBLIC_API_BASE_URL=https://api.example.com` and the API sets `CALLSHIELD_CORS_ALLOW_ORIGINS='["https://app.example.com"]'`. Never use `*`.

## Postgres and row-level security

Tenant isolation is enforced in the app and, on Postgres, by RLS keyed on `app.tenant_id`. RLS does not apply to the table owner or a superuser, so run the API as a separate non-owner role (migrations run as the owner). `scripts/verify_postgres_rls.py` checks this against a migrated database in the `api-postgres` CI job; it has not been run yet.

## TLS and reverse proxy

Do not expose the API or dashboard directly. Put a reverse proxy (Caddy, nginx, Traefik, or Cloudflare Tunnel) in front:

- Terminate TLS 1.2+ at the proxy; redirect HTTP to HTTPS; enable HSTS.
- Proxy only the dashboard and `/v1` API. Never expose Postgres, Redis, Asterisk ARI (8088), or metrics.
- Webhook endpoints: allow only provider source ranges where the provider publishes them; signature validation is still mandatory.
- If the browser calls the API directly from another origin, set `CALLSHIELD_CORS_ALLOW_ORIGINS` (below). Preferred: serve both from one origin through the proxy and leave CORS unset.
- Add rate limits at the proxy as a second layer.
- SIP/RTP ports (if you ever enable Asterisk) need firewall rules limited to your provider; SIP exposed to the internet is a toll-fraud target.

## Secrets

- `.env` is gitignored; never commit it. Use file permissions `600`.
- Generate: `openssl rand -hex 32` for `CALLSHIELD_JWT_SECRET` and `CALLSHIELD_WEBHOOK_SECRET` (`callshield init` does this for you).
- Rotation: change the value, restart `api` and `worker`. Rotating `CALLSHIELD_JWT_SECRET` invalidates sessions. Rotate the webhook secret in coordination with the provider (accept old and new during overlap if supported).
- Rotate immediately on suspected compromise, and also revoke SIP trunk credentials at the provider (PRD 7.3).
- Rotate Postgres password by altering the role, updating `.env`, and restarting.
- Consider a secrets manager (Docker/Compose secrets, SOPS) for anything beyond a single-user local install.

## Backups and retention

Back up Postgres and the evidence storage volume. Retention defaults are design defaults ([`data-retention.md`](../privacy/data-retention.md)); backups outlive deletion unless you handle erase cycles.

## Monitoring

`infra/monitoring/prometheus.yml` and `alerts.yml` give a starting point. Metrics must not contain phone numbers or transcript text.

## Incident basics

Credential compromise: revoke trunk access, rotate secrets, review the audit log, and alert affected users. Vulnerabilities in this project: [`SECURITY.md`](../../SECURITY.md).
