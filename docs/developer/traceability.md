# Requirements Traceability

Maps PRD requirement IDs to target module. Status is `planned` for all rows; update as code and tests land. Source: PRD 5.1-5.4, 19.2.

Note: PRD requirement IDs `D-001..D-012` (Defend) are unrelated to decision-log IDs `D-001..D-008`. The decision log is in [decision-log.md](decision-log.md).

| ID | Requirement | Priority | Module | Test | Status |
|---|---|---|---|---|---|
| D-001 | Enroll and verify line ownership | MVP | auth, domain/lines | | planned |
| D-002 | Trusted contacts and overrides | MVP | domain/contacts | | planned |
| D-003 | Deterministic routing before any LLM | MVP | policy-engine | | planned |
| D-004 | Screening agent only in authorized configs | MVP | policy-engine, ai-agent | | planned |
| D-005 | Caller name/purpose captured as claims | MVP | ledger, ai-agent | | planned |
| D-006 | Concise screening question and safe transfer | MVP | telephony-core, ai-agent | | planned |
| D-007 | Voicemail, unknown disposition, safe timeout | MVP | telephony-core | | planned |
| D-008 | Deliver configured removal request on live sales call | MVP | ai-agent, policy-engine | | planned |
| D-009 | Respect monitoring/recording/transcript limits | MVP | policy-engine (consent) | | planned |
| D-010 | One-click disable of screening | MVP | policy-engine, api | | planned |
| D-011 | Structured incident record for each action | MVP | ledger, audit | | planned |
| D-012 | Urgent-call labels with conservative handoff | MVP | policy-engine | | planned |
| R-001 | Case creation | MVP | case-management | | planned |
| R-002 | Destination provenance; caller-ID-only blocked | MVP | case-management, policy-engine | | planned |
| R-003 | Explicit user initiation; no target lists/auto-dial | MVP | policy-engine, telephony-core | | planned |
| R-004 | IVR navigator | MVP | ivr-navigator | | planned |
| R-005 | Queue and transfer management | MVP | telephony-core | | planned |
| R-006 | AI compliance conversation (states automated) | MVP | ai-agent | | planned |
| R-007 | Outcome typing | MVP | case-management | | planned |
| R-008 | Persistent case timeline / attempt records | MVP | case-management, ledger | | planned |
| R-009 | Reviewable further contact; attempt limits and cooldowns | MVP | policy-engine, case-management | | planned |
| R-010 | Resolution evidence: acknowledgment vs cessation | MVP | case-management | | planned |
| R-011 | Privacy-safe presentation | MVP | api, evidence | | planned |
| R-012 | Human-first alternative | MVP | telephony-core, dashboard | | planned |
| R-013 | Stop conditions | MVP | policy-engine, ai-agent | | planned |
| R-014 | Compliance-review parking; no LLM legal interpretation | MVP | policy-engine, case-management | | planned |
| E-001 | Append provider-observed call events | MVP | ledger, ingestion | | planned |
| E-002 | Provenance labels OBSERVED/CLAIMED/MODEL_INFERRED/USER_ATTESTED/EXTERNALLY_VERIFIED | MVP | ledger, evidence | | planned |
| E-003 | Retain authorized audio/transcripts per consent and retention | MVP | evidence, policy-engine | | planned |
| E-004 | Integrity hashes, custody changes, redactions | MVP | evidence | | planned |
| E-005 | Prepare reviewable FTC/FCC/carrier submissions | MVP | filing | | planned |
| E-006 | State legal uncertainty; never mark violations as established | MVP | assessment engine | | planned |
| E-007 | Offline PDF/JSON/CSV exports with versions | MVP | evidence/export | | planned |
| E-008 | Corrections, deletion requests, amendment audit | MVP | audit, evidence | | planned |
| E-009 | Reference official reporting paths; no mass reports | MVP | filing | | planned |
| E-010 | Transfer case to user's attorney | MVP | evidence/export | | planned |
| ENF-001 | Durable call-event ingestion | P0 | ingestion, ledger | | planned |
| ENF-002 | Evidence provenance | P0 | ledger, evidence | | planned |
| ENF-003 | Automatic case creation | P0 | case-management, worker | | planned |
| ENF-004 | Case history timeline | P0 | case-management, dashboard | | planned |
| ENF-005 | Artifact inventory | P0 | evidence | | planned |
| ENF-006 | Attribution gate | P0 | case-management, assessment engine | | planned |
| ENF-007 | User correction | P0 | case-management, audit | | planned |
| ENF-008 | No-recording fallback | P0 | policy-engine, ledger | | planned |
| ENF-009 | Case export with manifest | P0 | evidence/export | | planned |
| ENF-010 | Evidence authorization and retention | P0 | auth, evidence | | planned |
| ENF-011 | Eligibility engine | P1 | assessment engine | | planned |
| ENF-012 | Evidence completeness checklist | P1 | assessment engine | | planned |
| ENF-013 | Complaint templates | P1 | filing | | planned |
| ENF-014 | Attestation and approval | P1 | filing | | planned |
| ENF-015 | Filing adapter registry | P1 | filing | | planned |
| ENF-016 | Manual handoff | P1 | filing | | planned |
| ENF-017 | Receipt tracking | P1 | filing | | planned |
| ENF-018 | Dedupe and update | P1 | filing | | planned |
| ENF-019 | Legal-review flagging | P1 | assessment engine | | planned |
| ENF-020 | Rights and retention | P1 | evidence, audit | | planned |
| ENF-021 | External counsel sharing | P2 | evidence | | planned |
| ENF-022 | Network evidence corroboration | P2 | community (deferred) | | planned |
| ENF-023 | Automated submission (future verified channels) | P2 | filing (deferred) | | planned |
| C-001 | Disabled by default; informed opt-in | post-MVP | community (deferred, post-MVP) | | planned |
| C-002 | Minimized indicators only | post-MVP | community (deferred, post-MVP) | | planned |
| C-003 | Minimum group size; reidentification tests | post-MVP | community (deferred, post-MVP) | | planned |
| C-004 | No unsalted phone hashes as anonymous IDs | post-MVP | community (deferred, post-MVP) | | planned |
| C-005 | Multi-feature clustering | post-MVP | community (deferred, post-MVP) | | planned |
| C-006 | Contributor correction/takedown | post-MVP | community (deferred, post-MVP) | | planned |
| C-007 | Sybil/flood/defamation detection | post-MVP | community (deferred, post-MVP) | | planned |
| C-008 | No downloadable target lists | post-MVP | community (deferred, post-MVP) | | planned |
| C-009 | Classification may raise screening only, never dialing | post-MVP | community (deferred, post-MVP) | | planned |
| C-010 | Human review for published attribution | post-MVP | community (deferred, post-MVP) | | planned |
