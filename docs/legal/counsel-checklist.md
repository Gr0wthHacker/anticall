# Counsel Deliverables Checklist

> **DRAFT - gate checklist. NOT LEGAL ADVICE.** Source: PRD Appendix E item 10 and section 21.2. These are required before public launch, real outbound calling, recording, or agency submission. Nothing is complete yet.

| # | Deliverable | Gate for | Owner | Status | Date / reference |
|---|---|---|---|---|---|
| a | License and attribution memo (AGPL-3.0-only vs alternatives; third-party model/voice/code compatibility; unmodified license text; notices) | Any public distribution (D-004) | Counsel | NOT STARTED | |
| b | Federal and state telecom functionality review (TSR, TCPA incl. AI voices, 47 USC 223, state rules) for Defend and Resolve | Outbound calling; R1.5 | Counsel | NOT STARTED | |
| c | Recording / AI interception matrix (live processing and persistence, multi-jurisdiction) | Recording, live STT (D-002) | Counsel | NOT STARTED | `recording-consent-matrix.md` |
| d | Contract and disclaimer assessment (DISCLAIMER, acknowledgment, hosted ToS, indemnity, venue) | Public docs; hosted mode | Counsel | NOT STARTED | |
| e | Privacy and consumer-rights assessment (state privacy statutes, deletion limits, notice) | Any data collection beyond local synthetic | Counsel | NOT STARTED | |
| f | Hosted provider agreement review (SIP/AI vendors permit automated/AI calls?) | Telephony; outbound | Counsel | NOT STARTED | `provider-compatibility-matrix.md` |
| g | Insurance review | Public launch (D-008) | Maintainer + counsel | NOT STARTED | |
| h | Abuse-handling playbook | Public launch | Maintainer + counsel | NOT STARTED | |
| i | **G1 Consent theory (OPEN - blocking).** Does an inbound call from a number give implied prior express consent under 47 USC 227(b) for one disclosed, automated return call to that number? | All callbacks | Counsel | NOT STARTED | `CHANGE_callback_flow.md`, D-009 |
| j | **G2 Spoof exposure (OPEN - blocking).** What is our exposure if an A-attested inbound number was nonetheless spoofed, and is the A-attestation gate a sufficient precaution? | All callbacks | Counsel | NOT STARTED | ADR 0006 residual risk |
| k | **G3 Listen window (OPEN - blocking).** Is live local extraction before disclosure, with no audio or transcript persisted, "recording" under all-party statutes including Cal. Penal Code 632.7? If yes, disclosure must move to the first utterance and extraction start after it. | Listen-window extraction | Counsel | NOT STARTED | Fallback: `listen_window_seconds=0` |
| l | **G4 State automated-dialer laws (OPEN - blocking).** Do state laws on automated or prerecorded messages (Indiana first) apply to a noncommercial return call, and what do they require? | All callbacks | Counsel | NOT STARTED | |
| m | **G5 Provider terms (OPEN - blocking).** Do the chosen SIP trunk and any model or voice vendors permit this use? | Provider selection | Counsel | NOT STARTED | `provider-compatibility-matrix.md` |

## Additional review items (PRD 21.2)

- [ ] Hosted offering: enforceable terms with assent mechanism, venue, dispute resolution
- [ ] `ACCEPTABLE_USE.md` reviewed against actual hosted scope
- [ ] Filing attestation adapted per jurisdiction (`filing-attestation-v1`)
- [ ] Acknowledgment text reviewed for enforceability (`acknowledgment-v1`)
- [ ] Disclosures updated before any direct agency submission channel is enabled
- [ ] Entity formation and trademark clearance (D-008)

Do not mark an item complete without a written counsel deliverable or sign-off referenced in the last column.
