# Privacy Notice (Template for Operators)

> **DRAFT - NOT LEGAL ADVICE. TEMPLATE ONLY.** This follows the outline in PRD Appendix D. An operator must replace the bracketed fields with the facts of their own deployment and have counsel review applicability of state privacy statutes. Do not publish it as-is as an operational notice.

## 1. Who operates the deployment

- Upstream project maintainers publish source code. They are **not** the operator, controller, or processor of self-hosted installations, and receive no data from them.
- Operator of this deployment: `[legal entity or individual]`, `[address]`, privacy contact `[email]`.

## 2. What the software currently collects (self-hosted, default configuration)

Stored in your own PostgreSQL database:
- Verified line data (protected number, ownership verification state)
- Call metadata (timestamps, call ID, observed caller ID, duration, route, disposition)
- Caller-stated claims (name, purpose), labeled as claims
- Opt-out cases, destination provenance, attempt records, user corrections
- User settings, account identifiers, audit events
- Audio and transcripts **only** if recording/processing is enabled and legally authorized. Default: disabled.

## 3. Current data flows

| Flow | Default state |
|---|---|
| Telemetry / analytics to maintainers | None. No telemetry exists. |
| Community network sharing | Off. Requires separate affirmative opt-in. Not implemented. |
| Remote AI (STT/LLM/TTS) | Off. Local/synthetic only. |
| Outbound complaint submission | Off. Manual handoff only; export is user-initiated. |
| Logs | Structured, redacted, 14-day default retention (design default) |

## 4. Why data is processed

Routing, screening, case resolution, evidence exports, security, and (only with separate consent) community intelligence.

## 5. Processors and recipients

Operator must list: telephony carrier `[ ]`, AI provider `[ ]`, hosting/storage vendor `[ ]`, any export recipient `[ ]`. State "none" only if true.

## 6. Retention

Design defaults (not legal requirements; see [`docs/privacy/data-retention.md`](docs/privacy/data-retention.md)): metadata 90 days, recordings 30 days, transcripts 30 days, opt-out cases 1 year by user choice, observability logs 14 days. Operator: `[actual configured values, backup cycle, legal holds]`.

## 7. Choice and access

Export, correction, deletion, consent changes, and sharing opt-out where applicable. Deletion may be limited by legal hold; the operator must say so.

## 8. Security

Role-based access, tenant isolation, encrypted secrets, redacted logs, incident response procedure. No promise of absolute security.

## 9. International processing

Operator: `[disclose if audio or metadata leaves your jurisdiction]`.

## 10. Community network

Not available. If added later, it requires separate affirmative consent and documents linkage and re-identification risks and a correction process.

## 11. Contact and updates

Version `[x]`, date `[date]`, contact `[email]`. Material changes will be announced `[how]`.
