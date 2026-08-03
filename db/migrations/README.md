# Migration history

Applied migrations are immutable. DocPlane records each filename and SHA-256 in
`docplane.schema_migrations` and refuses to start if an applied file changes.

Every schema change, including changes needed by a fresh installation, must be
an additive migration with the next unused ordinal. Do not update
`000_docplane_genesis.sql` after release; fresh installations reach current
schema by applying the complete ordered migration history.
