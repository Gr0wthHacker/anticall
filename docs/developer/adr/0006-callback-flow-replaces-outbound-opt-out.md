# ADR 0006: Callback flow replaces the outbound opt-out call

- Status: Proposed (gated; code ships behind `callback_enabled=false`)
- Date: 2026-10-27
- Source: `docs/prd/CHANGE_callback_flow.md` (supersedes PRD v3 outbound sections), ADR 0003, D-009

## Context

PRD v3 had the AI place an outbound call to a researched "verified company contact" and speak a do-not-call request. That path is weak on its own terms: the number is found by research, the call rarely reaches anyone who can act, and what it produces is a claim of receipt, not an identity. The complaint flow needs the opposite: a named party behind a specific unwanted call.

The callback flow returns a recent inbound call to the number that placed it, listens to the introduction, discloses itself, asks identifying questions and hangs up. Disclaimers do not bind the people on the other end, so every guardrail is code.

## Decision

- Remove the opt-out outbound path. `POST /v1/resolution-cases/{id}/start-contact` and `.../contact-authorizations` return `410 OUTBOUND_OPT_OUT_REMOVED` with pointers to the callback API and the complaint flow. Case tracking, manual attempt logging, timeline and next steps are unchanged. The reusable libraries (`resolve.policy`, `resolve.service`, IVR navigator, agent tools) stay; their tests run against the services directly (`tests/resolve_legacy.py`).
- Add `callshield/callback/` behind `callback_enabled=false` (env prefix `CALLSHIELD_CALLBACK_`). Every route is a 404 until an operator enables it and the user opts in.
- Destinations come only from a logged inbound call's displayed caller ID. The API takes `inbound_call_id`, never a number; request bodies forbid extra fields.
- Seven eligibility gates (inbound record, A-attestation, user window, one-tap authorization, blocklists, caps, calling hours) are pure and fail closed. They run at queue time and again inside the dial service, and once more immediately before the ring.
- The dialer service owns the caps (2 per inbound call, 2 per number per 30 days per user, 5 per user per day with 10 minutes between attempts, 3 per number per 30 days across the instance). Ceilings are constants, not settings.
- The instance-wide cap and blocklist cross tenants, so they are keyed by an HMAC of the destination under a server-side secret and hold no tenant, user or call identifiers.
- Caller ID is the user's own protected line only, and only after line verification, an outbound ownership check and a provider-reported outbound A-attestation (`line_outbound_attestation`). No field, header or config path sets a caller ID. The dialer seam takes a `VerifiedCaller` object that only `callback.lines.verified_caller` can build.
- The call is a deterministic state machine. All speech comes from a closed set of fixed strings; the dialogue model may only choose a question intent. The disclosure is a versioned template in code, rendered with the call time and the user's first name. The AI says nothing before the disclosure, with no exceptions.
- Hard stops (wrong party, stop request, threat or distress) hang up (apologising first only if the disclosure already happened; silent in the listen window), add the number to the per-user and instance blocklists and, for wrong party, append a `possible_spoof` classification and ledger event.
- Listen-window text lives in an in-RAM buffer with no persistence methods and is discarded at disclosure. Only `rep_name`, `company_claimed` and `greeting_type` survive. Audio and transcripts are stored only with `outbound_recording=true`, starting after the disclosure.
- Processing is local only. `assert_local_only` refuses non-loopback, non-unix-socket model endpoints. Speaker embeddings and voiceprints are prohibited; a test scans dependencies and source.
- Dialing runs as a worker job: `callback.dial` (gates, claim, consume authorization, count the attempt) then `callback.place` (no retries; last time/blocklist/identity check, then the ring). The counted attempt is committed before the phone rings and a crash cannot cause a second ring.

## Tradeoffs

- More moving parts and a fixed script. Fixed text cannot adapt, which is the point: nothing a callee says can change what the system says or does.
- A-attestation is a network assertion, not proof. Requiring it cuts reassigned-number and obvious-spoof risk but excludes most scam traffic, which is rarely A-attested. Those incidents go to the complaint flow only.
- The 800 ms / 1.5 s / 10 s listen-window triggers lean on local STT quality. If counsel finds listen-window extraction is "recording" (gate G3), set `listen_window_seconds=0` and extraction starts after the disclosure; the code path exists and is tested.
- Dropping the opt-out call removes the only automated attempt at suppression. Suppression remains manual (Resolve manual attempts) and through the complaint flow.

## Residual risk

- **Spoof exposure on A-attested numbers (G2).** An A-attested caller ID can still be a number the caller is authorized to use but that belongs to someone else (a leased or compromised line, a reassigned number, a legitimate customer of a bad actor). The callback would then reach an innocent party. Mitigations: window limit, wrong-party and stop-request hard stops, the instance blocklist, caps, calling hours. They reduce exposure; they do not remove it. Counsel must say whether the A-attestation gate is a sufficient precaution before any real-number use.
- Consent theory for a disclosed, automated return call (G1), state automated-dialer laws (G4) and provider terms (G5) are open. Nothing ships to real numbers until `docs/legal/release-gates.md` shows them closed.
- Outbound A-attestation is provider-reported; this repo records it but does not verify it. A wrong report would let a callback go out with weaker attestation.

## Consequences

- Eleven new tables (`db/models/callback_ext.py`), three worker jobs (`callback.dial`, `callback.place`, `callback.expire_sweep`), one router, and a docs/legal update.
- The AI tool registry stays at exactly eight tools; there is no dial tool. Only the user's one-tap authorization starts the dial chain.
- The legacy opt-out tests keep running against the reusable services so the cooldown, cap, IVR and agent-tool assertions are not lost.
