# ADR 0004: Evidence is append-only with lawful deletion

- Status: Accepted (prototype)
- Date: 2026-10-27
- Source: PRD 19.1, 19.4, 19.6, 22, 11.3

## Context

Evidence needs a trustworthy trail, but users have deletion rights and retention is limited by design. "Immutable" evidence claims would be false.

## Decision

- Source events are append-only at the logical level. Corrections, reclassifications, unlinks, and redactions are new events/versions; originals and derived data are stored separately and never silently overwritten.
- Every item carries a provenance label: `OBSERVED`, `CLAIMED`, `MODEL_INFERRED`, `USER_ATTESTED`, `EXTERNALLY_VERIFIED`.
- Artifacts carry SHA-256 digests, authorization state, retention, and source. Hashes detect modification only; the docs never claim authenticity, chain of custody, or admissibility.
- Underlying files and personal data can be deleted under retention, user request, or legal process, subject to legal holds. Deletion is itself an audit event; tombstones keep the digest and timestamps where lawful.
- Database permissions block UPDATE/DELETE on ledger and audit tables for the application role (retention jobs use a separate, audited path).

## Consequences

- Export manifests list omissions explicitly.
- No promise of mathematical immutability to users.
- Retention and deletion code needs its own tests and review.
