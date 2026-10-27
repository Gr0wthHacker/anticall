# Recording and Live-Processing Consent Matrix (Template)

> **TEMPLATE - NOT LEGAL ADVICE. NO CONCLUSIONS ARE ASSERTED HERE.** Counsel must complete and approve this matrix (counsel deliverable c) before recording, persistent transcription, or live AI speech processing is enabled anywhere (decision D-002).

## Framing (PRD section 11.3)

- US recording and interception law differs across states, and a call may involve more than one jurisdiction. Some states generally require consent of all parties; this document does not say which, and defaults do not rely on it.
- Federal one-party rules do not displace stricter state laws that may apply.
- A mere announcement does not necessarily make recording or live AI processing lawful.
- Two separate questions: (1) **live media processing** (STT/AI hearing the call), (2) **persistence** (audio/transcript retained).
- When authorization cannot be established: **disable the capability** and fall back to signaling metadata only, provider-only screening, transparent routing, or human-only contact (ENF-008).

## Default policy (engine behavior until counsel approves)

`recording_authorization = NOT_EVALUATED` -> treated as DENIED. No audio persisted, no remote STT.

## Matrix

One row per jurisdiction the operator's lines or callers may touch. Leave cells blank until counsel completes them.

| Jurisdiction | Live processing permitted? | Persistence permitted? | Required disclosure / consent mechanism | Inbound vs outbound differences | Counsel source / date | Reviewer |
|---|---|---|---|---|---|---|
| `[state / territory]` | | | | | | |
| `[state / territory]` | | | | | | |
| `[unknown caller location]` | Treat as DENIED | Treat as DENIED | n/a | n/a | engine default | n/a |

## Open questions for counsel

- Mixed-jurisdiction calls (line owner in one place, caller in another): which rule governs the engine's default?
- Is a pre-answer disclosure sufficient for live AI processing, and for persistence?
- Do provider contracts (carrier, STT vendor) impose additional recording or processing terms?
- Is signaling metadata alone sufficient for the ENFORCE timeline in restrictive jurisdictions?
