# Testing

Source: PRD 16, 19.13, 19.14, 20.3.

## Rules

- Fictional numbers only (`+1-555-0100`..`+1-555-0199`) or provider-reserved test numbers. No real calls in CI, ever.
- Every safety gate has a negative test proving the blocked path is blocked.
- Fixtures contain no real recordings, transcripts, or personal data.
- Tests are deterministic. AI behavior is tested via fixtures and red-team suites, not live models, in CI.

## Layout

| Dir | Purpose |
|---|---|
| `apps/api/tests/unit` | Domain logic: policy, state machines, ledger, hashing |
| `apps/api/tests/integration` | API plus database, tenant isolation, idempotency |
| `tests/telephony` | Asterisk R0 harness scenarios (manual/optional in CI) |
| `tests/legal-policy` | Policy fixtures: consent gating, destination verification, attribution gate |
| `tests/red-team` | Prompt injection, spoofing, hallucination, privacy failures |

## Run

```bash
cd apps/api && pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy src && pytest
python scripts/policy_guard.py
```

## Required coverage areas

- **Telephony:** SIP timeout, 503, busy, answering machine, call waiting, transfer, voicemail, DTMF path drift, codec mismatch, lost media stream, concurrent sessions, duplicate events, emergency fallback.
- **Defend:** trusted caller reaches user; unknown screened; malformed webhook cannot cross tenants; AI outage -> configured fallback; screening disable is immediate.
- **Resolve:** caller-ID-only destination blocked; unverified company corrected; no unsupervised rapid redial after failure; `ACKNOWLEDGED` never maps to suppressed; stop conditions (R-013).
- **Enforce:** duplicate webhook creates one observation; spoofed CLI does not prove same operator; eleven manual attempts represented separately with no cutoff; transcript correction preserves original; export manifest has every attachment digest and no secrets or cross-tenant data.
- **Filing:** unsupported destination returns 422; no `SUBMITTED` without transport acknowledgment (failure injection); ambiguous response -> `UNKNOWN`, no blind resubmit; approval bound to payload digest; supplement needs new approval.
- **Security:** cross-tenant denial on every endpoint; webhook signature + replay; secrets absent from logs.

## Acceptance scenarios

The 12 scenarios in PRD 19.13 map to integration tests; track in [traceability.md](traceability.md).

## Gates

Measurable gates (PRD 19.14) are in [`docs/legal/release-gates.md`](../legal/release-gates.md). A green CI run does not mean a gate is passed unless the gate's specific test exists and ran.
