# CALLSHIELD NETWORK
## Final Development Product Requirements Document — v3.0
**Date:** October 27, 2026  
**Status:** Development kickoff approved for local/synthetic prototyping; public telephony and filing require explicit legal and technical gates  
**Working name:** CallShield Network (not cleared for trademark use)  
**Geographic scope:** U.S.-first, no assumption of nationwide regulatory uniformity  
**License recommendation:** AGPL-3.0-only for core software, subject to dependency and counsel review  
**Owner:** Project maintainers (legal entity to be determined)

> **Important:** The legal text in Appendix A–E is drafting material, not a legal opinion, legal certification, or assurance of immunity. A qualified U.S. telecom/privacy/open-source attorney must review it and deployment specifics before launch. No license or disclaimer can eliminate all developer, distributor, or operator exposure.

---

# 1. Executive summary

CallShield Network is a self-hostable, open-source system for defending against persistent unwanted telephone solicitations, requesting removal from calling lists, and gathering evidence of repeated contact. Its three pillars are **Defend**, **Resolve**, and **Enforce**.

- **Defend:** Intercept or screen authorized inbound calls, identify probable unwanted solicitation, preserve wanted calls, and document the outcomes.
- **Resolve:** Let the subscriber open a case and make a user-initiated, bounded, purpose-specific telephone contact to a verified business endpoint; support AI-assisted IVR/DTMF navigation, human-agent conversation, explicit opt-out requests, outcome classification, and later reviewable follow-up when warranted.
- **Enforce:** Construct a reliable evidence trail, prepare FTC/FCC/carrier complaints, and—after the core experience works—share minimized opt-in intelligence about apparent campaigns with safeguards against false attribution.

**Originating problem:** The user described hundreds of unwanted credit-loan solicitations per week. To secure removal, the user had to call the firm's system 11 times, select menu option 2 to reach an agent, and request removal repeatedly until the 11th contact appeared to process the request. The product must support persistent *case resolution*, rather than treating one unsuccessful telephone call as final. The eleven contacts are a user-provided experience, **not** a policy-recommended number or a default dialing program.

**Central boundary:** Documented, individually authorized follow-up for a legitimate opt-out purpose is in scope; repeated calling intended to overwhelm, intimidate, or harass is not. The product will not create distributed call floods, indiscriminate bulk dialers, or automatic rapid-redial loops. User ownership of a SIP trunk and open-source disclaimers do not turn otherwise unlawful conduct into lawful conduct.

# 2. Why this product exists

## 2.1 User problems

1. Unknown or spoofed calls interrupt ordinary life.
2. Blocking an individual displayed number is inadequate when caller IDs rotate.
3. The actual seller or telemarketer is often hidden behind scripts, call centers, brands, and IVR menus.
4. Opt-out requests are often difficult to deliver and harder to prove.
5. Users lack a unified trail of calls, requests, acknowledgments, and subsequent contact.
6. Existing reports rarely link a campaign across multiple affected people without exposing personal information.
7. Consumer complaints cost time even when the problem began with the caller's noncompliant behavior.

## 2.2 Product hypothesis

An AI call-screening and case-resolution assistant can decrease interruption costs, reduce repetitive menu navigation, and improve the quality of records that support subsequent carrier and regulator escalation. Community signals might improve defenses further, but only once local evidence quality and privacy are validated.

## 2.3 Value proposition

**For consumers:** Get interrupted less, resolve persistent solicitation more efficiently, and preserve understandable records.  
**For self-hosters:** Operate privately with SIP, open models, and infrastructure they control.  
**For consumer advocates:** Access carefully anonymized trends and defensible incident packages, not a vigilante target directory.  
**For businesses:** Protect legitimate work numbers without accidentally blocking wanted customers.

# 3. Goals, non-goals, and success criteria

## 3.1 Product goals

G1. Reduce unwanted interruptions per protected number.  
G2. Keep false-positive blocking of important wanted calls extremely low.  
G3. Increase the proportion of legitimate opt-out requests communicated and correctly documented.  
G4. Reduce human time navigating IVRs and repeated unproductive conversations.  
G5. Correlate possible repeat contact without misidentifying innocent number owners.  
G6. Enable legally supportable evidence exports and user-reviewed complaints.  
G7. Work on a modest self-hosted server with hosted inference optional.  
G8. Publish a reproducible and maintainable open-source distribution.

## 3.2 Non-goals

N1. Automatic rapid redial until a person answers.  
N2. Multi-user coordinated retaliatory calling.  
N3. Public lists of numbers for retaliation.  
N4. Caller ID spoofing, telecom-rule evasion, or provider throttling bypass.  
N5. AI threatening agents, falsely impersonating regulators, or claiming guaranteed legal damages.  
N6. Auto-submission of bulk complaints.  
N7. Universal interception of existing mobile telephone numbers irrespective of carrier support.  
N8. Unconsented recording or live interception.  
N9. Claiming that a business is definitively responsible for calls based only on displayed caller ID.

## 3.3 North-star and guardrails

**North star:** Verified unwanted user interruptions avoided per active line per month.  
**Resolution outcome:** Cases with verified acknowledgement plus a monitored absence or reduction of attributable later unwanted contacts; report acknowledgment and observed cessation separately.  
**Guardrails:** Legitimate calls missed, incorrect entity associations, unauthorized capture, attempted outbound harassment, per-minute cost, and unexpected call-routing failures.

# 4. User personas and roles

- **Consumer subscriber:** Owns or controls protected numbers; starts cases, chooses screening, approves calls and exports.
- **Power user/self-host operator:** Configures SIP, local AI, infrastructure, retention and jurisdiction rules.
- **Household/team administrator:** Manages allowed members, protected lines, routing, and access restrictions.
- **Reviewer/moderator (network beta):** Reviews false reports, clusters, appeals and sensitive material.
- **Maintainer:** Publishes code and documentation, triages security and reliability issues; must not be confused with the operator of a third-party deployment.
- **Hosted service operator (optional):** Separately responsible for its own service operations, carrier contracts, privacy obligations, abuse handling and disclosures.

# 5. Product modules

## 5.1 DEFEND — Inbound call protection [MVP]

**D-001:** Enroll and verify user control over an eligible number or SIP endpoint before routing.  
**D-002:** Maintain trusted contacts, permitted callers, unknowns, known unwanted contacts and overrides.  
**D-003:** Apply deterministic inbound routing rules before invoking any LLM.  
**D-004:** Answer unknown inbound calls with the automated screening agent only in supported and authorized configurations.  
**D-005:** Prompt for caller name and purpose; mark them as *claims*, not proven identities.  
**D-006:** Ask a concise screening question and provide safe transfer to the user.  
**D-007:** Support voicemail, unknown caller disposition and safe timeout.  
**D-008:** Deliver user's configured removal request on an appropriate live unsolicited sales call.  
**D-009:** Respect legal restrictions governing monitoring, live speech processing, recordings and stored transcripts.  
**D-010:** Provide one-click disable of screening and restore preconfigured ordinary routing.  
**D-011:** Track each action, routing rule and failure path in a structured incident record.  
**D-012:** Provide detection labels for potentially urgent medical, school, family and emergency calls, with conservative human handoff.

**Acceptance criteria:** A trusted caller reaches the user; unknown caller is screened; malformed provider webhook cannot route across tenants; loss of AI service causes configured fallback rather than dropped calls; screening can be disabled immediately.

## 5.2 RESOLVE — Opt-out case management [MVP]

The product's distinguishing feature is a *resolution case*, not simply an outbound telephone session.

**R-001: Case creation.** A subscriber opens a case from one or more call records or a manually described problem. Fields: owned line, alleged business, incident summary, objective, evidence, urgency, and legal policy region.

**R-002: Destination provenance.** Every proposed outbound endpoint is labeled as: independently verified company contact, user-provided but unverified, or caller-ID-only. Caller-ID-only endpoints cannot be used for automated outbound resolution. Present evidence supporting verified contacts (official business web page, prior authenticated correspondence, or other corroborated source).

**R-003: Explicit initiation.** User selects a verified destination and deliberately starts an outbound contact. No public or shared user target list, and no arbitrary scheduled auto-dial wave.

**R-004: IVR navigator.** Detect menu prompts, show possible options, and send DTMF on user-confirmed or previously user-approved menu paths. Example: select option 2 for an agent. Maintain IVR version, confidence, last validation, timeout and fallback.

**R-005: Queue and transfer management.** Detect hold state, agent connection and disconnection; permit bridging the user or authorized AI agent; do not keep abandoned calls active indefinitely.

**R-006: AI compliance conversation.** The agent states that it is automated; identifies the purpose, requests do-not-call suppression of the user's authorized telephone line, asks if the request was received, and avoids disclosure of unnecessary personal information.

**R-007: Outcome typing.** `NO_ANSWER`, `IVR_FAILED`, `DROPPED`, `WRONG_DESTINATION`, `AGENT_CONNECTED`, `REQUEST_DELIVERED`, `ACKNOWLEDGED`, `REFUSED`, `REFERRED_TO_CHANNEL`, `UNRESOLVED`, `VERIFIED_RESOLVED`, `USER_ABORTED`.

**R-008: Persistent case timeline.** Append an attempt record with verified dialed destination, user authorization, reason for attempt, start/end, IVR route, agent claims, policy state and outcome.

**R-009: Reviewable further contact.** An unresolved case can generate a recommended next step—verified compliance email, mail, carrier report, supervised additional call, or regulatory complaint—based on why prior contact failed. The platform must require independent user action for additional outbound calls and apply conservative attempt restrictions and cooldowns. No rapid loops, no dialing simply to create nuisance, and no circumvention via multiple accounts or telephony providers.

**R-010: Resolution evidence.** Differentiate acknowledgment from observed cessation; later inbound calls can reopen a case for review when credible attribution exists.

**R-011: Privacy-safe presentation.** Do not publicly expose complainant's own number or personally identifying information within shared case data.

**R-012: Human-first alternative.** The user may perform the entire call manually using the same call interface and ledger. AI is optional and must not become a legal or technical prerequisite.

**R-013: Stop conditions.** Stop automated agent activity on explicit counterparty refusal to speak with a bot, request to terminate, unexpected sensitive information, policy uncertainty, unsafe destination changes, consent failure or user cancellation. Mark the case and offer non-calling alternatives.

**R-014: Independent compliance-review status.** Where the agent hears conflicting instructions or rules, park the workflow for user review; do not rely on the LLM to interpret laws autonomously.

**Acceptance tests:** Navigate a controlled test IVR using option 2, reach an agent, deliver a removal request, store accurately what was said, and maintain a case as unresolved if no confirmation was obtained. Demonstrate prevention of unsupervised rapid redials after repeated failure. Demonstrate correction when the alleged company is unverified.

### 5.2.1 Detailed resolution state machine

`CREATED -> DESTINATION_REVIEW -> VERIFIED_DESTINATION -> USER_APPROVED_CONTACT -> IN_PROGRESS -> REQUEST_DELIVERED -> {ACKNOWLEDGED, UNCONFIRMED, REFUSED, INTERRUPTED} -> {MONITORING, NEXT_STEP_REVIEW, CLOSED}`

Subsequent new call: `MONITORING -> POSSIBLE_RECONTACT -> ATTRIBUTION_REVIEW -> {REOPENED, NOT_RELATED}`.

No transition directly equates `ACKNOWLEDGED` with `SUPPRESSED_IN_SENDER_SYSTEM`. Only evidence of actual cessation or independent proof can support the stronger status, with relevant limitations stated.

### 5.2.2 Example: credit loan firm

1. Subscriber reports repeated loan solicitation.
2. System groups their inbound records but does not assume all calls share one operator.
3. Subscriber independently establishes an official resolution number.
4. User starts a purpose-specific contact; IVR navigator recognizes `Press 2` and selects it within approved scope.
5. AI or subscriber asks for entity-specific do-not-call suppression.
6. If transferred, disconnected, or refused, mark exact reason; provide next-step options.
7. If caller confirms removal, save acknowledgement status; monitor later inbound incidents.
8. If unwanted calls continue from a credibly related organization, reopen and prepare additional documentation or escalation.

The experience of eleven personal attempts is important evidence of user friction, but it is **not** an instruction to automate eleven back-to-back calls.

## 5.3 ENFORCE — Evidence and complaint workflows [MVP exports; network later]

**E-001:** Append provider-observed call events with timestamps, ingest source, provider reference, and idempotency keys.  
**E-002:** Label information `OBSERVED`, `CLAIMED`, `MODEL_INFERRED`, `USER_ATTESTED` or `EXTERNALLY_VERIFIED`.  
**E-003:** Retain authorized audio/transcript artifacts according to explicit consent and retention policy.  
**E-004:** Record evidence integrity hashes, custody changes and redactions; do not promise that hashes alone ensure admissibility.  
**E-005:** Prepare chronological, redacted, user-reviewable FTC, FCC and carrier submissions.  
**E-006:** State legal uncertainty and possible exemptions; never mark potential legal violations as judicially established.  
**E-007:** Allow offline PDF/JSON/CSV exports (formats optional by release); track generated export versions.  
**E-008:** Support user corrections, deletion requests and audit trail of amendments.  
**E-009:** Provide reliable references to official reporting paths rather than mass-report generation.  
**E-010:** Allow a case to be transferred to a user's own attorney without granting open access to community contributors.

## 5.4 COMMUNITY — Privacy-preserving campaign signals [post-MVP]

**C-001:** Disabled by default and enabled only through informed opt-in.  
**C-002:** Send minimized indicators, not raw recordings or complete phone logs.  
**C-003:** Apply minimum group size before public aggregate displays; test reidentification risks.  
**C-004:** Do not treat unsalted telephone hashes as anonymous identifiers.  
**C-005:** Use several independent features when clustering, including script, claims, timing and trusted metadata.  
**C-006:** Permit contributor correction, takedown requests, and abuse investigations.  
**C-007:** Identify malicious report floods, Sybil attacks and defamation attempts.  
**C-008:** Disallow public downloadable lists marketed as calling targets.  
**C-009:** A network classification may suggest heightened *screening*, never authorize retaliatory dialing.  
**C-010:** Publishing confirmed attribution requires human review and supporting evidence.

# 6. Core user stories

**US-01:** As a subscriber I can connect an authorized phone endpoint and test safe fallback, so I don't lose real calls.  
**US-02:** As a subscriber I can send unknown inbound calls to an AI screener, so I am interrupted less.  
**US-03:** As a subscriber I can receive a transcript or structured event only when lawfully captured, so I can review the call.  
**US-04:** As a subscriber I can create a removal case from persistent calls, so I can track the actual problem.  
**US-05:** As a subscriber I can start a contact to a verified company's official channel, so I avoid calling spoofed number owners.  
**US-06:** As a subscriber I can approve menu choice 2 for a particular IVR, so the software reaches a person without repetitive navigation.  
**US-07:** As a subscriber I can let an AI speak a limited removal request or take over, so control remains mine.  
**US-08:** As a subscriber I can see that an agent acknowledged a request without mistaking this for true suppression, so my record is accurate.  
**US-09:** As a subscriber I can review an appropriate follow-up rather than being forced to stop after a failed call, so legitimate unresolved cases can continue.  
**US-10:** As a subscriber I can export a well-labeled evidence timeline for a complaint, so I do not start from scratch.  
**US-11:** As a self-hoster I can operate without a central intelligence cloud, so I retain control of my data.  
**US-12:** As an innocent number owner I can challenge a malicious attribution without receiving retaliatory calls from the network.

# 7. Technical architecture

## 7.1 Proposed technology stack

- Web dashboard: Next.js, TypeScript, accessible components.
- API/orchestration: FastAPI and Python; typed OpenAPI contracts, background job workers.
- Telephony: Asterisk with ARI and supported external media integration, plus provider adapter abstractions.
- SIP: User-owned or hosted SIP trunk; verify supported inbound and user-initiated outbound permissions with chosen provider.
- Voice: streaming STT, TTS, VAD and turn management; locally deployable engines as first-class integrations.
- LLM: pluggable local or remote models, bounded tools; never grant raw carrier-administration or unbounded dial privileges.
- Database: PostgreSQL, tenant-isolation policies, migration framework.
- Cache/worker: Redis plus controlled queues.
- Object storage: local encrypted filesystem or S3-compatible backend for authorized artifacts.
- Observability: OpenTelemetry, Prometheus-compatible metrics, structured redacted logs.
- Packaging: Docker Compose, pinned image digests, health checks, reproducible installation.
- Authentication: OIDC / local authentication through a vetted library; MFA support for administrative access.

**Architecture choice:** Modular monolith for R0/R1; do not prematurely split into 15 microservices.

## 7.2 Architectural flows

**Inbound**: `SIP/provider webhook -> verification -> routing policy -> AI or transfer -> disposition -> ledger -> optional evidence -> dashboard`.

**Outbound resolution**: `user starts authorized case -> verify destination/provenance -> jurisdiction & consent policy -> call via SIP -> bounded IVR/AI/human -> outcome -> evidence ledger -> case review`.

**Community**: `explicit opt-in -> sanitizer -> privacy validator -> abuse screening -> candidate campaign engine -> moderation -> aggregate defensive signals`.

## 7.3 Failure modes

- SIP registration failed: notify and restore safe fallback when technically possible.
- AI latency exceeded: transfer, play neutral fallback, or terminate per user configuration.
- Stream lost: never infer successful opt-out from an incomplete audio exchange.
- Provider webhook duplicate: idempotent event ingestion.
- Unknown recording legality: disable live AI media processing in that call configuration and offer non-processing routing.
- Missing verified outbound destination: block AI-initiated resolution contact; show manual review options.
- Credential compromise: revoke trunk access, rotate secrets and alert operator.
- Provider policy forbids automated calls: disable outbound AI module for that deployment.
- Community backend offline: local protection and case ledger keep working.

# 8. Key data schema

| Table | Required fields |
|---|---|
| tenants | id, hosting_mode, jurisdiction_policy_version |
| users | id, tenant_id, role, consent_preferences |
| protected_lines | id, tenant_id, verification_status, routing_config |
| call_sessions | id, tenant_id, line_id, provider_call_id, direction, start/end, disposition |
| caller_observations | id, session_id, displayed_caller_id, attestation_metadata, source |
| organization_claims | id, session_id, claimed_name, statement_type, verification_status |
| resolution_cases | id, tenant_id, protected_line_id, claimed_entity, objective, status, owner |
| contact_destinations | id, case_id, phone, source_url_or_evidence, verification_status, verified_at |
| contact_attempts | id, case_id, destination_id, user_authorization_id, reason, outcome, times |
| ivr_paths | id, destination_id, version, choices, last_tested_at, confidence |
| opt_out_events | id, case_id, attempt_id, request_text_version, status, acknowledged_at |
| classifications | id, call_id, model_version, label, confidence, reviewed_status |
| evidence_artifacts | id, case_id, media_type, storage_ref, integrity_digest, consent_ref, expiry |
| consent_records | id, session_id, processing_mode, legal_basis_config, authorized_at |
| audit_events | id, tenant_id, actor, action, time, immutable_ref |
| complaint_drafts | id, case_id, jurisdiction, draft_version, user_review_state |
| candidate_campaigns | id, features_version, confidence, review_status |
| campaign_links | id, incident_id, campaign_id, source, review_state |

**Data design constraints:** Tenant isolation everywhere; no raw user numbers in general logs; source provenance for evidence; model confidence separately from factual verification; version all algorithms and schemas; do not use deterministic public hashes of telephone numbers as privacy guarantees.

## 8.1 API surface

`POST /v1/lines`, `POST /v1/lines/{id}/verify`, `GET /v1/calls`, `GET /v1/calls/{id}`, `POST /v1/resolution-cases`, `GET /v1/resolution-cases/{id}`, `POST /v1/resolution-cases/{id}/destinations`, `POST /v1/resolution-cases/{id}/contact-authorizations`, `POST /v1/resolution-cases/{id}/start-contact`, `POST /v1/resolution-cases/{id}/attempts/{id}/review`, `POST /v1/resolution-cases/{id}/exports`, `GET /v1/evidence/{id}`, `PUT /v1/rules/{id}`, `GET /v1/audit-events`.

**Critical control:** `start-contact` requires ownership/auth, destination verification, explicit user action, current legal/provider policy approval, and a valid case; never accept a bare target number plus a retry count as sufficient authorization. All dial actions are rate-controlled, logged, and stoppable.

## 8.2 Event topics

`call.received`, `call.answered`, `call.media.started`, `call.transferred`, `call.ended`, `case.created`, `destination.verified`, `contact.authorized`, `contact.started`, `ivr.option.selected`, `request.delivered`, `request.acknowledged`, `contact.ended`, `case.reopened`, `evidence.created`, `complaint.drafted`, `artifact.expired`.

Each event includes origin, schema version, creation time, idempotency token and tenant scope.

# 9. AI agent and IVR specification

## 9.1 Agent rules

The AI must clearly identify itself where appropriate; disclose the authorized purpose; never pretend to be a human, lawyer, regulator or police; never demand sensitive information; never threaten, insult or harass; never modify telecom account settings; never originate additional calls autonomously; never promise an opt-out has succeeded based solely on a model prediction.

The call planner may propose a permissible **next step**, but a policy engine and user authorize every new outbound contact.

## 9.2 Tool schema (conceptual)

- `read_case_summary(case_id)` — minimum required fields.
- `get_verified_destination(destination_id)` — read-only provenance.
- `get_current_ivr_map(destination_id)` — advisory.
- `send_dtmf(session_id, digits)` — only on active authorized connection and approved path.
- `speak_opt_out(session_id, template_id)` — bounded approved phrase.
- `request_human_handoff(session_id)` — safe transfer.
- `record_disposition(session_id, code, notes)` — structured provisional notes.
- `end_session(session_id)` — bounded stop.

**Not available to the LLM:** arbitrary `dial(number)`, `schedule_bulk_calls`, `disable_audit`, `bypass_consent`, `change_trunk_limits`, or network-target broadcasting.

## 9.3 Conversation templates

**Inbound:** “I'm an automated screening assistant. Who is calling, and what is the purpose of your call?”

**Outbound:** “I'm an automated assistant calling on behalf of the person associated with this telephone number. They request that your organization stop solicitation calls to that number and add it to your internal do-not-call list. Can you confirm you've received the request?”

**If agent says call back:** “Is there a specific compliance channel where this request can be delivered in writing, or someone who can record it now?”

**If acknowledgment is unclear:** “I understand you may not be able to confirm processing today. I will record that the request was communicated, but confirmation is outstanding.”

**If person asks to end call:** “Understood. Goodbye.” Terminate.

## 9.4 IVR navigation

A stateful IVR navigator shall: detect speech/menu structure; show text for accessibility; propose DTMF action; support per-destination user-approved routes; handle repeated menus, queue announcements, transfers, no-answer, busy, rate limits, and operator changes. Do not dynamically probe large numbers of arbitrary extensions or initiate a repeat-dial sequence.

## 9.5 Performance targets (hypotheses pending measurements)

- Decision latency after media recognition: median <1.5 s, p95 <3 s.
- Intent classification precision on confidently identified solicitation: target >=95% validated on representative samples.
- Confirmed legitimate-call false blocking: target <0.5% in validation set, with zero tolerance for known severe priority-call failures.
- Unauthorized agent action: zero in mandatory security tests.
- Average processing cost: establish only after telephony+inference benchmarking.

# 10. UX information architecture

**Sidebar:** Overview, Calls, Resolve, Cases, Evidence, Complaints, Campaigns (later), Rules, Settings, System Health.

**Overview:** Incoming calls, screened calls, calls routed to human, avoided interruptions, unresolved cases, possible repeat contact, telephony status, estimated processing expenses.

**Calls:** Filters by date, class, direction, dispositions, claimed identity, user correction; details separate factual metadata and model conclusions.

**Resolve/Case detail:** Heading (claimed firm + confidence), purpose, authorized protected line, verified destination and source, **Contact now** action, optional AI/human toggle, IVR map (“Press 2”), timeline, request acknowledgment, next steps, associated inbound records and export button.

**Active call:** Clear elapsed duration, destination, displayed IVR path, agent connection, live permissible content, pause/terminate, handoff, recording/processing disclosure status.

**Evidence:** Artifact list, provenance, legal status, retention, export, delete, corrections.

**Settings:** SIP adapter, number ownership, contacts, inbound fallback, AI provider, recording/processing policies, retention, notification digest, budget caps, network opt-in.

**Access:** keyboard and screen-reader accessible, WCAG 2.2 AA target, readable confidence explanations, no gamified attack metrics such as “calls fired at target.”

# 11. Security, privacy and abuse controls

## 11.1 Threat model

- Caller-ID spoofing implicates innocent third parties.
- Callers may embed prompt injection in speech.
- Malicious subscribers may attempt to dial arbitrary or protected targets.
- Community contributors may submit fabricated incidents to smear businesses.
- Hosted operators may mishandle personal audio or call metadata.
- SIP credentials may be stolen and used for toll fraud.
- Multi-tenant access bugs may reveal sensitive phone numbers.
- Model hallucinations may overstate acknowledgment or criminal culpability.

## 11.2 Required controls

- Strict destination verification and independently reviewed exceptions.
- Rate restrictions, cooldowns, case-level authorization, finite session duration and transparent stop controls.
- Explicit counterparty stop and opt-out controls where applicable.
- Tenant isolation, encryption, secret storage, MFA for privileged users.
- Provider webhook signature validation and replay prevention.
- Redaction before logging; optional local-only AI for highly sensitive deployments.
- Auditable model tool invocations and deterministic policy enforcement.
- Private-by-default evidence and limited retention.
- Report quality controls, corrections, removals and appeals for shared campaigns.
- Incident response procedure and abuse reporting contact.

## 11.3 Recording and live processing

US recording/interception law differs across states, and a call may implicate multiple jurisdictions. A mere announcement does not necessarily make recording or live AI processing lawful. The legal gate must consider both live media processing and persistence. When authorization cannot be established, disable the relevant capability and fall back to transparent routing or human-only contact.

**Proposed configurable retention:** metadata 90 days, recordings 30 days, transcripts 30 days, opt-out cases one year by user choice, observability logs 14 days. These are design defaults, not legal advice or requirements; operators must adjust per region, evidence need and applicable laws. Support deletion and preservation holds where lawfully appropriate.

# 12. Regulatory analysis and implementation implications

**FTC TSR, 16 CFR Part 310:** Certain covered telemarketers must honor entity-specific do-not-call requests; the FTC describes practices that improperly obstruct requests. The rule does not automatically apply to every claimed lender, caller, organization or transaction. Product language must distinguish suspected violations from established findings. [FTC TSR guidance](https://www.ftc.gov/business-guidance/resources/complying-telemarketing-sales-rule).

**Federal telephone harassment statute, 47 USC §223:** Repeated ringing or calls with prohibited harassment intent can incur criminal exposure in covered circumstances. The fact that the project is open source or the recipient is a suspected scam firm does not automatically negate this exposure. [Statutory text](https://uscode.house.gov/us/usc/119-118/t47/s223/).

**FCC/TCPA and artificial voices:** FCC interpretation treats AI-generated voices as artificial/prerecorded voices for TCPA purposes; applicability to specific outbound contact types requires counsel and provider analysis. Do not assume a non-marketing purpose removes every applicable obligation. [FCC announcement](https://docs.fcc.gov/public/attachments/DOC-400393A1.pdf).

**Recording/interception:** Federal one-party rules do not displace stricter potentially applicable state laws. [RCFP recording guide](https://www.rcfp.org/introduction-to-reporters-recording-guide/).

**Official complaint routes:** The FTC directs unwanted-call reports to DoNotCall.gov and scam/fraud detail to ReportFraud.ftc.gov; the FCC gathers unwanted-call complaints for enforcement and policy but generally does not resolve the individual unwanted-call complaint. [FTC consumer guidance](https://consumer.ftc.gov/articles/national-do-not-call-registry-faqs); [FCC complaint handling](https://consumercomplaints.fcc.gov/hc/en-us/articles/202752940-How-the-FCC-Handles-Your-Complaint).

**License:** AGPL/GPL include warranty and liability language, but enforcement may vary by jurisdiction. Open Source Initiative criteria prevent restricting fields of use in an OSI-open-source license. A separate acceptable-use policy may govern maintainers' hosted services and network access but must not be presented as a restriction on the open-source license's grant. [GNU GPL terms](https://www.gnu.org/licenses/gpl-3.0.html); [OSI definition](https://opensource.org/osd).

# 13. Open-source packaging and governance

```
callshield/
  apps/dashboard/
  apps/api/
  apps/worker/
  packages/telephony-core/
  packages/ai-agent/
  packages/ivr-navigator/
  packages/policy-engine/
  packages/case-management/
  packages/evidence/
  packages/shared-schemas/
  integrations/asterisk/
  integrations/voice-providers/
  integrations/local-ai/
  infra/docker/
  infra/monitoring/
  tests/unit/
  tests/integration/
  tests/telephony/
  tests/legal-policy/
  tests/red-team/
  docs/deployment/
  docs/developer/
  docs/privacy/
  docs/legal/
  LICENSE
  README.md
  SECURITY.md
  PRIVACY.md
  ACCEPTABLE_USE.md
  DISCLAIMER.md
  CONTRIBUTING.md
  CODE_OF_CONDUCT.md
```

Use dependency SBOMs and license scanning; do not claim “100% anonymous” community submissions; publish security advisory and reproducible release processes. Governance should separate the public code repository from any hosted or federated service. The project must not suggest disclaimers absolve maintainers from their own misconduct, product safety choices, or unlawful operation.

# 14. Deployment modes and economics

| Mode | Benefits | Limitations |
|---|---|---|
| Fully local/self-host | Control of code and local metadata | SIP setup, legal operations, and compute remain user responsibility |
| Hybrid | Local telephony with optional hosted AI | Additional processor and network privacy responsibilities |
| Managed hosted (future) | Simplified onboarding | Carrier compliance, privacy, abuse and uptime obligations for operator |
| Federation (later) | Cross-user campaign detection | Reidentification, false attribution, moderation and governance risks |

Monthly cost = server/DB + inbound/outbound carrier minutes + per-call setup/number charges + AI STT/LLM/TTS + storage/egress + monitoring/support. Illustrative workload: 400 intercepted calls of one minute each means 400 screened minutes, *not* an estimated dollar cost. Outbound resolution calls incur additional carrier and AI expenses. Instrument per-session costs and cap monthly spending.

# 15. Development releases, dependencies and gates

**R0 — Test harness:** Asterisk SIP, sample provider, mock IVR with “press 2”, user-owned destination, media bridge, minimal agent; demonstrate clean termination and safe fallback.  
**R1 — Self-hosted Defend MVP:** Enrollment, inbound screening, trusted callers, deterministic policy, event ledger, safe disclosures, full test suite.  
**R1.5 — Resolve MVP:** Case creation, verified destination, user-initiated outbound contact, DTMF, AI or human conversation, acknowledgment tracking, limited reviewable follow-up.  
**R2 — Evidence and complaints:** Tamper-evident records, redacted exports, clear provenance, user-reviewed FTC/FCC/carrier complaint packages.  
**R3 — Network beta:** Opt-in privacy-preserving sharing, human moderation, campaign clustering, appeal/correction.  
**R4 — Hosted and ecosystem:** Hardened multi-tenancy, adapter SDK, enterprise controls, installation tooling, support.

Each phase depends on operational validation, not arbitrary calendar estimates.

## 15.1 MVP exit gates

- Proven line ownership and carrier-compatible routing.
- Authorized processing of media under jurisdictional policy.
- Inbound screening, user transfer and fallback tested.
- Controlled IVR/DTMF path can reach agent through option 2.
- User-initiated outbound agent clearly discloses role and request.
- Every contact associated with a real case, verified destination and recorded authorization.
- Further calls never form an unchecked redial sequence.
- Source provenance and user corrections implemented.
- High-impact hallucinations, spoofing and privacy failure tests passed.
- Clean Docker install by an independent engineer.
- Legal counsel clears deployed workflows and published agreements.

# 16. QA and acceptance test plan

**Telephony:** SIP timeout, 503 response, busy, answering machine, call waiting, transfer, voicemail, DTMF path drift, audio codec mismatch, lost media stream, concurrent sessions, event duplication, emergency fallback.

**AI:** Confusing accents, scripted loans, genuine bank call, medical office, prompt injection, human claiming to be regulator, fake agent authority, hallucinated acknowledgment, IVR ambiguity, interruptions and high noise.

**Case resolution:** Verified destination, rejected spoofed number, wrong official number, user intervention, acknowledgment vs actual cessation, multiple prior manual attempts, independent step review, refusal to speak to bot, non-calling alternatives.

**Security:** Cross-tenant API access, trunk credential theft, outbound fraud attempt, forged webhooks, poisoning of evidence, data deletion, report spam, excessive concurrent requests, federation linkage attacks.

**Privacy:** Processing without sufficient consent must not proceed, no raw media in telemetry, redaction, default community-sharing OFF, consent changes propagate, retention expiry actually deletes objects and indexes.

**Deployment:** Local model deployment, no proprietary mandatory dependencies, compose install, disaster recovery, reverse proxy/TLS, health probe, secret rotation, Linux compatibility.

# 17. Key performance measures

- Weekly unique protected lines and successful integrations.
- Inbound screening coverage and wanted-call false block rate.
- Time saved from IVR and conversations (methodology transparent).
- Resolution case creation and completion rate.
- Requests delivered; acknowledgement obtained; subsequent contact observed and attributable.
- Calls per resolution case (a diagnostic, **not** a gamified optimization target).
- Verified associations in campaign clusters.
- Evidence accuracy and export completion.
- Telephony uptime, AI p95 latency and average processing cost.
- Privacy incidents and reports of misuse.

# 18. Risks, decisions and unresolved questions

| Risk | Severity | Decision / action |
|---|---|---|
| Legal uncertainty over AI outbound processing and calls | Critical | Counsel review before enabling live outbound AI |
| Mobile forwarding capability varies by carrier | Critical | Start with proven SIP/test numbers; explicit compatibility matrix |
| Spoofed caller ID misdirects follow-up | Critical | Only verified destinations for automated resolution |
| Live ASR itself may implicate wiretap law | Critical | Processing gate, not just recording toggle |
| Wrongly screened urgent calls | High | Safe trusted contacts, overrides, conservative routing, audits |
| Coordinated network false attribution | High | Hold network until moderation and privacy validation |
| “Open source means no liability” misconception | High | Legal docs explicitly reject this claim |
| LLM fabricates acknowledgement or identity | High | Keep source types and human review separate |
| Carrier toll fraud | High | Locked-down outbound privileges and budgets |
| Provider bans bot dialing | High | Respect provider terms; human-only fallback |
| High inference expense | Medium | Bounded conversations and deterministic prefilters |
| Trademark conflict | Medium | Clear name before publication |

Open decisions requiring validation: exact launch jurisdiction policy, provider adapter selection, recording/processing modes, AGPL vs permissive license after counsel, community governance structure, direct hosted-service model, cross-provider portability, and whether a written notice workflow should take precedence after one failed call.

---

# 19. ENFORCE 2.0 — Automated Evidence, Formal Case Construction, and Filing [MVP core / submission phased]

**Normative status:** This section supersedes any earlier description that treats incident capture, case building, and complaint preparation as merely optional. This release requires automatic evidence-case creation; legally valid electronic filing remains contingent on a verified destination and express user approval.

## 19.1 Product commitments and boundaries

- Every protected inbound call generates an append-only operational event record, even if no conversation content may lawfully be retained.
- Every user-initiated outbound opt-out contact generates a linked attempt record. The system must *not* contact an unverified callback number merely because it appears in caller ID.
- Every eligible incident is automatically evaluated for existing cases, and either attached to a case or flagged as a candidate. Users can override incorrect links without deleting underlying original evidence.
- The system incrementally constructs a **case dossier**, not merely a PDF summary: chronology, opt-out history, follow-up incidents, verified and claimed operator identities, provenance, attachment inventory, statements, review history, filing attempts, agency receipts, and later supplements.
- Record what actually happened. Call metadata is not automatically evidence that a specific person or company placed the call. Recording and persistent speech transcription require a properly validated legal basis.
- The product can prepare complaints automatically. Actual transmission to an agency is permissible only through an approved technical integration or user-directed external submission and only after the user approves and attests to accuracy.
- No repetitive bulk or retaliatory calling feature is added by this requirement. Outbound contact remains bounded, individual-case-focused, and policy-gated.

## 19.2 Requirements register (normative)

| ID | Priority | Requirement | Definition of done |
|---|---|---|---|
| ENF-001 | P0 | Durable call-event ingestion | Authenticated, idempotent carrier events survive retries and restarts |
| ENF-002 | P0 | Evidence provenance | Original vs derived vs user-stated vs caller-claimed vs independently verified are visibly separate |
| ENF-003 | P0 | Automatic case creation | Configurable rules create candidate case on first unwanted call, link to protected line |
| ENF-004 | P0 | Case history | Display inbound/outbound attempts, opt-outs, acknowledgments, subsequent calls as event timeline |
| ENF-005 | P0 | Artifact inventory | Each permitted audio, transcript, screenshot, uploaded document has digest, authorization, retention, source |
| ENF-006 | P0 | Attribution gate | A spoofable displayed number alone cannot establish seller identity or verified repeat-contact violation |
| ENF-007 | P0 | User correction | Reclassify events and sever incorrect links without altering source events |
| ENF-008 | P0 | No-recording fallback | Maintain lawful signaling metadata when live speech capture/retention is not authorized |
| ENF-009 | P0 | Case export | Produce human-readable dossier plus manifest and machine-readable JSON with original hashes |
| ENF-010 | P0 | Evidence authorization | Prevent unauthorized sharing, deletion bypass, cross-tenant access, and over-retention |
| ENF-011 | P1 | Eligibility engine | Rule-set determines possible complaint types and highlights prerequisites, exclusions, unknowns |
| ENF-012 | P1 | Evidence completeness | Weighted checklist with explicit missing sources, not a bogus legal-validity percentage |
| ENF-013 | P1 | Complaint templates | FCC unwanted-call, FTC fraud/Do Not Call as appropriate, telecom carrier, state AG (where validated) |
| ENF-014 | P1 | Attestation and approval | Require user to review actual contents and separately authorize each submission |
| ENF-015 | P1 | Filing adapter registry | Only documented, authorized provider methods; no CAPTCHA bypass or simulated agency API |
| ENF-016 | P1 | Manual handoff | Pre-filled copy/export + verified official destination when no supported programmatic submission exists |
| ENF-017 | P1 | Receipt tracking | Save official confirmation number, submission time, endpoint, response body sanitized, payload digest |
| ENF-018 | P1 | Dedupe and update | Prevent duplicate submissions; offer supplemental case updates after material new evidence |
| ENF-019 | P1 | Legal-review flagging | Potential TCPA/TSR and state-law issue spotting, never legal determination or damages guarantee |
| ENF-020 | P1 | Rights and retention | Data subject export/deletion with legal hold and explicit disclosure of deletion limits |
| ENF-021 | P2 | External counsel sharing | User-controlled expiring grant with redaction, logs, and revocation |
| ENF-022 | P2 | Network evidence corroboration | Consent-based minimized reports; do not merge unrelated identities from matching caller IDs |
| ENF-023 | P2 | Automated submission | Allowed solely for future verified submission channels with per-filing authorization and anti-abuse controls |

## 19.3 Formal case lifecycle

`OPENED -> INTAKE -> EVIDENCE_COLLECTING -> REVIEW_REQUIRED -> ELIGIBLE_TO_DRAFT -> DRAFT_READY -> USER_APPROVED -> SUBMISSION_IN_PROGRESS -> SUBMITTED / SUBMISSION_FAILED -> AGENCY_ACKNOWLEDGED -> MONITORING -> SUPPLEMENT_PENDING -> CLOSED`

Orthogonal fields: `attribution_status=UNKNOWN|CANDIDATE|CORROBORATED|DISPUTED`; `recording_authorization=NOT_EVALUATED|PERMITTED|DENIED`; `legal_assessment=NOT_REVIEWED|POTENTIALLY_RELEVANT|COUNSEL_REVIEWED`; `case_resolution=UNRESOLVED|REQUEST_ACKNOWLEDGED|CONTACT_STOPPED|UNKNOWN`. A case cannot become `SUBMITTED` merely because a draft was generated or a web page was opened. A confirmation number cannot be invented.

**Transition guards:** `USER_APPROVED` requires identity/line authorization, evidence snapshot, template-specific required fields, completed human attestation, consent sharing confirmation, and destination validation. `SUBMISSION_IN_PROGRESS` requires an adapter with an approved documented method and scoped credential; otherwise choose `EXTERNAL_SUBMISSION_REQUIRED`. `SUBMITTED` requires a real transport acknowledgment, and `AGENCY_ACKNOWLEDGED` requires a distinguishable confirmation from the receiving body.

## 19.4 Source-of-truth evidence taxonomy

| Evidence type | Capture source | Trust label | Reversible? |
|---|---|---|---|
| Signaling timestamps, call ID, duration, route | Signed carrier webhook/Asterisk | observed; integrity of origin must be validated | Corrections as appended events only |
| Observed CLI/caller ID | Carrier | observed number, NOT verified calling party | Append corrective attribution |
| STIR/SHAKEN evidence (when exposed) | Telecom interface | network attestation, not fraud verdict | Preserve original and interpretation |
| AI transcript | Authorized STT operation | derived; may contain speech recognition error | Preserve versions and revisions |
| Caller-stated seller and promise | Authorized transcript or user statement | claimed | Corrections as annotations |
| User DNC instructions | Authorized outbound/inbound request event | observed system action or user-attested | Append new status |
| Operator acknowledgement | Authorized transcript or user report | claimed/attested, not verified suppression | Update upon new evidence |
| Later calls apparently from same campaign | Correlation engine | inferred until corroborated | User/moderator unlink |
| Written business confirmation | Business email/document with provenance | external documentary, verification scope noted | Preserve origin |
| Official complaint receipt | Agency response or user-uploaded confirmation | external receipt; verification status explicit | Preserve origin |

Original material and derivative summaries are separately retained and never silently overwritten. Hashes detect modification but are not, by themselves, proof of authenticity, chain of custody, or admissibility.

## 19.5 Evidence packet structure and manifest

On export create a versioned bundle:

```text
case_<case-id>_<snapshot-id>.zip
  manifest.json
  README_EVIDENCE.txt
  case_summary.pdf  (rendering component may be configured separately)
  chronology.csv
  calls.csv
  opt_out_requests.csv
  operator_identity_assessment.json
  issue_spotting.json
  user_attestation.json
  audit_extract.json
  attachments/   (only authorized, selected originals)
  redactions.json
```

`manifest.json` fields: `schema_version`, `case_id`, `snapshot_id`, `created_at_utc`, `tenant_id_pseudonym`, `included_artifacts[] {artifact_id,sha256,byte_length,mime_type,origin,collection_method,collected_at_utc,consent_status,redaction_state}`, `excluded_artifacts_summary`, `generator_version`, `hash_algorithm`, `export_digest`, `review_status`. No unsigned claims of notarization, forensic certification, admissibility, or irrefutable identity.

## 19.6 Minimum schema extensions

```sql
CREATE TABLE cases (
 id UUID PRIMARY KEY, tenant_id UUID NOT NULL, protected_line_id UUID NOT NULL,
 title TEXT NOT NULL, state TEXT NOT NULL, opened_at TIMESTAMPTZ NOT NULL,
 attribution_status TEXT NOT NULL DEFAULT 'UNKNOWN',
 resolution_status TEXT NOT NULL DEFAULT 'UNRESOLVED',
 review_version INT NOT NULL DEFAULT 1
);
CREATE TABLE case_incidents (
 case_id UUID NOT NULL, incident_id UUID NOT NULL, linkage_type TEXT NOT NULL,
 evidence_basis JSONB NOT NULL DEFAULT '{}'::jsonb,
 created_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(case_id, incident_id)
);
CREATE TABLE evidence_artifacts (
 id UUID PRIMARY KEY, tenant_id UUID NOT NULL, case_id UUID,
 incident_id UUID, source_type TEXT NOT NULL, storage_key TEXT,
 sha256 TEXT, collected_at TIMESTAMPTZ NOT NULL,
 processing_authorization TEXT NOT NULL,
 sharing_authorization TEXT NOT NULL,
 provenance JSONB NOT NULL, deleted_at TIMESTAMPTZ
);
CREATE TABLE case_assessments (
 id UUID PRIMARY KEY, case_id UUID NOT NULL, ruleset_version TEXT NOT NULL,
 issue_type TEXT NOT NULL, status TEXT NOT NULL,
 supporting_event_ids JSONB NOT NULL, missing_requirements JSONB NOT NULL,
 evaluated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE filing_drafts (
 id UUID PRIMARY KEY, case_id UUID NOT NULL, agency TEXT NOT NULL,
 template_version TEXT NOT NULL, draft_json JSONB NOT NULL,
 evidence_snapshot_digest TEXT NOT NULL, review_state TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE submission_approvals (
 id UUID PRIMARY KEY, draft_id UUID NOT NULL, user_id UUID NOT NULL,
 approved_payload_digest TEXT NOT NULL, attestation_version TEXT NOT NULL,
 authorized_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE filing_submissions (
 id UUID PRIMARY KEY, draft_id UUID NOT NULL, approval_id UUID NOT NULL,
 adapter TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL, sent_at TIMESTAMPTZ,
 transport_response_ref TEXT, agency_receipt TEXT, failure_code TEXT,
 created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE case_audit_events (
 id UUID PRIMARY KEY, case_id UUID NOT NULL, actor_type TEXT NOT NULL,
 action TEXT NOT NULL, event_time TIMESTAMPTZ NOT NULL,
 event_data JSONB NOT NULL
);
```

This is a **logical schema sketch**, not complete migration SQL: production migrations additionally require explicit foreign keys to prior tables, `tenant_id`/RLS and access checks on every relation, indexes, enum or check constraints, audit immutability policy, encryption design, lifecycle handling, and tests. Record user corrections as new audit events; do not mutate source artifacts. Underlying files may still have to be deleted under lawful retention/deletion controls; avoid false promises of mathematically immutable personal data.

## 19.7 API additions

| Method | Resource | Security/behavior |
|---|---|---|
| POST | `/v1/cases` | Authorized protected-line owner; optional creation idempotency key |
| GET | `/v1/cases` | Tenant-scoped pagination, filter states |
| GET | `/v1/cases/{id}` | Authorized case including incident links and evidence sufficiency |
| POST | `/v1/cases/{id}/incidents:link` | Append linkage basis, create audited association |
| POST | `/v1/cases/{id}/incidents:unlink` | Append correction; original incident remains |
| POST | `/v1/cases/{id}/assess` | Versioned deterministic rules; AI commentary distinct |
| POST | `/v1/cases/{id}/exports` | Async job, evidence snapshot, scoped download URL |
| GET | `/v1/cases/{id}/timeline` | Source-labeled chronological events |
| POST | `/v1/cases/{id}/filings/drafts` | Agency template, do-not-call case eligibility check |
| GET | `/v1/filing-drafts/{id}` | Exact draft revision and source list |
| POST | `/v1/filing-drafts/{id}/approve` | User attestation, exact approved payload digest |
| POST | `/v1/filing-drafts/{id}/submit` | Requires approved snapshot and verified supported adapter |
| GET | `/v1/filings/{id}` | Submission state; must not hallucinate receipt |
| POST | `/v1/filings/{id}/supplements` | Separate user-approved material update |
| POST | `/v1/cases/{id}/evidence/{artifactId}/redact` | Make derived redacted version; preserve permitted original |

`POST /submit` must return `422 UNSUPPORTED_DESTINATION` when no lawful supported electronic interface exists; the UI should offer official manual handoff. For ambiguous transmission failures, query status or display `UNKNOWN` rather than silently retrying and risking duplicate complaints.

## 19.8 Filing adapter contract

```typescript
interface FilingAdapter {
  id: string;
  destinationName: string;
  method: 'documented_api' | 'manual_handoff';
  supportedIssueTypes: string[];
  validate(draft: FilingDraft): Promise<ValidationReport>;
  prepare(draft: FilingDraft): Promise<PreparedSubmission>;
  submit?(submission: ApprovedSubmission): Promise<SubmissionReceipt>;
  lookupStatus?(receipt: SubmissionReceipt): Promise<FilingStatus>;
}
```

`manual_handoff` does not implement `submit`. For every external agency, maintain `destination_registry` records with source URL, capability evidence, last-reviewed date, authorization requirements, supported fields, attachment constraints, retention, and sunset procedures. No headless submission/captcha bypass absent express authorization. No bulk unsolicited filing. For FCC unwanted-call complaints specifically, explain that FCC does not resolve individual unwanted-call complaints and that a receipt is not adjudication.

## 19.9 Eligibility and issue-spotting engine

The engine uses law-versioned checklists for, as applicable: subscriber and line control; call dates and times; whether calls were telemarketing, informational, debt collection or exempt; whether the alleged seller is identifiable; DNC Registry or entity-specific request history; timing of later communications; type of technology used; recording authorization; jurisdiction; and applicable exceptions. Output states: `INSUFFICIENT_INFORMATION`, `POSSIBLY_RELEVANT`, `REQUIRES_COUNSEL_REVIEW`, `NOT_SUPPORTED_BY_CURRENT_EVIDENCE`. **Never output** `VIOLATION_PROVEN` or an automatically collectible damages amount. Civil-claim references must remain issue-spotting only and include a disclaimer against legal-advice claims.

## 19.10 Filing experience

From a case screen: **Build case** (automatic) -> **Review incidents** (separate suspected from confirmed) -> **Choose recipient** -> **Check completeness** -> **Generate draft** -> **View exact words and attachments** -> **Attest and approve** -> **Submit through verified method OR open official manual form** -> **Save confirmation** -> **Track updates**. Filing drafts cannot disclose unconsented third-party audio, full community reports, unrelated names, or hidden personal details; apply explicit export scope.

## 19.11 Example case: repeated credit-loan solicitations

The owner reports hundreds of unwanted calls per week and describes 11 manually initiated return calls, with IVR option 2 leading to a human agent, before a removal request was apparently honored. The system should import permitted historic user-attested accounts as `USER_ATTESTED`, keep them separate from events witnessed by the software, and **not fabricate recordings, carrier metadata, dates, identities, or confirmations**. Future instrumented calls generate genuine `OBSERVED` events. The evidence timeline includes each attempted resolution, route, operator response, opt-out delivery, claimed acknowledgment, and any subsequent potentially related inbound call. User-confirmed 11-attempt history demonstrates the need for resilient resolution tracking, not a product mandate to auto-dial 11 times.

## 19.12 Automatic case-building worker

```text
carrier_event_received
  -> authenticate_provider_signature
  -> normalize_and_dedupe
  -> persist_call_observation
  -> check_recording_processing_policy
  -> create_or_match_candidate_incident
  -> attach_to_case_if_confident_else_queue_review
  -> recompute_optout_timeline
  -> recompute_ruleset_assessment
  -> refresh_case_dossier_snapshot_metadata
  -> alert_user_only_for_material_changes
```

No LLM should decide whether to place another call or send a complaint. Retries are idempotent jobs; original provider webhook references are retained. Every derived assessment stores model and rule versions. Case updates are eventually consistent; the UI communicates processing states and errors.

## 19.13 Acceptance scenarios

1. **First protected call:** event appears, provenance identifies carrier; candidate case opens in <= 5 seconds after authenticated event delivery under healthy reference deployment.
2. **No consent:** audio isn't persisted or sent to remote STT; lawful metadata can still be documented. If live processing is prohibited, fall back to provider-only screening and routing.
3. **Spoofed CLI:** same displayed number never automatically proves same operator or opens a verified violation determination.
4. **Eleven manual attempts:** system represents all attempts separately and has no arbitrary one-call cutoff; attempts remain human-authorized and bounded by lawful policy.
5. **AI transcription error:** reviewer can correct derived text with immutable version reference to earlier output; original evidence unaffected.
6. **Export:** one package has case timeline, opt-outs, source provenance, manifest, hashes and explicit omissions; secrets and other users' data absent.
7. **Unsupported filing API:** submission is blocked; user receives correct manual filing path and a copyable prepared submission.
8. **Successful authorized submission:** exact approved revision matches transmitted payload; receipt stored and visible.
9. **Ambiguous response:** state is `UNKNOWN`, no blind resubmit, user asked to verify status.
10. **Duplicate event:** multiple webhook deliveries create only one observed call and no duplicate complaint.
11. **New evidence:** supplemental draft requires new user approval and doesn't silently alter the old filing.
12. **Withdrawal:** user can stop further processing, revoke community sharing where possible, and request deletion subject to documented legal restrictions.

## 19.14 Measurable gates

- 100% of simulated authenticated provider events either retained once or placed in a recoverable error queue.
- 0 false `SUBMITTED` states in failure injection tests.
- 0 transmissions absent exact-draft user authorization.
- 100% exported attachments recorded in a digest manifest.
- 100% cross-tenant access tests deny access.
- 0 unsupported agency API calls falsely represented as official electronic filing.
- 0 automatically inferred legal guilt or asserted damages calculations.
- No more than 0.5% mislinked incidents in the manually reviewed release validation corpus (target, must be measured).

# 20. Implementation kickoff backlog and engineering handoffs

## 20.1 Work packages and dependencies

| Epic | Deliverable | Dependency | Release |
|---|---|---|---|
| E01 | Repo, linting, tests, Docker Compose, secrets | None | R0 |
| E02 | Authentication, tenants, verified protected lines, RLS | E01 | R0 |
| E03 | SIP inbound event adapter, call session lifecycle, fallback | E01,E02 | R0 |
| E04 | Append-only source event ingestion, idempotency, evidence store | E02,E03 | R0 |
| E05 | Consent/processing policy engine and legally reviewed deployment modes | E02 | R0 go/no-go |
| E06 | AI-assisted inbound screening, transfer, control-plane separation | E03,E05 | R1 |
| E07 | Opt-out case model, incident links, evidence timeline | E04 | R1 |
| E08 | Verified destination registry and user-initiated outbound SIP/DTMF | E02,E03,E05 | R1 |
| E09 | AI-assisted opt-out script, IVR state engine, call review | E08 | R1 |
| E10 | Case dashboard, correction and permissioned artifact viewer | E07 | R1 |
| E11 | Versioned case exports with hashes, manifest and audit extract | E04,E07 | R1 |
| E12 | Deterministic legal-issue checklists and complaint template schema | E07, legal review | R2 |
| E13 | Complaint drafting, user attestation, approved snapshot | E11,E12 | R2 |
| E14 | Filing registry, official manual handoff, user-supplied receipt | E13 | R2 |
| E15 | Authorized electronic adapters when validated | E14, external availability | Later |
| E16 | Opt-in privacy-reviewed community intelligence and moderation | E07, privacy audit | Later |

Do not split these into 16 independent microservices for R0. Use a modular monolith for domain logic plus telephony/media worker and background jobs; introduce separate services only for measured operational needs.

## 20.2 Suggested first 20 engineering tickets

1. Initialize repository, `AGENTS.md`, formatting, tests, and CI.
2. Define tenant/role/line ownership contracts and database migrations.
3. Implement OIDC login and protected-line enrollment.
4. Implement signed provider webhook adapter and replay defense.
5. Define event schema with source provenance and schema versions.
6. Build inbound SIP test harness and automated fixtures.
7. Implement deterministic fallback routing and call loop prevention.
8. Implement consent / speech-processing permission policy as a standalone module.
9. Implement an event ledger with tenant partitioning and idempotency.
10. Implement evidence artifact store with hashes and scoped access.
11. Implement incident classification labels and correction events.
12. Implement opt-out case lifecycle and incident linking.
13. Implement chronological case timeline UI.
14. Implement trusted call screening and human transfer.
15. Implement a verified organization/contact directory with manual verification.
16. Implement user-initiated outbound call with explicit session-level approval.
17. Implement IVR DTMF handling and operator-connected detection test fixtures.
18. Implement constrained AI opt-out dialogue and acknowledgment parsing.
19. Implement case bundle export with manifest and redaction tests.
20. Implement dry-run complaint template generation with no remote submission.

## 20.3 Repository contracts and migration rules

- Backend publishes versioned OpenAPI spec and JSON schemas; frontend consumes generated typed client.
- Any persisted enum changes require migration and backwards-compatible event handling.
- Each command accepting a legal/financial/telephony action has explicit `actor_id`, `scope`, `authorization_basis`, `idempotency_key` and auditable outcome.
- All AI prompt policies and deterministic rulesets are separately versioned.
- Test fixtures must use fictional numbers or provider-reserved test numbers; real calling only in authorized controlled environments.
- All network functions default disabled until configured.
- Code ownership: security owner for telephony outbound changes; privacy owner for recording and federation; legal reviewer for templates and filing adapters.

## 20.4 Decision log required before merge to main

`D-001`: first supported telephone transport and provider; `D-002`: actual live STT consent gate by legal jurisdiction; `D-003`: hosting vs fully local processing; `D-004`: AGPL-v3-only vs permissive licensing; `D-005`: incident-to-case match threshold validated against false attribution; `D-006`: initial FTC/FCC forms and whether any agency-approved submission API exists; `D-007`: data retention and deletion in operator's jurisdictions; `D-008`: company legal entity, trademark clearance, and insurance.

# 21. Legal documents and developer-liability refinements

This supplements Appendices A–F, which should be read as draft documents, not guaranteed legal shields.

## 21.1 Role distinction

Different legal obligations may attach to (i) maintainer publishing source, (ii) hosted service operator, (iii) telephony provider account holder, (iv) a user initiating calls, and (v) third-party community data provider. License terms are not the same as service conditions. The publisher cannot make a user solely legally responsible in every scenario by asserting it in a README.

## 21.2 Mandatory review before release

- Confirm source license is included unmodified where required, attribution/copyright notices are correct, and licenses for third-party models/voices/code are compatible.
- Separate a `DISCLAIMER.md` from license text. Use clear 'AS IS', no warranty, limitation to extent permitted by law, no legal advice, no guaranteed complaint results, and no responsibility for independent operator configuration; avoid claiming nonexistent absolute immunity.
- For hosted offerings, have counsel draft enforceable terms with assent mechanism, jurisdiction/venue where appropriate, indemnification if legally defensible, and dispute resolution provisions vetted for local law and users.
- Publish `ACCEPTABLE_USE.md` explaining that maintainers do not operate or supervise independent self-hosters, while provider-operated hosted resources disallow abuse and may be suspended.
- Explain that lawful recording/transcription and AI call processing may depend on all call participants' locations and provider contracts.
- User attestation states factual accuracy to the best of user's knowledge, consent to share specified artifacts, and no guarantee that regulator acts on complaint.
- Do not imply FCC complaint submission means a private lawsuit is commenced, or FTC reports create an individual remedy.
- Before enabling direct agency submission, secure legitimate permission for the channel and update disclosures for data transfers.

## 21.3 Additional suggested filing acknowledgment (draft)

> I have reviewed the exact complaint text, recipient, and attachments and believe the statements I am submitting are accurate to the best of my knowledge. I understand that observed call metadata, caller statements, and AI inferences are different types of evidence; that the identity of the responsible caller or business may be unverified; and that filing a complaint does not establish a legal violation or guarantee investigation, compensation, or removal from a calling list. I authorize transmission of the selected information to the named recipient using the described submission channel. I understand I am responsible for confirming that I am entitled to disclose the included material, and that recordings or transcripts may be subject to consent laws.

This text requires counsel and jurisdiction-specific adaptations; user approval should be captured with exact payload digest, timestamp, and version.

# 22. Final contradiction and readiness audit — v3.0

| Previous gap or contradiction | Corrective specification | Status |
|---|---|---|
| Enforce treated as later or merely manual | Automatic case ingestion/timeline/export in MVP; filing drafts R2 | Resolved |
| Implied submission without confirmed agency API | Verified adapter registry and manual handoff | Resolved |
| Inbound recording conflated with live processing | Separate legally validated processing permission | Resolved in design; counsel gate open |
| Caller ID equated to offender | Attribution statuses and corroboration requirement | Resolved |
| History of 11 calls treated as automatic redial quota | Manual provenance and case workflow only | Resolved |
| Evidence called immutable while users have deletion rights | Append-only logical events with lawful retention/deletion and audit policy | Resolved |
| Hashes conflated with evidentiary admissibility | Integrity only; full chain-of-custody context | Resolved |
| AI conclusions presented as legal determination | Deterministic checklists + counsel review | Resolved |
| Auto filing might spam agencies | Exact approval, single idempotent submission, supplements | Resolved |
| Open-source disclaimer claimed full developer immunity | Explicit limits and role-specific counsel review | Resolved |
| MVP release scope unclear | R0/R1 capture and export; R2 drafts; later direct adapters | Resolved |
| Telephony provider and regulatory implementation not selected | Recorded go/no-go decisions D-001–D-008 | Open implementation decision |
| Regulatory form mapping unimplemented | Templates require validation before shipping | Open implementation decision |
| Security/privacy review not completed | Mandatory preproduction gates | Open external review |

**Release verdict:** Ready for repository scaffolding, interface contracts, synthetic test harness, and local-only prototype development. Not certified or cleared for public deployment, real outbound calling, recording, or regulatory submission until the specified technical and legal gates pass.

**Document lineage:** v3.0 includes the complete v2.0 PRD and adds these authoritative requirements. Where requirements conflict, sections 19–22 control.


# APPENDIX A — Proposed developer-facing disclaimer (`DISCLAIMER.md`)

> **DRAFT — NOT LEGAL ADVICE; ATTORNEY REVIEW REQUIRED BEFORE DISTRIBUTION**

**Software provided as-is.** CallShield Network is experimental open-source software furnished on an “AS IS” and “AS AVAILABLE” basis, without representations or warranties of any kind, express or implied, to the maximum extent permitted by applicable law. Applicable warranty disclaimers and liability limitations in the project's governing open-source license continue to apply.

**No legal, regulatory, or technical assurances.** The developers, copyright holders, contributors, and maintainers do not represent that use of the software, any calling strategy, any AI-generated response, recording, report, or data-processing configuration will comply with applicable telecommunications, privacy, recording, consumer-protection, or other law in any particular jurisdiction. The software does not provide legal advice or guarantee enforcement, removal from solicitation lists, call suppression, or admissibility of records.

**Independent operator responsibility.** Those who install, configure, host, connect telephony providers to, or operate the software are responsible for evaluating their intended uses, securing rights and consents, complying with applicable law and provider agreements, safeguarding data and credentials, and reviewing outputs before acting on them. The mere availability of a technical feature is not a statement that its use is lawful.

**Third-party services.** Developers do not control independent telephone carriers, SIP providers, AI vendors, self-hosted instances, third-party plugins, or operators and do not guarantee their performance or conduct. The exact relationship and obligations of any particular developer or operator depend on the actual facts, arrangements, and applicable law.

**No endorsement of harassment.** The project's documented purpose is defensive call handling, authorized opt-out requests and legitimate resolution workflows. Publication of source code does not constitute authorization or encouragement to intimidate, harass, threaten, deceive, interfere with, or unlawfully contact any person or organization.

**Limitation of liability.** To the fullest extent allowed by applicable law and the governing license, copyright holders and contributors disclaim liability for losses associated with use or inability to use the software, including lost calls, unintended routing, incorrect classification, data loss, expenses, operational interruption or third-party misuse. Nothing in this notice excludes liability that cannot legally be excluded, nor does it provide immunity from statutes, government enforcement, criminal law, or claims arising from a party's own conduct.

**No attorney-client relationship.** Materials and automated complaint drafts do not establish an attorney-client relationship. Operators should consult qualified counsel for jurisdiction-specific questions.

**License controls.** This notice supplements the governing LICENSE file as explanatory documentation; it does not purport to replace, rewrite or silently add restrictions to the open-source license.

---

# APPENDIX B — Suggested acceptance acknowledgment (installation / hosted onboarding)

> **DRAFT — USE ONLY AFTER COUNSEL REVIEW AND CHECKING ENFORCEABILITY**

**Before activating telephony features, I acknowledge that:**

- I control or am authorized to manage the telephone lines connected to this installation.
- I am responsible for obtaining necessary permissions and complying with applicable law and carrier/provider terms.
- Displayed caller ID may be false or spoofed and does not, by itself, identify a responsible organization.
- AI-generated assessments may be incorrect; I must review consequential outcomes.
- Recording, transcription, and live AI processing may require additional disclosures and consents.
- A removal request or verbal acknowledgment does not guarantee suppression of further calling.
- Repeated contacts made to harass, intimidate, or disrupt a destination are not authorized by the project's intended workflows.
- I understand that the project is experimental and can lose or mishandle calls if misconfigured.
- I have reviewed the license, disclaimer, privacy notice, and any applicable service terms.

**User interaction:** Checkbox group + timestamp + versioned text + operator ID, not a single vague “I waive all responsibility” button. The acknowledgment records informed acceptance; it does not guarantee a court will enforce a liability waiver against every kind of claim.

---

# APPENDIX C — Acceptable-use policy (`ACCEPTABLE_USE.md`)

> **DRAFT — service/network rules, not extra restrictions inserted into AGPL**

This policy governs maintainer-operated hosted services, public federation services, community infrastructure and support channels, to the extent stated in applicable service terms. Independent use of licensed source code remains governed by the software license and applicable law.

**Permitted intended behavior:** Inbound screening of controlled lines; consensual call transfers; individually authorized legitimate opt-out requests to verified official contact channels; recordkeeping; complaint preparation; privacy-preserving defensive signals.

**Prohibited on maintainer-operated services:** Mass or coordinated retaliatory calling; repeated calls intended to harass or overwhelm; calls to unverified spoofed-number owners; third-party doxxing; impersonation of officials; threats; unlawful recording; data theft; evasion of provider rules; using community data as a target list; fabricated complaints; unauthorized operation of someone else's line.

**Enforcement:** Rate limits, case review, account or network suspension, artifact deletion, and referral to providers or authorities where necessary and lawful. Maintain a documented appeal and correction process. The project does not guarantee monitoring of every independently deployed instance.

---

# APPENDIX D — Privacy notice outline (`PRIVACY.md`)

> **DRAFT — actual notice must describe the actual operator's data flows**

1. **Who operates the deployment:** Identify legal entity, service address, and privacy contact. The upstream code maintainer is not necessarily the controller/operator of self-hosted installations.
2. **Categories collected:** Verified line data, metadata, caller-stated claims, opt-out cases, user settings, optional legally captured media/transcripts, limited telemetry.
3. **Why processed:** Routing, screening, case resolution, evidence exports, security, and optional separately consented community intelligence.
4. **Processors/recipients:** Selected telephony carrier, AI provider, storage/hosting vendor, or approved recipient of an export. No claim that third-party processors are absent when they are used.
5. **Retention:** Document actual configuration and erase cycles; describe backups and lawful preservation.
6. **Choice and access:** Export, deletion, consent changes, sharing opt-in and correction where applicable.
7. **Security:** Encryption, roles, logging and incident response, with no promise of absolute security.
8. **International processing:** Disclose if audio or metadata goes to another jurisdiction.
9. **Community network:** Separate affirmative consent; explain aggregate linkage risks and correction mechanisms.
10. **Contact and updates:** Version/date, material-change notice, and privacy support contact.

Do not publish this outline as though it were a complete operational privacy notice; fill with factual implementation and review applicability of state privacy statutes.

---

# APPENDIX E — License, entity, copyright, and liability strategy

1. **Select legal entity before distributing at scale.** Counsel should consider formation, role separation, corporate governance, insurance and indemnities. An LLC/corporation can offer useful structural separation but is not absolute protection from personal liability, unlawful conduct or all claims.
2. **License source code under a standard license.** AGPL-3.0-only is proposed for reciprocal source availability; consider Apache-2.0 if ecosystem adoption matters more. Publish full unmodified license text and copyright notices. Do not create a “modified AGPL” by adding a general ban on disfavored fields of use; OSI principles require broad use rights.
3. **Distinguish project from hosted business.** The operator of a hosted dial/AI service assumes additional telephony, privacy, service contract and incident response obligations; the project repository alone is not equivalent to operating the service.
4. **Use separate contracts where valid.** Hosted customer Terms of Service may include lawfully drafted acceptable use, representations, indemnification, limitations, dispute processes, and governing law. Some clauses are not enforceable in some contexts; counsel must review.
5. **Document safety-by-design.** Evidence that the maintained product has verified destinations, inbound ownership checks, bounded dialing, recording gates and abuse controls is more valuable than relying only on broad disclaimers.
6. **Publisher transparency.** Publish SECURITY.md, vulnerability disclosure contact, support boundaries, dependency/SBOM procedures, and response process for credible abuse reports.
7. **Avoid unsupported “no liability” claims.** Open-source status does not categorically absolve the developer from product, tort, intellectual-property, data-security, telecom, or criminal exposure.
8. **Do not promise indemnity from users without counsel.** Any indemnity must be narrowly drafted to align with real parties, valid assent, governing law and enforceability limits.
9. **Trademark and branding:** Clear name and distinguish unofficial forks; preserve origin notices where permitted by license.
10. **Counsel deliverables before public launch:** (a) license and attribution memo, (b) federal/state telecom functionality review, (c) recording/AI interception matrix, (d) contract/disclaimer assessment, (e) privacy/consumer rights assessment, (f) hosted provider agreement review, (g) insurance review, (h) abuse-handling playbook.

---

# APPENDIX F — Specific sources and legal verification list

1. FTC, *Complying with the Telemarketing Sales Rule*: https://www.ftc.gov/business-guidance/resources/complying-telemarketing-sales-rule
2. FTC, *Telemarketing Sales Rule*: https://www.ftc.gov/legal-library/browse/rules/telemarketing-sales-rule
3. U.S. Code, 47 USC §223 (current House compilation): https://uscode.house.gov/us/usc/119-118/t47/s223/
4. FCC, AI-generated voices and TCPA (Feb. 8, 2024): https://docs.fcc.gov/public/attachments/DOC-400393A1.pdf
5. RCFP, *Recording Guide*: https://www.rcfp.org/introduction-to-reporters-recording-guide/
6. FCC, *How the FCC Handles Your Complaint*: https://consumercomplaints.fcc.gov/hc/en-us/articles/202752940-How-the-FCC-Handles-Your-Complaint
7. FTC, *National Do Not Call Registry FAQs*: https://consumer.ftc.gov/articles/national-do-not-call-registry-faqs
8. GNU GPLv3 sections 15–17; compare AGPL text during license review: https://www.gnu.org/licenses/gpl-3.0.html
9. Open Source Initiative, *The Open Source Definition*: https://opensource.org/osd

**Last review date:** October 27, 2026. Laws, agency rules, and carrier policies may change. This specification must be re-reviewed before implementation and before each public release.

---

# APPENDIX G — Self-audit against product completeness

| Audit domain | Rating | Current state / release action |
|---|---|---|
| User problem and differentiator | Strong | Real experience captured; no false “11 automatic retries” requirement |
| Inbound feature completeness | Strong specification | Verify carrier integration, fallback and authorization |
| Outbound resolution completeness | Strong product definition | Validate IVR, legal calling mode, destination verification and stop paths |
| System architecture | Feasible proposal | Benchmark Asterisk/media pipeline; create detailed ADRs |
| Data/evidence model | Strong draft | Threat-model tampering, access controls and deletion |
| AI conversation design | Moderate-high | Needs real call fixtures, quality evaluation and red-team testing |
| Community federation | Conceptual | Intentionally deferred; privacy architecture needs deep validation |
| Legal posture | Cautious draft | Counsel approval mandatory; disclaimers not immunity |
| Cost economics | Unvalidated | Carrier/provider/model benchmarks required |
| Deployment readiness | Not built | R0 working prototype necessary |
| Final public launch readiness | Not ready | Require all operational, legal, performance and security gates |

**Consistency check:** The PRD does not promise that users can legally call indefinitely. It preserves iterative, genuinely purposeful opt-out case management and IVR navigation, while excluding mass harassment and rapid repeat-calling automation. It does not claim anonymous community data is automatically safe or that an OSS license waives statutory duties. It does not claim carrier, recording or consent capabilities that have not yet been validated.

**Final acceptance:** Full requirements, flows, draft legal pack, schemas, QA plan and phased delivery are specified; product implementation, exact legal suitability, carrier support and operational performance remain to be verified.
