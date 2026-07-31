# Restore the database from backup

Rebuild the documentation database from the most recent backup and
return the site to a certified state.

## Preconditions

- A backup you trust, and its timestamp written down.
- The docs-api image tag currently in production, noted as the rollback
  tag before you begin.
- NTP settled on the target host (skewed clocks make the audit trail
  look reordered).
- Nobody mid-authoring: announce the restore window first.

## Procedure

1. Stop docs-api so nothing writes during the restore.
2. Restore the dump into PostgreSQL.
3. Start docs-api and watch its log: the entrypoint applies migrations
   before serving. A restart loop here means a migration is failing —
   stop and diagnose; do not proceed.
4. Confirm the migration ledger is contiguous with no gaps.
5. `POST /api/v1/publication/retry` to rebuild and promote the site.
6. Confirm certification reports CURRENT.

## Success checks

- `/healthz` reports the expected git identity.
- Certification state is CURRENT and the release identity matches the
  latest publication receipt.
- Ten spot-checked pages match their authored content.

## Rollback

The restore itself is the rollback for data. If the new application
image misbehaves, redeploy the noted rollback tag; authored state is
unaffected. A failed site build after a good restore needs only another
publication retry, never re-authoring.
