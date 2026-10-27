# Requirements Traceability

Maps PRD requirement IDs to code and tests. Status reflects the code in `apps/api/src/callshield` as of 2026-10-27, not intent. Source: PRD 5.1-5.4, 19.2, and [CHANGE_callback_flow.md](../prd/CHANGE_callback_flow.md).

**Summary (85 rows):** 52 implemented, 18 partial, 1 removed, 14 not-started.

Status values: `implemented` (code and tests exist), `partial` (exists with the stated limit), `planned`, `removed` (replaced by the callback flow), `not-started`.

Limits that apply to every row: telephony is mock only; tests run on SQLite; Postgres RLS is checked only by the `api-postgres` CI job (`scripts/verify_postgres_rls.py`), which has not run yet; Docker and Asterisk are not independently verified. Module paths are under `apps/api/src/callshield/`, tests under `apps/api/tests/`. Dashboard coverage is not tracked per row.

Note: PRD requirement IDs `D-001..D-012` (Defend) are unrelated to decision-log IDs `D-001..D-008` ([decision-log.md](decision-log.md)). `CB-*` rows come from the callback spec and have no PRD ID.

| ID | Requirement | Priority | Module | Test | Status | Notes |
|---|---|---|---|---|---|---|
| D-001 | Enroll and verify line ownership | MVP | defend/lines.py, defend/verification.py, api/routers/lines.py | integration/test_defend_api.py | partial | code-based verification only; no real SMS/voice challenge provider |
| D-002 | Trusted contacts and overrides | MVP | policy/routing.py, api/routers/contacts.py, api/routers/rules.py | unit/test_defend_policy.py, integration/test_defend_api.py | implemented |  |
| D-003 | Deterministic routing before any LLM | MVP | policy/routing.py, screening/decision.py | unit/test_defend_policy.py, unit/test_defend_screening.py | implemented |  |
| D-004 | Screening agent only in authorized configs | MVP | policy/processing.py, defend/consent.py | unit/test_defend_policy.py, integration/test_defend_screening_flow.py | implemented | flags off by default |
| D-005 | Caller name/purpose captured as claims | MVP | defend/screening_flow.py, screening/extract.py | integration/test_defend_screening_flow.py | implemented |  |
| D-006 | Concise screening question and safe transfer | MVP | defend/screening_flow.py, screening/templates.py, telephony/mock.py | integration/test_defend_screening_flow.py, unit/test_defend_telephony.py | partial | mock telephony only; real transfer unverified |
| D-007 | Voicemail, unknown disposition, safe timeout | MVP | defend/screening_flow.py (handle_screening_failure), policy/routing.py | integration/test_defend_screening_flow.py | partial | mock telephony only |
| D-008 | Deliver configured removal request on live sales call | MVP | screening/decision.py, screening/templates.py | unit/test_defend_screening.py | partial | fixed-template decision logic exists; no live-call delivery path |
| D-009 | Respect monitoring/recording/transcript limits | MVP | policy/jurisdiction.py, policy/processing.py, defend/consent.py | unit/test_defend_policy.py | implemented | legal matrix is a draft pending counsel |
| D-010 | One-click disable of screening | MVP | defend/lines.py, api/routers/lines.py (screening:disable) | integration/test_defend_api.py | implemented |  |
| D-011 | Structured incident record for each action | MVP | ledger/service.py, audit/service.py, defend/calls.py | integration/test_ledger_audit.py | implemented |  |
| D-012 | Urgent-call labels with conservative handoff | MVP | policy/urgency.py, screening/classifier.py | unit/test_defend_policy.py, unit/test_defend_screening.py | implemented |  |
| R-001 | Case creation | MVP | resolve/cases.py, api/routers/resolution_cases.py | integration/test_resolve_cases.py | implemented |  |
| R-002 | Destination provenance; caller-ID-only blocked | MVP | resolve/policy.py, resolve/authorizations.py | unit/test_resolve_policy.py, integration/test_resolve_cases.py | partial | library only; the automated dial it gated was removed (CHANGE_callback_flow); callbacks use callback/gates.py |
| R-003 | Explicit user initiation; no target lists/auto-dial | MVP | callback/authorization.py, callback/gates.py (user authorization gate) | unit/test_callback_gates.py, integration/test_callback_flow.py | implemented | reworked: single-use 30 min authorization per callback; start-contact returns 410 |
| R-004 | IVR navigator | MVP | ivr/navigator.py, ivr/parser.py (library); callback DTMF-only handling | unit/test_resolve_ivr.py, integration/test_callback_acceptance.py | partial | reworked: DTMF only, speech-only menus end the call; no real trunk |
| R-005 | Queue and transfer management | MVP | resolve/dialer.py (library), callback/dialer_service.py | resolve_legacy.py, integration/test_callback_flow.py | partial | queue and caps exist; no live transfer |
| R-006 | AI compliance conversation (states automated) | MVP | callback/dialogue.py, callback/disclosure.py; agent/ retained as library | unit/test_callback_session.py, integration/test_callback_acceptance.py | partial | reworked: callback disclosure replaces opt-out script; mock telephony only; counsel gate G3 open |
| R-007 | Outcome typing | MVP | resolve/outcomes.py, callback/results.py | unit/test_resolve_agent.py, integration/test_callback_flow.py | implemented |  |
| R-008 | Persistent case timeline / attempt records | MVP | resolve/attempts.py, resolve/events.py, casebuilder/timeline.py | integration/test_resolve_flow.py, integration/test_enforce_pipeline.py | implemented |  |
| R-009 | Reviewable further contact; attempt limits and cooldowns | MVP | callback/caps.py, resolve/next_steps.py | unit/test_callback_gates.py, integration/test_resolve_flow.py | implemented | reworked as callback caps (2/inbound, 2/number/30d, 5/user/day, 3/number/30d instance) |
| R-010 | Resolution evidence: acknowledgment vs cessation | MVP | resolve/outcomes.py, resolve/machine.py | unit/test_resolve_agent.py, integration/test_resolve_safety.py | implemented | ACKNOWLEDGED never equals suppression |
| R-011 | Privacy-safe presentation | MVP | core/redaction.py, api/routers/ | integration/test_resolve_safety.py, unit/test_core.py | partial | redaction helpers and response shaping; no end-to-end privacy audit |
| R-012 | Human-first alternative | MVP | resolve/next_steps.py; dashboard case pages | integration/test_resolve_flow.py | partial | manual attempt logging and next steps; no live human transfer |
| R-013 | Stop conditions | MVP | resolve/stop.py, callback/hardstop.py, callback/blocklist.py | unit/test_callback_hardstop_locality.py, integration/test_resolve_safety.py | implemented |  |
| R-014 | Compliance-review parking; no LLM legal interpretation | MVP | resolve/next_steps.py, assessment/safety.py | integration/test_resolve_safety.py, integration/test_filing_assessments.py | partial | assessments never state violations as established; parking is a status with no reviewer UI |
| E-001 | Append provider-observed call events | MVP | ledger/service.py, defend/ingest.py, api/routers/webhooks.py | integration/test_defend_webhooks.py, integration/test_ledger_audit.py | implemented |  |
| E-002 | Provenance labels OBSERVED/CLAIMED/MODEL_INFERRED/USER_ATTESTED/EXTERNALLY_VERIFIED | MVP | domain/enums.py, evidence/derive.py | integration/test_enforce_evidence.py | implemented |  |
| E-003 | Retain authorized audio/transcripts per consent and retention | MVP | evidence/store.py, evidence/retention.py, callback/retention.py | integration/test_enforce_evidence.py, unit/test_callback_session.py | partial | retention sweeps and holds exist; recording off by default and unexercised on real calls |
| E-004 | Integrity hashes, custody changes, redactions | MVP | evidence/integrity.py, evidence/access.py | integration/test_enforce_evidence.py | implemented |  |
| E-005 | Prepare reviewable FTC/FCC/carrier submissions | MVP | filing/drafts.py, filing/templates.py, filing/approvals.py | integration/test_filing_flow.py | implemented | drafts only; user submits |
| E-006 | State legal uncertainty; never mark violations as established | MVP | assessment/engine.py, assessment/safety.py | integration/test_filing_assessments.py | implemented |  |
| E-007 | Offline PDF/JSON/CSV exports with versions | MVP | casebuilder/export.py, casebuilder/export_bundle.py | integration/test_enforce_export.py | partial | JSON/CSV zip bundle with manifest; PDF rendering is separately configurable and unverified |
| E-008 | Corrections, deletion requests, amendment audit | MVP | evidence/rights.py, casebuilder/lifecycle.py, audit/service.py | integration/test_enforce_api.py, integration/test_enforce_evidence.py | implemented |  |
| E-009 | Reference official reporting paths; no mass reports | MVP | filing/destinations.py, filing/handoff.py | integration/test_filing_submission.py | implemented |  |
| E-010 | Transfer case to user's attorney | MVP | casebuilder/export_bundle.py | integration/test_enforce_export.py | partial | export bundle only; no dedicated sharing flow |
| ENF-001 | Durable call-event ingestion | P0 | defend/ingest.py, ledger/service.py, worker/queue.py | integration/test_defend_webhooks.py | implemented |  |
| ENF-002 | Evidence provenance | P0 | domain/enums.py, evidence/derive.py | integration/test_enforce_evidence.py | implemented |  |
| ENF-003 | Automatic case creation | P0 | casebuilder/pipeline.py, worker/handlers/ | integration/test_enforce_pipeline.py | implemented |  |
| ENF-004 | Case history timeline | P0 | casebuilder/timeline.py; dashboard Timeline component | integration/test_enforce_api.py | implemented |  |
| ENF-005 | Artifact inventory | P0 | evidence/store.py, api/routers/evidence.py | integration/test_enforce_evidence.py | implemented |  |
| ENF-006 | Attribution gate | P0 | casebuilder/attribution.py | integration/test_enforce_pipeline.py | implemented |  |
| ENF-007 | User correction | P0 | casebuilder/lifecycle.py, audit/service.py | integration/test_enforce_api.py | implemented |  |
| ENF-008 | No-recording fallback | P0 | policy/processing.py, ledger/service.py | integration/test_enforce_pipeline.py | implemented |  |
| ENF-009 | Case export with manifest | P0 | casebuilder/export_bundle.py, casebuilder/snapshot.py | integration/test_enforce_export.py | implemented |  |
| ENF-010 | Evidence authorization and retention | P0 | auth/scoping.py, evidence/access.py, evidence/retention.py | integration/test_auth_and_isolation.py, integration/test_enforce_evidence.py | partial | app-level tenant scoping tested on SQLite; Postgres RLS checked only by the api-postgres CI job, which has not run yet |
| ENF-011 | Eligibility engine | P1 | assessment/engine.py, assessment/rulesets/enf_2026_10_v1.py | integration/test_filing_assessments.py | implemented | ruleset needs counsel review |
| ENF-012 | Evidence completeness checklist | P1 | casebuilder/sufficiency.py | integration/test_enforce_pipeline.py | implemented |  |
| ENF-013 | Complaint templates | P1 | filing/templates.py | integration/test_filing_flow.py | implemented |  |
| ENF-014 | Attestation and approval | P1 | filing/attestation.py, filing/approvals.py | integration/test_filing_flow.py | implemented | approval bound to payload digest |
| ENF-015 | Filing adapter registry | P1 | filing/adapters.py | unit/test_filing_unit.py | partial | registry plus manual-handoff adapter only; no documented-API adapter registered |
| ENF-016 | Manual handoff | P1 | filing/handoff.py | integration/test_filing_submission.py | implemented |  |
| ENF-017 | Receipt tracking | P1 | filing/submission.py, filing/lifecycle.py | integration/test_filing_submission.py | implemented | manual receipts; UNKNOWN stays UNKNOWN |
| ENF-018 | Dedupe and update | P1 | filing/snapshot.py, filing/lifecycle.py | integration/test_filing_snapshot.py | implemented |  |
| ENF-019 | Legal-review flagging | P1 | assessment/service.py | integration/test_filing_assessments.py | partial | flag exists; no reviewer workflow |
| ENF-020 | Rights and retention | P1 | evidence/rights.py, evidence/retention.py | integration/test_enforce_evidence.py | implemented |  |
| ENF-021 | External counsel sharing | P2 | none |  | not-started |  |
| ENF-022 | Network evidence corroboration | P2 | none (community deferred) |  | not-started |  |
| ENF-023 | Automated submission (future verified channels) | P2 | filing/adapters.py (register_documented_adapter hook only) | unit/test_filing_unit.py | not-started | hook exists; no adapter; agency submission flag off |
| C-001 | Disabled by default; informed opt-in | post-MVP | none (db/models/community.py tables only) |  | not-started |  |
| C-002 | Minimized indicators only | post-MVP | none (db/models/community.py tables only) |  | not-started |  |
| C-003 | Minimum group size; reidentification tests | post-MVP | none (db/models/community.py tables only) |  | not-started |  |
| C-004 | No unsalted phone hashes as anonymous IDs | post-MVP | none (db/models/community.py tables only) |  | not-started |  |
| C-005 | Multi-feature clustering | post-MVP | none (db/models/community.py tables only) |  | not-started |  |
| C-006 | Contributor correction/takedown | post-MVP | none (db/models/community.py tables only) |  | not-started |  |
| C-007 | Sybil/flood/defamation detection | post-MVP | none (db/models/community.py tables only) |  | not-started |  |
| C-008 | No downloadable target lists | post-MVP | none (db/models/community.py tables only) |  | not-started |  |
| C-009 | Classification may raise screening only, never dialing | post-MVP | none (db/models/community.py tables only) |  | not-started |  |
| C-010 | Human review for published attribution | post-MVP | none (db/models/community.py tables only) |  | not-started |  |
| OPTOUT-CALL | Outbound AI opt-out call to a researched company contact (PRD R-003/R-004 original form) | MVP | resolve/ library retained; start-contact and contact-authorizations return 410 OUTBOUND_OPT_OUT_REMOVED | integration/test_callback_resolve_retired.py | removed | ADR 0006 |
| CB-G1 | Gate: inbound record exists (no free-text numbers) | callback | callback/gates.py, callback/lines.py | unit/test_callback_gates.py, integration/test_callback_acceptance.py | implemented |  |
| CB-G2 | Gate: STIR/SHAKEN attestation A; missing counts as fail | callback | callback/gates.py, telephony/attestation.py | unit/test_callback_gates.py, unit/test_defend_attestation.py | implemented |  |
| CB-G3 | Gate: within callback_window_hours (default 72, max 168) | callback | callback/gates.py | unit/test_callback_gates.py | implemented |  |
| CB-G4 | Gate: explicit single-use user authorization, 30 min expiry | callback | callback/authorization.py, callback/gates.py | unit/test_callback_gates.py, integration/test_callback_flow.py | implemented |  |
| CB-G5 | Gate: number not on per-user or instance blocklist | callback | callback/blocklist.py, callback/gates.py | unit/test_callback_gates.py | implemented |  |
| CB-G6 | Gate: caps (2/inbound, 2/number/30d, 5/user/day, 3/number/30d instance, 10 min spacing) | callback | callback/caps.py | unit/test_callback_gates.py, integration/test_callback_acceptance.py | implemented | enforced in dialer service and re-checked before dial |
| CB-G7 | Gate: calling hours 8:00-21:00 local (user and destination) | callback | callback/timeutil.py, callback/locality.py | unit/test_callback_hardstop_locality.py | implemented |  |
| CB-CFG | Config defaults and locked ceilings; callback_enabled=false | callback | callback/config.py | unit/test_callback_config.py | implemented | CALLSHIELD_CALLBACK_* env vars |
| CB-LISTEN | Listen window; disclosure as first utterance; fixed template | callback | callback/session.py, callback/disclosure.py | unit/test_callback_session.py | implemented | counsel gate G3 open; listen_window_seconds=0 fallback supported |
| CB-HARDSTOP | Hard stops (wrong party, stop calling) and instance blocklist | callback | callback/hardstop.py, callback/blocklist.py | unit/test_callback_hardstop_locality.py | implemented |  |
| CB-DATA | No pre-disclosure persistence; claimed/observed labels; local-only processing | callback | callback/storage.py, callback/extraction.py, callback/retention.py | unit/test_callback_session.py, integration/test_callback_acceptance.py | implemented | egress check runs against the mock; not a real network audit |
| CB-BIO | No voice biometrics anywhere | callback | build-wide | unit/test_callback_no_biometrics.py | implemented |  |
| CB-CALLERID | Caller ID is the user's verified number only; no override path | callback | callback/dialer_service.py, callback/lines.py | integration/test_callback_acceptance.py | implemented |  |
| CB-TEL | Real telephony dialer behind the dialer port | callback | callback/dialer_port.py, telephony/mock.py | integration/test_callback_flow.py | partial | mock dialer only; no real provider adapter |
| CB-REL | Counsel release gates G1-G5 cleared | callback | docs/legal/release-gates.md |  | not-started | legal gates, not code; callbacks stay off until cleared |
