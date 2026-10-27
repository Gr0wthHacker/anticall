# ADR 0002: Database-backed job queue before Redis

- Status: Accepted (prototype)
- Date: 2026-10-27
- Source: PRD 7.1 (Redis plus controlled queues), 19.12, 20.1

## Context

The PRD lists Redis for cache and worker queues. The case-building pipeline (19.12) needs idempotent, retryable jobs, and the project aims for lean stacks that run on small servers.

## Decision

Start with a PostgreSQL-backed job table (`SELECT ... FOR UPDATE SKIP LOCKED`, attempt counters, unique idempotency keys, dead-letter state). Redis stays an optional compose profile and is introduced only if measurements show the DB queue is a bottleneck.

## Consequences

- One stateful dependency to run, back up, and secure.
- Job enqueue can share a transaction with the ledger write (no dual-write loss).
- Recoverable error queue satisfies the "retained once or recoverable" gate (19.14).
- Throughput ceiling is lower than Redis; revisit with benchmarks.

## Alternatives rejected

- Redis-first: extra moving part, dual-write risk between ledger and queue.
- External broker (RabbitMQ/Kafka): disproportionate.
