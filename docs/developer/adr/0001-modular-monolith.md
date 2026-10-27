# ADR 0001: Modular monolith for R0/R1

- Status: Accepted (prototype)
- Date: 2026-10-27
- Source: PRD 7.1, 20.1

## Context

Sixteen epics (E01-E16) could be mapped to sixteen services. The project is solo-maintained, pre-alpha, and has no measured operational load.

## Decision

One deployable API application with internal domain modules (policy, ledger, cases, evidence, auth, audit), plus a worker process from the same codebase for telephony/media and background jobs. Module boundaries are enforced by package structure and import rules, not by network calls.

## Consequences

- Simple local install (compose: postgres, api, worker, dashboard).
- Transactional consistency between ledger and case state.
- Services are split out only for a measured operational need (media latency, isolation of the telephony worker), recorded in a new ADR.
- Discipline required: no cross-module reach-ins; the policy engine stays standalone.

## Alternatives rejected

- Microservices from day one: operational cost with no benefit at this scale.
- Single-package spaghetti: makes later extraction and safety review hard.
