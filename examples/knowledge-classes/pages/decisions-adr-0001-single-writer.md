# ADR-0001: One writer per record

Status: accepted. This page is immutable; if the decision changes, a new
ADR supersedes it and a `SUPERSEDES` link keeps the chain navigable.

## Context

Early prototypes let both the dashboard and a sync script write directly
to the database. Two writers meant two validation paths, and the audit
trail could not say which rules a given row had passed through. A
corrupted page could not be attributed to a code path.

## Decision

Exactly one service — docs-api — writes to the database. Every other
surface (dashboard, MCP, scripts, importers) calls its API and inherits
validation, optimistic locking, idempotency and audit events from the
single writer. Scripts that "just need one UPDATE" are not an exception;
they are the reason this rule exists.

## Consequences

- Adding a new surface costs an API client, never a second SQL path.
- Bulk work (backfills, imports) is expressed as bounded batches of API
  calls with receipts, which makes it replayable and auditable.
- The API becomes a bottleneck by design; capacity work happens there,
  once, instead of consistency work happening everywhere, forever.
