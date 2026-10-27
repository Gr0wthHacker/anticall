# Self-Hosting

> **Pre-alpha. Synthetic/local mode only.** Real telephony, recording, live AI processing, outbound calling, and agency filing are not cleared (see [`docs/legal/release-gates.md`](../legal/release-gates.md)). Maintainers do not operate or supervise self-hosted instances. You are responsible for legal compliance, provider terms, and security. Not legal advice.

## Local synthetic mode

Nothing connects to a real carrier. Provider events are synthetic fixtures. Use fictional numbers (555-01xx).

```bash
cp env.example .env
# edit .env: replace every CHANGE_ME. Leave all *_ENABLED flags false.
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
- Outbound calls
- Programmatic complaint submission

Before enabling any of these you need the relevant gate passed, counsel review for your jurisdiction, and a provider that permits the use.

## TLS and reverse proxy

Do not expose the API or dashboard directly. Put a reverse proxy (Caddy, nginx, Traefik, or Cloudflare Tunnel) in front:

- Terminate TLS 1.2+ at the proxy; redirect HTTP to HTTPS; enable HSTS.
- Proxy only the dashboard and `/v1` API. Never expose Postgres, Redis, Asterisk ARI (8088), or metrics.
- Webhook endpoints: allow only provider source ranges where the provider publishes them; signature validation is still mandatory.
- Set `CALLSHIELD_ALLOWED_ORIGINS` to your real dashboard origin.
- Add rate limits at the proxy as a second layer.
- SIP/RTP ports (if you ever enable Asterisk) need firewall rules limited to your provider; SIP exposed to the internet is a toll-fraud target.

## Secrets

- `.env` is gitignored; never commit it. Use file permissions `600`.
- Generate: `openssl rand -hex 32` for `CALLSHIELD_SECRET_KEY` and `CALLSHIELD_WEBHOOK_SIGNING_SECRET`.
- Rotation: change the value, restart `api` and `worker`. Rotating `SECRET_KEY` invalidates sessions. Rotate the webhook secret in coordination with the provider (accept old and new during overlap if supported).
- Rotate immediately on suspected compromise, and also revoke SIP trunk credentials at the provider (PRD 7.3).
- Rotate Postgres password by altering the role, updating `.env`, and restarting.
- Consider a secrets manager (Docker/Compose secrets, SOPS) for anything beyond a single-user local install.

## Backups and retention

Back up Postgres and the evidence storage volume. Retention defaults are design defaults ([`data-retention.md`](../privacy/data-retention.md)); backups outlive deletion unless you handle erase cycles.

## Monitoring

`infra/monitoring/prometheus.yml` and `alerts.yml` give a starting point. Metrics must not contain phone numbers or transcript text.

## Incident basics

Credential compromise: revoke trunk access, rotate secrets, review the audit log, and alert affected users. Vulnerabilities in this project: [`SECURITY.md`](../../SECURITY.md).
