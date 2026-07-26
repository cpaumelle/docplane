# Schema genesis and upgrades

DocPlane ships one authoritative fresh-install schema:

`db/migrations/000_docplane_genesis.sql`

The genesis represents the final product schema and deterministic system seed state. It is not a historical replay of development migrations.

The migration ledger is owned by `docs-api/migrate.py` and is deliberately excluded from the genesis SQL. On a new database, the runner creates the ledger, applies genesis once, records its checksum, and thereafter applies only numbered forward upgrades.

Future released schema changes begin at `001_*.sql` and remain immutable after publication. A checksum or filename mismatch fails closed.

The pre-release development chain that produced genesis was applied to disposable PostgreSQL 16, exported, restored into a clean database, and compared using normalized schema dumps plus semantic system-workspace seed evidence. Both comparisons were exact.

System workspaces use stable product UUIDs so API references and deployment tooling do not depend on install-time randomness. Their timestamps remain install-time values.

DocPlane does not support upgrading databases that recorded the discarded pre-release 000–015 development ledger. Those databases were never a released product contract and must be recreated or migrated through an explicit import path.
