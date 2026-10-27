# Architecture

Source: PRD 7, 8, 19. Pre-alpha; module names are targets and may differ from code as it lands. Modular monolith per [ADR 0001](adr/0001-modular-monolith.md).

## Components

```mermaid
flowchart TB
  subgraph Edge
    PROV[Provider webhooks]
    AST[Asterisk / ARI]
  end
  subgraph App["API + worker (one codebase)"]
    ING[Ingestion<br/>signature, replay, idempotency]
    POL[Policy engine<br/>consent, routing, destination gates]
    LED[Event ledger]
    CASE[Case management]
    EVD[Evidence store + export]
    AGT[AI agent<br/>no dial tool]
    FIL[Filing adapters<br/>manual handoff]
    JOBS[DB job queue]
  end
  PG[(PostgreSQL)]
  UI[Next.js dashboard]
  PROV --> ING
  AST --> ING
  ING --> LED --> PG
  POL --> AGT
  LED --> JOBS --> CASE --> EVD --> PG
  CASE --> FIL
  UI --> App
```

## Inbound flow (PRD 7.2)

```mermaid
flowchart LR
  A[SIP / provider webhook] --> B[Verification<br/>signature + replay]
  B --> C[Routing policy<br/>deterministic]
  C -->|trusted| T[Transfer to user]
  C -->|unknown, authorized| AI[AI screening]
  C -->|processing not authorized| F[Provider-only screening / transparent routing]
  AI --> D[Disposition]
  T --> D
  F --> D
  D --> L[Ledger]
  L --> E[Optional evidence]
  E --> UI[Dashboard]
```

## Outbound callback flow (CHANGE_callback_flow; replaces PRD 7.2 outbound resolution)

> Supersedes the PRD 7.2 outbound opt-out call, which was removed (ADR 0006). The callback is off by default (`CALLSHIELD_CALLBACK_CALLBACK_ENABLED=false`) and counsel-gated (G1-G5).

```mermaid
flowchart LR
  I[Logged inbound call] --> G[Gates 1-7, server-side<br/>record, attestation A, window,<br/>authorization, blocklist, caps, hours]
  G -->|any fail| X[Blocked: reason shown, complaint flow]
  G --> U[Single-use user authorization<br/>expires in 30 min]
  U --> R[Re-check gates, then dial<br/>user's verified number only]
  R --> W[Listen window<br/>RAM only, no persistence]
  W --> D[Fixed disclosure<br/>automated assistant]
  D --> Q[Up to 4 identifying questions]
  Q -->|wrong party / stop| H[Hang up + instance blocklist]
  Q --> F[Hang up; claimed fields]
  F --> L[Ledger + case evidence]
```

The AI has no dial tool (ADR 0003). Caps are enforced in the dialer service. The old `start-contact` and `contact-authorizations` endpoints return `410 OUTBOUND_OPT_OUT_REMOVED`.

## Automatic case-building worker (PRD 19.12)

```mermaid
flowchart TD
  e1[carrier_event_received] --> e2[authenticate_provider_signature]
  e2 --> e3[normalize_and_dedupe]
  e3 --> e4[persist_call_observation]
  e4 --> e5[check_recording_processing_policy]
  e5 --> e6[create_or_match_candidate_incident]
  e6 --> e7[attach_to_case_if_confident_else_queue_review]
  e7 --> e8[recompute_optout_timeline]
  e8 --> e9[recompute_ruleset_assessment]
  e9 --> e10[refresh_case_dossier_snapshot_metadata]
  e10 --> e11[alert_user_only_for_material_changes]
```

Jobs are idempotent; original provider webhook references are retained; derived assessments store model and rule versions; no LLM decides on further calls or complaints.

## State machines

- Resolve (5.2.1; the automated contact states are superseded by the callback flow, manual attempts still use them): `CREATED -> DESTINATION_REVIEW -> VERIFIED_DESTINATION -> USER_APPROVED_CONTACT -> IN_PROGRESS -> REQUEST_DELIVERED -> {ACKNOWLEDGED, UNCONFIRMED, REFUSED, INTERRUPTED} -> {MONITORING, NEXT_STEP_REVIEW, CLOSED}`. `ACKNOWLEDGED` never equals suppression.
- Case (19.3): `OPENED -> INTAKE -> EVIDENCE_COLLECTING -> REVIEW_REQUIRED -> ELIGIBLE_TO_DRAFT -> DRAFT_READY -> USER_APPROVED -> SUBMISSION_IN_PROGRESS -> SUBMITTED / SUBMISSION_FAILED -> AGENCY_ACKNOWLEDGED -> MONITORING -> SUPPLEMENT_PENDING -> CLOSED`, with guards per 19.3.

## Failure modes (PRD 7.3)

SIP registration failure -> notify, safe fallback. AI latency -> transfer/neutral fallback/terminate per config. Stream lost -> never infer successful opt-out. Duplicate webhook -> idempotent. Unknown recording legality -> disable live AI media. Missing verified destination -> block AI outbound. Credential compromise -> revoke, rotate, alert. Provider forbids automated calls -> disable outbound AI. Community backend offline -> local features unaffected.

## Cross-cutting rules

- Every telephony/legal/financial command carries `actor_id`, `scope`, `authorization_basis`, `idempotency_key`.
- All network functions default disabled.
- Tenant isolation on every relation (Postgres RLS via `app.tenant_id`; checked by the `api-postgres` CI job, not yet run).
- Prompt policies and rulesets are separately versioned.
