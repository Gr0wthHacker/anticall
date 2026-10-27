# Voice Provider Adapters

Adapters connect a telephony provider (SIP trunk, CPaaS) to CallShield. No code lives here yet. Status: **planned; no provider verified** (decision D-001). See [`docs/deployment/provider-compatibility-matrix.md`](../../docs/deployment/provider-compatibility-matrix.md).

## Expectations for any adapter

- **Inbound events:** verify the provider's signature before any parsing; enforce a replay window; normalize into the shared event schema with original provider reference retained; emit idempotent events (duplicates are expected).
- **Provenance:** observed caller ID is labeled observed, never verified. Pass through STIR/SHAKEN attestation when exposed, unmodified.
- **Outbound:** originate only from the policy-gated command (verified destination, case, user approval, idempotency key). The adapter must not expose a free-form dial API to the AI layer or the dashboard (ADR 0003).
- **No spoofing:** no caller-ID override. Present the user's own verified line.
- **Limits:** honor max session duration, cooldowns, and provider rate limits. No retry loops that re-originate calls; on ambiguous failure report `UNKNOWN`.
- **Capabilities:** declare supported features (DTMF, transfer, recording, media streaming) so the policy engine can disable what the provider does not allow.
- **Provider terms:** if the provider forbids automated or AI calls, the adapter must report outbound AI as unsupported.
- **Secrets:** read from environment/secret store; never log credentials, numbers, or audio.
- **Test mode:** every adapter ships with a fake/simulator implementation using fictional 555-01xx numbers for CI.
