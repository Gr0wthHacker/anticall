# anticall: Callback Flow Change Spec

Oct 27, 2026 · Jack Gierlich. **Supersedes PRD v3 on outbound contact where they conflict.** DRAFT; gated behind counsel release gates G1–G5.

## Summary

Replace the outbound opt-out call in PRD v3 with an automated callback flow. The system returns a recent spam call to the number that placed it, listens to the answering party's introduction, discloses itself, collects identifying details, and hangs up.

The callback exists to identify who is behind the call (the company, the seller they represent, any transfer target) so the user's complaint names a party. Every legal guardrail lives in code, not policy text, because disclaimers do not bind the people being called.

## Scope changes vs PRD v3

| Area | PRD v3 | New behavior |
|---|---|---|
| Outbound opt-out call to a "verified company contact" | AI voice calls a researched business number | **Removed** |
| Calls to caller-ID-only numbers | Prohibited | Allowed as a callback, behind the gates below |
| Outbound AI voice | Off by default, speaks a DNC request | Speaks once, after a listen window, with a fixed disclosure; asks identifying questions only |
| AI persona | Not specified | Always identifies as an automated assistant; never claims to be the user, a buyer or a human |
| Recording before disclosure | Not specified | Nothing persisted; live local extraction only |
| Remote speech-to-text or LLM | Optional, off by default | Disabled for callbacks; local processing only |
| Voice biometrics | Not specified | Prohibited in all features, including incident-to-case matching |
| Agency submission | Manual handoff | Unchanged |

The AI dial tool stays absent. Every callback still requires a separate user authorization.

## Callback eligibility gates

A callback can be queued only when every gate passes. Evaluate gates server-side at queue time and again immediately before dialing; fail closed on any missing field.

1. **Inbound record exists.** The target number must match the caller ID of a logged inbound call to this user. No free-text numbers.
2. **STIR/SHAKEN attestation = A.** Store the attestation level from the inbound SIP Identity header or verstat parameter on the call record. B, C, missing or unparseable means callback blocked; route the incident to the complaint flow only. If the trunk or forwarding path strips attestation, treat it as missing.
3. **Within the user's window.** Inbound call time is within `callback_window_hours` (user-set, default 72, max 168). This reduces reassigned-number risk; it does not reduce spoofing.
4. **Explicit user authorization.** One tap per callback, logged with user ID, timestamp, inbound call ID and app version. Authorization expires after 30 minutes if the dial has not started.
5. **Number not blocklisted.** Check the per-user and global callback blocklists (see hard stops). A number blocked for "wrong party" or "stop calling" is never called again by any user of the instance.
6. **Caps not exceeded.** See caps section.
7. **Calling hours.** Dial only 8:00 to 21:00 in the user's local time and, when the line-type lookup returns a geography, the destination's local time.

If any gate fails, show the user which one and offer the complaint flow.

## Call flow

The AI produces no speech until the disclosure, and any wrong-party or stop signal ends the call and blocks the number.

1. **Dial.** Place the call from the user's verified number. Start a `max_call_seconds` timer.
2. **IVR navigation.** Detect IVR prompts with local STT. Respond with DTMF tones only. For speech-only menus, end the call and log `ivr_speech_only`; do not synthesize speech to get through.
3. **Live answer detection.** Classify the first speech as IVR, voicemail or live human. Voicemail: hang up, leave no message, log `voicemail`.
4. **Listen window.** Stay silent. Run local STT and field extraction in memory. Trigger the disclosure at the first of: the answering party's first pause of 800 ms or more after a business introduction; 1.5 s of silence after a bare greeting ("hello" with no business name); `listen_window_seconds` elapsed.
5. **Disclosure, as the first utterance.** Fixed template, not generated: "This is an automated assistant returning a call from this number at [time] on behalf of [first name]. This call is recorded. Can you confirm the company name and who you're calling for?" The recording sentence is mandatory when `outbound_recording=true` and kept by default. The LLM cannot alter this text.
6. **Ask questions.** Up to `max_question_turns`. Allowed intents: confirm company, seller represented, offer type, transfer target, callback number, website. The AI never states it is a person, never claims to be the user or an interested buyer, never gives the user's personal data, and answers "are you a robot?" with yes.
7. **Log and hang up.** Short closing ("Thank you, goodbye"), hang up, write fields with claimed labels, attach to the incident case.

**Hard stops** (checked continuously from step 4 onward, keyword plus intent classifier): "wrong number", "we didn't call you", "stop calling", "take me off your list", any threat or distress. Action: hang up (after the disclosure, first say "Sorry for the trouble, goodbye"; **before the disclosure, stay silent**, so the AI never speaks ahead of the disclosure), add the number to the instance callback blocklist, mark the incident `possible_spoof` where applicable. No further callbacks to that number are possible.

## Data handling

The disclosure is the boundary: nothing heard before it is stored as audio or text.

| Phase | Audio | Raw transcript | Structured fields | Where processed |
|---|---|---|---|---|
| Listen window (before disclosure) | RAM only, discarded at disclosure | RAM only, discarded at disclosure | Persisted: `rep_name`, `company_claimed`, `greeting_type` | Local STT and local extraction |
| After disclosure, `outbound_recording=false` (default) | Not stored | Not stored | Persisted: all fields below | Local |
| After disclosure, `outbound_recording=true` | Stored, starts at end of disclosure | Stored | Persisted | Local |
| Call metadata | n/a | n/a | Persisted | Local |

Extracted fields: `rep_name`, `company_claimed`, `represents_seller`, `offer_type`, `transfer_target`, `numbers_mentioned`, `websites_mentioned`, `hung_up_after_disclosure`, `wrong_party`, `stop_requested`. Every value from the answering party is labeled `claimed`. Call metadata (time, duration, attestation, line type, outcome) is labeled `observed`.

Outbound recording is off by default. To enable it per call, the user must confirm the recording notice is in the disclosure script; the toggle is unavailable if the disclosure template has been edited to remove it.

Retention: metadata and fields 90 days, audio and transcripts 30 days, as in PRD v3. A user-opened case places its records on hold until the user closes the case or deletes it.

**Prohibited:** speaker embeddings, voiceprints or any voice-similarity features, in storage or in matching logic. Incident-to-case matching uses numbers, metadata and extracted text only.

## Caps, rate limits and caller ID

- Per inbound call: at most 2 callback attempts. An attempt counts when the dial starts, answered or not.
- Per destination number: at most 2 attempts per rolling 30 days across all of the user's inbound calls from that number.
- Per user: at most 5 callback attempts per day; minimum 10 minutes between attempts.
- Instance-wide: at most 3 attempts per destination number per rolling 30 days across all users.
- Caller ID: the user's own number only, verified by an ownership check (SMS or voice code) before the first callback. No custom caller ID field in the UI, API or config.
- Attestation on our side: the SIP provider must sign outbound calls with A-attestation for the user's verified number. If the provider cannot, block outbound callbacks for that number.

Caps are enforced in the dialer service, not the UI, and are not configurable above these ceilings.

## Configuration defaults

| Key | Default | Allowed range | Who can change |
|---|---|---|---|
| `callback_enabled` | false | true / false | User, after counsel release gate is lifted |
| `callback_window_hours` | 72 | 1 to 168 | User |
| `listen_window_seconds` | 10 | 0 to 10 (0 = disclose on answer) | User |
| `bare_greeting_disclose_ms` | 1500 | 500 to 3000 | Operator |
| `max_attempts_per_inbound` | 2 | 1 to 2 | User |
| `max_attempts_per_number_30d` | 2 | 1 to 2 | User |
| `max_attempts_per_user_day` | 5 | 1 to 5 | User |
| `max_question_turns` | 4 | 1 to 4 | Operator |
| `max_call_seconds` | 120 | 30 to 120 | Operator |
| `outbound_recording` | false | true / false | User, per call |
| `remote_ai_processing` | false | false only for callbacks | Locked |
| `allowed_attestation` | A | A only | Locked |

## Acceptance tests (mock telephony harness, synthetic SIP traffic)

- [ ] Inbound call with attestation B, C or missing: callback button disabled, gate reason shown, complaint flow offered.
- [ ] Attestation stripped by forwarding: treated as missing.
- [ ] Inbound call older than `callback_window_hours`: callback blocked.
- [ ] Free-text or edited destination number: rejected by API.
- [ ] Third attempt for the same inbound call, number or instance window: rejected by dialer service even if the UI is bypassed.
- [ ] Dial attempted outside 8:00 to 21:00 local: rejected.
- [ ] Answering party gives a business intro: AI silent until pause or 10 s ceiling, then disclosure as first utterance.
- [ ] Answering party says only "hello": disclosure starts within 1.5 s of silence.
- [ ] "wrong number" / "we didn't call you": hang-up (apology line only if the disclosure already happened; silent in the listen window), number added to instance blocklist, no further callbacks possible.
- [ ] "stop calling" or equivalent: hang-up, number blocklisted.
- [ ] Hang-up after disclosure: `hung_up_after_disclosure=true`, no retry unless user authorizes a second attempt within caps.
- [ ] AI asked "are you a robot?" or "who are you?": answers that it is an automated assistant for the user.
- [ ] Prompt-injection attempts by the answering party: AI ignores and discloses nothing beyond the disclosure template.
- [ ] No audio or raw transcript from the listen window exists on disk or in logs after the call.
- [ ] No outbound audio stored when `outbound_recording=false`.
- [ ] No call or transcript data leaves the host during a callback (network egress test).
- [ ] No voice embedding or speaker-ID model is loaded anywhere in the build (dependency audit).
- [ ] Caller ID always equals the user's verified number; no config path can change it.

## Release gates

Build everything behind `callback_enabled=false`. Ship to real numbers only after counsel signs off.

| Gate | Blocks | Counsel question |
|---|---|---|
| G1 Consent theory | All callbacks | Does an inbound call from a number give implied prior express consent under 47 USC 227(b) for one disclosed, automated return call to that number? |
| G2 Spoof exposure | All callbacks | What is our exposure if an A-attested inbound number was nonetheless spoofed, and is the A-attestation gate a sufficient precaution? |
| G3 Listen window | Listen-window extraction | Is live local extraction before disclosure, with no audio or transcript persisted, "recording" under all-party statutes including Cal. Penal Code 632.7? If yes, disclosure must move to the first utterance and extraction start after it. |
| G4 State automated-dialer laws | All callbacks | Do state laws on automated or prerecorded messages (Indiana first) apply to a noncommercial return call, and what do they require? |
| G5 Provider terms | Provider selection | Do the chosen SIP trunk and any model or voice vendors permit this use? |

If G3 comes back unfavorable, the fallback is already supported: set `listen_window_seconds` to 0 and extraction starts after the disclosure.
