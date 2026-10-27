# Contributing

Read [`README.md`](README.md) and the non-goals first. This project is pre-alpha and synthetic-only. Contributions that make it easier to harass, spoof, or bulk-dial will be closed.

## Dev setup

API (Python 3.12):

```bash
cd apps/api
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy src && pytest
```

Dashboard (Node 22), once `apps/dashboard` exists:

```bash
cd apps/dashboard && npm ci && npm run lint && npm run typecheck && npm run build
```

Policy guard (plain Python, no deps):

```bash
python scripts/policy_guard.py
```

Copy `env.example` to `.env`. Never commit `.env` or credentials.

## Workflow

- Branches: `feature/short-desc`, `fix/short-desc`
- Commits: imperative, lowercase, no trailing period (`fix sitemap redirect loop`)
- PRs are squash-merged to `main`
- Functions under 50 lines. No `console.log` or `print` in committed code. TypeScript strict on new code.

## Tests

- Every behavior change ships with tests. Safety gates need a negative test (the blocked path is proven blocked).
- Run the full suite and linters before opening a PR. CI must pass.
- Changes to policy rules, state machines, or ledger code need unit tests for every transition and guard (PRD sections 5.2.1, 19.3).
- See [`docs/developer/testing.md`](docs/developer/testing.md).

## Fictional numbers only

Fixtures, docs, and examples use fictional `555-01xx` numbers (for example `+1-555-0100` to `+1-555-0199`) or provider-reserved test numbers. Never commit real phone numbers, recordings, transcripts, or real people's data. Real calling happens only in authorized, controlled environments, never in CI.

## Safety rules

These PRs require review and approval from the security owner (CODEOWNERS) before merge:

- Anything that adds **dial capability** or changes outbound call code
- Anything that gives an **LLM a tool** or widens a tool's scope (the AI package has no dial capability; see ADR 0003)
- Anything that **loosens a gate**: destination verification, attempt limits, cooldowns, recording/processing consent, attribution gate, approval/attestation
- Changes to recording, transcription, or federation (privacy owner)
- Changes to legal templates or filing adapters (legal reviewer)

Never add: bulk dialing, auto-redial loops, caller ID override, CAPTCHA bypass, simulated agency APIs, or auto-submission without per-filing user approval.

Every command that triggers a legal, financial, or telephony action carries `actor_id`, `scope`, `authorization_basis`, and `idempotency_key`.

## Adding dependencies

Open an issue first. Check license compatibility with AGPL-3.0.

## Reporting security issues

Do not use public issues. See [`SECURITY.md`](SECURITY.md).

## Conduct

[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) applies.
