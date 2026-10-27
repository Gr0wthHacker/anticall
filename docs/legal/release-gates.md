# Release Gates

> Source: PRD sections 15.1 and 19.14. Nothing here is legal advice. A box is checked only with a linked artifact (test run, memo, sign-off). **No gate is passed yet.** The repository is cleared only for scaffolding, interface contracts, synthetic test harness, and local prototype work (PRD 22).

## MVP exit gates (15.1)

- [ ] Proven line ownership and carrier-compatible routing
- [ ] Authorized processing of media under jurisdictional policy
- [ ] Inbound screening, user transfer, and fallback tested
- [ ] Controlled IVR/DTMF path can reach agent through option 2
- [ ] User-initiated outbound agent clearly discloses role and request
- [ ] Every contact associated with a real case, verified destination, and recorded authorization
- [ ] Further calls never form an unchecked redial sequence
- [ ] Source provenance and user corrections implemented
- [ ] High-impact hallucination, spoofing, and privacy failure tests passed
- [ ] Clean Docker install by an independent engineer
- [ ] Legal counsel clears deployed workflows and published agreements (see `counsel-checklist.md`)

## ENFORCE measurable gates (19.14)

- [ ] 100% of simulated authenticated provider events either retained once or placed in a recoverable error queue
- [ ] 0 false `SUBMITTED` states in failure-injection tests
- [ ] 0 transmissions absent exact-draft user authorization
- [ ] 100% of exported attachments recorded in a digest manifest
- [ ] 100% of cross-tenant access tests deny access
- [ ] 0 unsupported agency API calls falsely represented as official electronic filing
- [ ] 0 automatically inferred legal guilt or asserted damages calculations
- [ ] No more than 0.5% mislinked incidents in the manually reviewed validation corpus (target; must be measured)

## Pre-release housekeeping (21.2, 13)

- [ ] LICENSE unmodified; notices and third-party licenses reviewed; SBOM and license scan published
- [ ] DISCLAIMER separate from license; no immunity claims
- [ ] Decisions D-001..D-008 closed (`docs/developer/decision-log.md`)
- [ ] Security and privacy review completed externally
- [ ] Reproducible release process and security advisory channel live

## Capability gates (what stays off until the above pass)

| Capability | Env flag (default false) |
|---|---|
| Real telephony events | `CALLSHIELD_TELEPHONY_ENABLED` |
| Outbound calling | `CALLSHIELD_OUTBOUND_CALLING_ENABLED` |
| AI on calls | `CALLSHIELD_AI_AGENT_ENABLED` |
| Live STT / recording / persisted transcripts | `CALLSHIELD_LIVE_STT_ENABLED`, `CALLSHIELD_RECORDING_ENABLED`, `CALLSHIELD_TRANSCRIPTION_PERSIST_ENABLED` |
| Agency submission | `CALLSHIELD_AGENCY_SUBMISSION_ENABLED` |
| Community sharing | `CALLSHIELD_COMMUNITY_ENABLED` |
