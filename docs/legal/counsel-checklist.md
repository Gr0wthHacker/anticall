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

## Additional review items (PRD 21.2)

- [ ] Hosted offering: enforceable terms with assent mechanism, venue, dispute resolution
- [ ] `ACCEPTABLE_USE.md` reviewed against actual hosted scope
- [ ] Filing attestation adapted per jurisdiction (`filing-attestation-v1`)
- [ ] Acknowledgment text reviewed for enforceability (`acknowledgment-v1`)
- [ ] Disclosures updated before any direct agency submission channel is enabled
- [ ] Entity formation and trademark clearance (D-008)

Do not mark an item complete without a written counsel deliverable or sign-off referenced in the last column.
