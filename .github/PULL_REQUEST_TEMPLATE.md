## What and why

<!-- One or two sentences. Link the PRD requirement ID(s), e.g. R-009, ENF-003. -->

## Checklist

- [ ] Commits are imperative, lowercase, no trailing period; branch is `feature/` or `fix/`
- [ ] Tests added or updated; `ruff`, `mypy`, `pytest`, and `python scripts/policy_guard.py` pass
- [ ] Fixtures use fictional numbers (555-01xx) only; no real numbers, recordings, personal data, or secrets
- [ ] Docs updated (`docs/developer/traceability.md` status, ADR if architecture changed)

## Safety gates (check all that apply, or confirm none apply)

- [ ] None of the below apply
- [ ] Adds or changes **dial / outbound capability** (security-owner review required)
- [ ] Adds or widens an **LLM tool** (security-owner review required; the AI has no dial capability, ADR 0003)
- [ ] **Loosens a gate**: destination verification, attempt limits/cooldowns, consent/recording, attribution, approval/attestation (security-owner review required)
- [ ] Touches **recording, transcription, or community sharing** (privacy-owner review)
- [ ] Touches **legal templates or filing adapters** (legal-reviewer review)
- [ ] Changes **data flows** (updated `docs/privacy/data-flows.md` and `PRIVACY.md`)
- [ ] Adds a **dependency** (license checked against AGPL-3.0; approved in an issue)

I confirm this PR adds no bulk dialing, redial loops, caller ID override, CAPTCHA bypass, simulated agency APIs, or auto-submission without per-filing user approval.
