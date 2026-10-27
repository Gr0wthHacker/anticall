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

## Callback release gates (CHANGE_callback_flow, ADR 0006, D-009)

> Build is complete behind `callback_enabled=false`. Ship to real numbers only after counsel signs off. If G3 comes back unfavorable, the fallback is already supported: set `listen_window_seconds` to 0 and extraction starts after the disclosure.

| Gate | Blocks | Counsel question | Status |
|---|---|---|---|
| G1 Consent theory | All callbacks | Does an inbound call from a number give implied prior express consent under 47 USC 227(b) for one disclosed, automated return call to that number? | **OPEN - blocking** |
| G2 Spoof exposure | All callbacks | What is our exposure if an A-attested inbound number was nonetheless spoofed, and is the A-attestation gate a sufficient precaution? | **OPEN - blocking** |
| G3 Listen window | Listen-window extraction | Is live local extraction before disclosure, with no audio or transcript persisted, "recording" under all-party statutes including Cal. Penal Code 632.7? If yes, disclosure must move to the first utterance and extraction start after it. | **OPEN - blocking** |
| G4 State automated-dialer laws | All callbacks | Do state laws on automated or prerecorded messages (Indiana first) apply to a noncommercial return call, and what do they require? | **OPEN - blocking** |
| G5 Provider terms | Provider selection | Do the chosen SIP trunk and any model or voice vendors permit this use? | **OPEN - blocking** |

- [ ] G1-G5 closed with linked counsel deliverables (`counsel-checklist.md`, rows i-m)

## Pre-release housekeeping (21.2, 13)

- [ ] LICENSE unmodified; notices and third-party licenses reviewed; SBOM and license scan published
- [ ] DISCLAIMER separate from license; no immunity claims
- [ ] Decisions D-001..D-009 closed (`docs/developer/decision-log.md`)
- [ ] Security and privacy review completed externally
- [ ] Reproducible release process and security advisory channel live

## Capability gates (what stays off until the above pass)

| Capability | Env flag (default false) |
|---|---|
| Real telephony events | `CALLSHIELD_TELEPHONY_ENABLED` |
| Outbound calling | `CALLSHIELD_OUTBOUND_CALLING_ENABLED` |
| Automated callbacks (blocked by G1-G5) | `CALLSHIELD_CALLBACK_CALLBACK_ENABLED` |
| AI on calls | `CALLSHIELD_AI_AGENT_ENABLED` |
| Live STT / recording / persisted transcripts | `CALLSHIELD_LIVE_STT_ENABLED`, `CALLSHIELD_RECORDING_ENABLED`, `CALLSHIELD_TRANSCRIPTION_PERSIST_ENABLED` |
| Agency submission | `CALLSHIELD_AGENCY_SUBMISSION_ENABLED` |
| Community sharing | `CALLSHIELD_COMMUNITY_ENABLED` |
