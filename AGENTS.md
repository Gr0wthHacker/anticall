# AGENTS.md — conventions for agents working on CallShield Network

Spec: `docs/prd/PRD_v3.md`. Backend: `apps/api` (Python 3.11+, FastAPI, SQLAlchemy 2, one distribution `callshield`, src layout).

## Hard rules

- Do not run git. The orchestrator commits.
- Never log raw phone numbers. Use `callshield.core.phone.mask_phone` / `core.redaction`. No `print`/`console.log`.
- Fictional numbers only in tests/fixtures: `+1415555 01xx` (e.g. `+14155550123`; `is_reserved_test_number`).
- No real network calls in tests (no carrier, STT, LLM, agency endpoints). Inject fakes.
- No LLM may be given dial capability or the ability to decide on calls/filings (PRD 9, 19.12). Deterministic policy gates decide; models only classify/summarize, and their output is labeled `MODEL_INFERRED`.
- All risky features default OFF (`outbound_enabled`, `live_stt_enabled`, `ai_outbound_enabled`, `community_enabled`, `filing_submission_enabled`). Gate code on `Settings`; raise `PolicyBlockedError` when off. Never flip defaults.
- Never output `VIOLATION_PROVEN`; never equate ACKNOWLEDGED with suppression; never invent a receipt/confirmation number.
- Caller-ID-only destinations can never be dialed automatically. `start-contact` is removed (returns 410); callbacks go through the gated callback flow, see docs/prd/CHANGE_callback_flow.md. (Superseded text: `start-contact` needed a verified destination + explicit `ContactAuthorization`, PRD 8.1.)
- Corrections are appended (new rows/events/versions), never in-place edits of evidence.

## Layout (`apps/api/src/callshield/`)

| Path | Purpose |
|---|---|
| `core/` | settings, ids, clock, hashing, phone, redaction, idempotency, `errors` (ApiError family) |
| `domain/` | `enums.py` (all StrEnums), `topics.py` (event topics), `state_machines.py` |
| `db/` | `base.py`, `session.py`, `models/{tenancy,defend,resolve,enforce,community,platform}.py` |
| `ledger/` | `append_event(...)` idempotent append-only events |
| `audit/` | `record_audit`, `record_case_audit` |
| `auth/` | tokens, passwords, `deps.py`, `scoping.py`, `mfa.py` (stub) |
| `api/` | `create_app()`, `errors.py`, `discovery.py`, `routers/` |
| `worker/` | DB job queue, `handlers/` auto-imported |

## Shared files — DO NOT EDIT (ask the orchestrator)

`core/*`, `domain/enums.py`, `domain/topics.py`, `domain/state_machines.py`, `db/base.py`, `db/session.py`, `db/models/*` (existing tables), `ledger/*`, `audit/*`, `auth/*`, `api/app.py`, `api/errors.py`, `api/discovery.py`, `api/routers/{auth,health,meta}.py`, `worker/{queue,runner}.py`, `alembic/versions/0001_*`, `tests/conftest.py`, `pyproject.toml`, `AGENTS.md`.
Need a new column/enum value/topic? Put it in your own new migration/module and report it; do not patch shared files.

## How to add things (zero shared-file edits)

- **Router**: create `api/routers/<module>.py` exporting `router = APIRouter(tags=[...])` with full paths (`@router.get("/v1/lines")`). Auto-mounted. Do not use local imports for types used in route signatures (FastAPI cannot resolve them).
  Auth: `ctx: Annotated[TenantContext, Depends(tenant_context)]`; roles: `Depends(require_role(Role.HOUSEHOLD_ADMIN, ...))`.
- **Module logic**: put services in `callshield/<module>/` (own package, e.g. `defend/`, `resolve/`, `enforce/`); routers stay thin.
- **Migration**: new file `alembic/versions/<rev>_<desc>.py` with `down_revision` = current head. New tenant tables must `ENABLE ROW LEVEL SECURITY` + `tenant_isolation` policy on Postgres (copy `_enable_rls` pattern) and use `TenantOwned` (tenant_id NOT NULL, FK, indexed). New models: own file `db/models/<module>_*.py`, and import it from your module so metadata registers (tests build schema via `Base.metadata.create_all`).
- **Job handler**: `worker/handlers/<name>.py` with `@job_handler("kind") def run(session, job) -> None`; enqueue via `enqueue(session, tenant_id, kind, payload, idempotency_key=...)`. Handlers must be idempotent; raise to retry (backoff, then DEAD).
- **Event**: `append_event(session, tenant_id, Topic.X, payload, EventOrigin.Y, idempotency_key, provider_ref=None) -> (event, created)`. Derive keys with `core.idempotency` helpers. If `created` is False, do not redo side effects.
- **Audit**: `record_audit(session, tenant_id, actor, action, data)`; per-case `record_case_audit(...)`. Every legal/financial/telephony command records actor, scope, authorization basis, idempotency key.
- **State changes**: `can_transition(Machine.RESOLUTION | Machine.ENFORCE_CASE, from, to, context)` — guards fail closed; add stricter edges with `register_guard`. Orthogonal case fields (attribution, recording authorization, legal assessment, resolution) are separate columns.
- **Tests**: `apps/api/tests/{unit,integration}/test_<module>_*.py`. Fixtures: `engine`, `session`, `client`, `settings`, `make_tenant_user(name)` (-> `.headers`, `.tenant_id`), `two_tenants` (A, B), `tenant_row`. Any new tenant-owned endpoint needs a cross-tenant denial test (404, never 403, never data leak).

## Tenant scoping rules

1. Every query on tenant-owned tables filters by `tenant_id`. Use `scoped_get(session, Model, id, tenant_id)`, `scoped_get_or_404`, `scoped_select`, or `ctx.repo(Model)` (`TenantScopedRepo`: `get/get_or_404/list/add`, `add` stamps tenant_id and ignores caller overrides). Never `session.get(Model, id)` on tenant data.
2. Route handlers get tenant from the verified principal, never from the request body/path.
3. Provider webhooks: resolve tenant from the verified line/signature, never from payload fields; malformed webhooks must not route across tenants.
4. On Postgres, RLS (`app.tenant_id`) is defence in depth, not a substitute for rule 1. Run the app as a non-owner DB role.

## Quality bar

Type-annotated, functions < 50 lines, descriptive names, `from __future__ import annotations`. Before finishing (from `apps/api`):
`.venv/Scripts/ruff check . && .venv/Scripts/ruff format . && .venv/Scripts/mypy && .venv/Scripts/python -m pytest`
If you add routes, regenerate the spec: `.venv/Scripts/python scripts/export_openapi.py`. Don't add dependencies without asking.
