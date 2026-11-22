# Schema genesis and upgrades

DocPlane ships one authoritative fresh-install schema,
`db/migrations/000_docplane_genesis.sql`, followed by numbered forward
migrations (`001_*.sql` onwards; see `db/migrations/README.md`).

The migration runner (`docs-api/migrate.py`) creates its checksum ledger,
applies genesis once, and then accepts only immutable numbered forward
migrations. Filename, ordinal and checksum drift fail closed. The API container
applies pending migrations before it serves anything.

Migrations are additive: new schemas, tables, nullable or defaulted columns, and
CHECK constraints extended only by adding values. Later migrations added the
work captures, the model and observe schemas, closure gates and provenance,
verification requests, generated-artifact contracts and projections,
artifact-scoped automation principals and the addressed WORK inbox.

Genesis contains the product model only. It does not preserve the discarded
development role hierarchy, approval workflow, shared API-key routes or legacy
bootstrap schema. Workspaces are classification boundaries; validated changes
publish directly with version history and deployment certification.

DocPlane does not support in-place upgrade from unreleased pre-genesis
development databases. A production instance is created from genesis or
populated through an explicit content import.
