# Going-live checklist

> **Honest status:** nothing in this project has been cleared for real use beyond local, synthetic operation. Real telephony has never been exercised end to end, the legal gates are open, and the legal text is a draft that counsel has not reviewed. This checklist tells you what would have to be true, not that doing it is safe or lawful. Not legal advice.

Work top to bottom. A box you cannot honestly tick means stop.

## A. Technical (any real line)

- [ ] `callshield doctor` has **no FAIL** and every WARN is understood.
- [ ] `CALLSHIELD_ENVIRONMENT=prod`; secrets are generated random values (`doctor` secrets check PASS); `.env` is mode 0600 and not in git.
- [ ] Postgres (not SQLite) with a non-owner application role; `scripts/verify_postgres_rls.py` passes against your database. (The CI job for this has not yet run.)
- [ ] Migrations at head (`callshield db upgrade`); a restore from backup has been **tested**, not just taken. Backups outlive deletion unless you handle erase cycles.
- [ ] TLS reverse proxy in front of API and dashboard; API, dashboard, ARI (8088), Postgres and metrics are not reachable from the internet. SIP/RTP reachable only from your provider.
- [ ] `CALLSHIELD_ALLOW_BOOTSTRAP=false`; CORS lists explicit origins only.
- [ ] Test-call checklist in [`connect-your-sip-trunk.md`](connect-your-sip-trunk.md) passed with **your own numbers**, including the safe-fallback test.
- [ ] Monitoring and alerts set up (`infra/monitoring`); logs contain no raw phone numbers or transcripts.
- [ ] You know how to rotate trunk and ARI credentials and have written it down.

## B. Provider (release gate G5)

- [ ] You read the provider's acceptable-use and service terms yourself (link in `SETUP_REPORT.md`).
- [ ] You have written confirmation that screening, and any automated or AI-voice calling you intend, is allowed on your account.
- [ ] Provider profile values were confirmed against their current documentation (all shipped profiles are UNVERIFIED templates).

## C. Gated features: each is OFF until its gate clears

| Feature | Variable | Do not enable until |
|---|---|---|
| Live speech-to-text | `CALLSHIELD_LIVE_STT_ENABLED` | Counsel confirms the recording/interception rules for **your jurisdiction and callers' jurisdictions** (G3); jurisdiction policy for the line is not UNKNOWN. |
| Recording | `CALLSHIELD_CALLBACK_OUTBOUND_RECORDING` | All-party-consent analysis done; the disclosure script includes the recording notice. |
| Outbound calling | `CALLSHIELD_OUTBOUND_ENABLED` | G1, G2, G4, G5 closed with counsel deliverables. |
| AI voice on outbound | `CALLSHIELD_AI_OUTBOUND_ENABLED` | Same as outbound plus state automated-call law review. |
| Automated callback | `CALLSHIELD_CALLBACK_CALLBACK_ENABLED` | G1-G5 **all** closed by counsel; your trunk exposes attestation and you have verified it with test calls; each user has completed the in-app acknowledgment. |
| Agency filing | `CALLSHIELD_FILING_SUBMISSION_ENABLED` | A documented submission channel exists and counsel approves the filing attestation. Manual handoff works with this off. |
| Community | `CALLSHIELD_COMMUNITY_ENABLED` | Not built beyond tables. Leave off. |
| LLM | `CALLSHIELD_LLM_ENABLED` | Prefer a local endpoint. A remote endpoint sends call text to a third party: check its terms (G5) and your privacy notice. |

Gate definitions and status: [`../legal/release-gates.md`](../legal/release-gates.md). At the time of writing G1-G5 are **open and blocking**. Even after they close, the project's non-goals still apply: no redial loops, no caller-ID spoofing, no harassment, no unconsented recording.

## D. Legal and people

- [ ] You are authorized to manage every line connected (acknowledgment-v1, item 1).
- [ ] Every user accepted the in-app acknowledgment ([`../legal/acknowledgment-v1.md`](../legal/acknowledgment-v1.md)). The wizard's acceptance does not replace it.
- [ ] You have a counsel-reviewed position for your jurisdiction. `CALLSHIELD_INSTANCE_JURISDICTION` is informational; the shipped jurisdiction data is unreviewed and every entry is UNKNOWN.
- [ ] You can explain to anyone who asks what the system does with their call, and how long it keeps it ([`../privacy/data-retention.md`](../privacy/data-retention.md)).
- [ ] You accept that maintainers do not run or supervise your instance, and that the license disclaims warranty.

## E. Recheck on every change

Re-run `callshield doctor` after any upgrade or config change, and re-read `callshield config show` for gated flags. A flag that changed without you changing it is an incident.
