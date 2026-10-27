# ADR 0003: The LLM has no dial capability

- Status: Accepted
- Date: 2026-10-27
- Source: PRD 3.2, 7.1, 11.1, 11.2, 19.12

## Context

LLMs can be prompt-injected through caller speech and can hallucinate. An LLM able to place calls could be steered into harassment, toll fraud, or calling protected targets.

## Decision

The `ai_agent` package exposes no tool that originates a call, changes a destination, alters policy, or submits a filing. Outbound calls originate only from a deterministic policy-gated command that requires `actor_id`, `scope`, `authorization_basis`, `idempotency_key`, a verified destination, a case, and explicit user approval. The AI may speak within a session that this path opened, and may emit only bounded, schema-validated intents (for example, "send DTMF digit from the pre-approved path", "end call") that deterministic code validates. No LLM decides whether to place another call or send a complaint.

## Enforcement

- `scripts/policy_guard.py` fails CI if `dial(` or similar appears in the `ai_agent` package.
- Negative tests prove injected speech cannot trigger a dial.
- Changes to AI tools require security-owner review (CODEOWNERS).

## Consequences

Some conveniences (the agent deciding to retry) are impossible by design. That is intended.
