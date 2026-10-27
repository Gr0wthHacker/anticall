# CallShield API

Modular monolith (FastAPI + SQLAlchemy 2). See `/AGENTS.md` for conventions.

## Setup

```bash
cd apps/api
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # Windows; use .venv/bin/ on Linux/macOS
# PostgreSQL support: pip install -e ".[postgres]"
```

## Dev commands

```bash
.venv/Scripts/python -m pytest                        # tests (SQLite in-memory)
.venv/Scripts/ruff check . && .venv/Scripts/ruff format .
.venv/Scripts/mypy
.venv/Scripts/python scripts/export_openapi.py        # writes openapi.json

export CALLSHIELD_DB_URL=sqlite:///./callshield.db    # or postgresql+psycopg://...
.venv/Scripts/alembic upgrade head
.venv/Scripts/uvicorn --factory callshield.api.app:create_app --reload
.venv/Scripts/python -m callshield.worker             # DB-queue worker
```

First tenant: `POST /v1/tenants` (allowed while no tenant exists, or when `CALLSHIELD_ALLOW_BOOTSTRAP=true`).

## Configuration (`CALLSHIELD_` prefix)

Gated features, all default `false`: `OUTBOUND_ENABLED`, `LIVE_STT_ENABLED`, `AI_OUTBOUND_ENABLED`,
`COMMUNITY_ENABLED`, `FILING_SUBMISSION_ENABLED`. Check state at `GET /v1/meta/capabilities`.
Set `JWT_SECRET` for anything beyond local dev. Safety limits: `MAX_CALL_SECONDS`,
`OUTBOUND_COOLDOWN_SECONDS` (900), `MAX_ATTEMPTS_PER_CASE_PER_DAY` (3).

Other settings:

- `CORS_ALLOW_ORIGINS` (JSON list, default `[]` = no CORS headers), e.g.
  `CALLSHIELD_CORS_ALLOW_ORIGINS='["https://app.example.com"]'`. Explicit origins only; `"*"` is
  rejected at startup. Credentials are never allowed (auth is a bearer header).
- `WEBHOOK_RECEIPT_RETENTION_DAYS` (30): the `webhook.purge_receipts` job deletes replay nonces
  older than this for the job's tenant. Enqueue it per tenant on a schedule.
- `FILING_UNKNOWN_VERIFY_AFTER_HOURS` (24): `GET /v1/filings/{id}` reports
  `needs_user_verification` once a submission has been UNKNOWN this long. The user exits UNKNOWN
  with `POST /v1/filings/{id}/resolve-unknown` (`verified_submitted` | `verified_not_submitted` |
  `abandon`); the server never resends or invents a receipt.

Provider feeds: `POST /v1/webhooks/{provider}/{line_key}/outbound-attestation` (same HMAC signing
as the call webhook) records the provider-reported outbound attestation level; only `A` unblocks
callbacks. Acknowledgment: `GET /v1/acknowledgments/current`, `POST /v1/acknowledgments`,
`GET /v1/acknowledgments/me`.

## PostgreSQL notes

The initial migration enables row-level security (`app.tenant_id`) on every tenant table and a
trigger blocking UPDATE/DELETE/TRUNCATE on `ledger_events`. Run the API as a non-owner role (owners
bypass RLS); the worker's job-claim query is cross-tenant and needs a role with BYPASSRLS. MFA/OIDC
are interface stubs only (`auth/mfa.py`).
