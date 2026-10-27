# Decision Log

Source: PRD section 20.4. These must be decided before merge to main for public deployment. All are **OPEN**. The prototype default lets local/synthetic work proceed and is not a commitment.

| ID | Decision | Status | Prototype default | Must be decided before public deployment |
|---|---|---|---|---|
| D-001 | First supported telephone transport and provider | OPEN | Asterisk + generic SIP trunk, behind an adapter interface; synthetic provider events in tests | Named provider(s) verified to permit inbound screening and user-initiated automated/AI outbound; webhook signing and idempotency semantics confirmed; compatibility matrix filled with evidence |
| D-002 | Live STT consent gate by legal jurisdiction | OPEN | Treat `NOT_EVALUATED` as DENIED: no live STT, no persisted audio, no remote STT | Counsel-approved jurisdiction matrix (`docs/legal/recording-consent-matrix.md`); engine rules and tests derived from it |
| D-003 | Hosting vs fully local processing | OPEN | Fully local, self-hosted; remote AI disabled | Supported deployment modes; processor disclosures for any hybrid mode; privacy notice per mode |
| D-004 | AGPL-3.0-only vs permissive licensing | OPEN | AGPL-3.0-only (provisional) | Counsel license and attribution memo; dependency/model/voice license compatibility; copyright holder named |
| D-005 | Incident-to-case match threshold | OPEN | Conservative: auto-link only on strong multi-feature evidence, otherwise queue for review; never on displayed number alone | Threshold validated against a reviewed corpus; mislink rate measured against the 0.5% target |
| D-006 | Initial FTC/FCC forms; any agency-approved submission API | OPEN | Manual handoff only; dry-run draft generation; no remote submission | Destination registry entries with source URL, capability evidence, last-reviewed date; written confirmation before any programmatic adapter |
| D-007 | Data retention and deletion in operator jurisdictions | OPEN | Design defaults: metadata 90d, recordings 30d, transcripts 30d, cases 1y, logs 14d | Per-jurisdiction retention and deletion rules; legal-hold design; backup erase cycle |
| D-008 | Legal entity, trademark clearance, insurance | OPEN | None; "CallShield Network" is a working name | Entity formed; name cleared; insurance reviewed; copyright holder in NOTICE |

## Process

- Move a row to DECIDED only with a dated entry below naming the decider, alternatives considered, and links to evidence (memo, test results).
- Reversing a decision adds a new entry; never edit history.

## Decisions made

None yet.
