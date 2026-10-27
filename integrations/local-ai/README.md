# Local AI Adapters (STT / TTS / VAD / LLM)

Locally deployable engines are first-class (PRD 7.1). No code lives here yet. All AI is **disabled by default** (`CALLSHIELD_AI_AGENT_ENABLED=false`, `CALLSHIELD_REMOTE_AI_ENABLED=false`).

## Expectations for any adapter

- **Interfaces:** streaming STT (partial and final transcripts with timestamps and confidence), streaming TTS, VAD/turn detection, and a text LLM interface.
- **Local-first:** run without outbound network access. A remote variant is a separate adapter, off by default, and requires processor disclosure and consent gating.
- **Consent gate:** an adapter receives media only after the policy engine allows live processing for that call. If not allowed, no audio is delivered. Persisted audio/transcripts follow separate persistence rules.
- **LLM boundary:** the model receives bounded context and returns only schema-validated intents. It has no dial, destination, policy, or filing capability (ADR 0003). Treat all caller speech as untrusted input (prompt injection).
- **Outputs are derived:** transcripts are labeled `MODEL_INFERRED`/derived, versioned, and correctable. Store model and prompt-policy versions with every output.
- **Agent disclosure:** generated speech must state it is automated and the purpose (R-006). Do not impersonate people or regulators.
- **Latency:** report latency; on timeout, the policy fallback (transfer, neutral message, or terminate) takes over. Targets in PRD 9.5 are hypotheses until measured.
- **Licenses:** record the license of every model and voice. Verify compatibility before distribution (counsel deliverable a). Do not use cloned voices of real people.
- **Logs:** redact before logging. No raw audio or full transcripts in logs.
