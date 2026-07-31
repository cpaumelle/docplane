# Service topology

How this installation is shaped, and why it holds that shape.

## One writer, many readers

PostgreSQL is the only authored source of truth. Every write path —
dashboard, MCP tools, scripts — goes through docs-api, which owns
validation, optimistic locking, audit events and publication receipts.
Nothing else connects to the database with write intent.

## The four surfaces

| Surface   | Role                                        | Talks to    |
|-----------|---------------------------------------------|-------------|
| docs-api  | single writer, contracts, migrations        | PostgreSQL  |
| dashboard | operator authoring and audit views          | docs-api    |
| docs-web  | generated, certified MkDocs release + front | docs-api    |
| docs-mcp  | agent tool surface                          | docs-api    |

## Why a generated site

The published site is a build artifact, not a mirror. Publication
revalidates every bound revision inside one transaction, then builds and
promotes the release and records a certification state. If the build
fails after the database mutation, the authored state stays durable and
`POST /api/v1/publication/retry` republishes it without re-authoring.

## Failure domains

The database and the generated site fail independently by design.
A dead site never loses authored content; a dead database never serves
stale content as current — certification state says which release is
trustworthy.
