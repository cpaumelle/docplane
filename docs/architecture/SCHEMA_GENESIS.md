# Schema genesis and upgrades

DocPlane ships one authoritative fresh-install schema:

`db/migrations/000_docplane_genesis.sql`

The migration runner creates its checksum ledger, applies genesis once, and thereafter accepts only immutable numbered forward migrations beginning at `001_*.sql`. Filename, ordinal and checksum drift fail closed.

Genesis contains the current product model only. It does not preserve the discarded development role hierarchy, approval workflow, shared API-key routes or legacy bootstrap schema. Every active named principal is a contributor; workspaces are classification boundaries; validated changes publish directly with version history and deployment certification.

DocPlane does not support in-place upgrade from unreleased pre-genesis development databases. A production instance is created from genesis or populated through an explicit content import.
