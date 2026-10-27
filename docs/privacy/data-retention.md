# Data Retention

> **Design defaults, not legal advice or requirements** (PRD 11.3). Operators must adjust per region, evidence need, and applicable law. Decision D-007 is open.

| Data class | Default | Env var | Notes |
|---|---|---|---|
| Call metadata / ledger | 90 days | `CALLSHIELD_RETENTION_METADATA_DAYS` | Longer for items linked to an open case |
| Recordings | 30 days | `CALLSHIELD_RETENTION_RECORDINGS_DAYS` | Disabled by default; no recordings exist unless enabled and authorized |
| Transcripts | 30 days | `CALLSHIELD_RETENTION_TRANSCRIPTS_DAYS` | Same as above |
| Opt-out cases | 1 year (user choice) | `CALLSHIELD_RETENTION_CASES_DAYS` | User may shorten or extend within operator limits |
| Observability logs | 14 days | `CALLSHIELD_RETENTION_LOGS_DAYS` | Redacted; no numbers or transcript text |

## Rules

- Private by default; limited retention.
- Deletion and preservation holds are both supported where lawfully appropriate. A hold suspends automatic deletion and is itself an audit event.
- Deleting underlying files does not rewrite the append-only logical record; tombstones keep digest and timestamps where lawful ([ADR 0004](../developer/adr/0004-evidence-append-only-with-lawful-deletion.md)).
- Backups: operators must document how deleted data ages out of backups. Do not promise immediate erasure from backups.
- Exported bundles leave the system. Exports are the user's responsibility once downloaded.
- Users must be told the limits of deletion (legal hold, exports already shared, agency receipts).
