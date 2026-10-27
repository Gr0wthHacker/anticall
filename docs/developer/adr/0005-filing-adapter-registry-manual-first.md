# ADR 0005: Filing adapter registry, manual handoff first

- Status: Accepted (prototype)
- Date: 2026-10-27
- Source: PRD 19.7, 19.8, ENF-015, ENF-016, ENF-023, D-006

## Context

No agency-approved programmatic submission API for individual unwanted-call complaints has been verified. Simulated APIs, headless form posting, or CAPTCHA bypass would misrepresent official filing and risk abuse.

## Decision

- Filing goes through a `FilingAdapter` registry (`documented_api` | `manual_handoff`).
- Only `manual_handoff` adapters ship now. They implement `validate` and `prepare` but not `submit`. The user receives a copyable prepared submission and the verified official destination.
- Each destination has a registry record: source URL, capability evidence, last-reviewed date, authorization requirements, supported fields, attachment limits, retention, sunset procedure.
- `POST /v1/filing-drafts/{id}/submit` returns `422 UNSUPPORTED_DESTINATION` when no supported lawful electronic interface exists.
- `SUBMITTED` requires a real transport acknowledgment. Confirmation numbers are never generated. Ambiguous failures yield `UNKNOWN`, never silent retry.
- Each submission needs an exact-payload-digest user approval and a unique idempotency key.

## Consequences

- Users do a manual step for now.
- Adding a `documented_api` adapter needs written permission for the channel, legal review, updated data-transfer disclosures, and security-owner/legal-reviewer approval.
