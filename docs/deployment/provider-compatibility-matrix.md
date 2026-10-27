# Provider Compatibility Matrix (Template)

> **ALL ENTRIES UNVERIFIED.** No carrier or provider has been tested or confirmed. Do not infer support from this file. Decision D-001 is open. Verify against the provider's current terms and with counsel before use.

Legend: `UNVERIFIED` (default), `CONFIRMED` (cite evidence), `NOT SUPPORTED`. Every non-UNVERIFIED cell needs a source URL or test reference and a date.

| Provider / carrier | Inbound SIP / DID | Signed webhooks | Inbound screening by 3rd party allowed | User-initiated outbound | Automated / AI-voice calls allowed | DTMF (RFC 2833) | Call recording permitted | STIR/SHAKEN attestation exposed | Terms last reviewed |
|---|---|---|---|---|---|---|---|---|---|
| Generic SIP trunk (reference for R0) | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | |
| `[provider name]` | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | |
| `[provider name]` | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | |

## Notes

- Existing mobile numbers generally cannot be intercepted regardless of carrier (non-goal N7). Document a supported routing mode (forwarding, SIP-native number, or app) per row.
- If a provider forbids automated calls, the outbound AI module must be disabled for that deployment (PRD 7.3).
- Record webhook replay window and duplicate-delivery behavior for idempotency tests.
- Add one test reference per CONFIRMED cell (for example, a `tests/telephony` scenario ID).
