# Post-migration doc updates — pending queue

**MIGRATION COMPLETE (2026-07-29): every entry is APPLIED; this PR is ready to merge and close.** It is a queue, not a
deliverable. Merge it only when the migration is done and every entry has been applied to DocPlane
(or explicitly dropped).

## Why this exists

While the corpus is mid-migration there is no single safe place to record "this legacy page is
wrong and needs updating":

- Writing to legacy docs-api still works, but **rendering is broken** (`docs.redirects` missing —
  see `DEFECT-docs-legacy-render-missing-redirects-table.md` in the charliehub-hub2 repo), so the
  change is invisible and may not survive the migration.
- Writing to DocPlane directly is premature while pages are still being imported — an edit can be
  overwritten by a later import of the legacy version.
- A staging folder in someone's home directory is **not backed up** (hub2's Tier-1 job covers
  `/opt/charliehub/` and the DB dumps only), has no concurrency model, and two agents editing the
  same file silently clobber each other.

A long-lived PR solves all three: durable and off-node, restorable, diffable, and conflicts surface
explicitly instead of being lost.

## How to add an entry (agents: read this before writing)

**One file per change.** Never edit another agent's file. This is what keeps concurrent agents from
colliding — the same pattern the charliehub-hub2 `docs/defects/` convention uses.

```
docs/post-migration-updates/YYYY-MM-DD-short-slug.md
```

Commit to the `docs/post-migration-updates` branch and push. If you hit a conflict, it will be on
the branch tip, not inside a file — rebase and push again.

### Required structure

```markdown
# <Title>

**Status:** PENDING | APPLIED | DROPPED
**Legacy page(s):** `operations/foo.md`
**Raised:** YYYY-MM-DD by <agent/person>

## What is wrong
## What it should say
## Evidence
## How to apply on DocPlane
```

### Rules

1. **Record the change, not the rendered page.** Prefer a diff or a precise "replace X with Y" over
   a full page copy. A full-page snapshot goes stale the moment anyone edits the page and then
   invites a re-apply that silently reverts newer content. Where a full snapshot is genuinely
   needed for migration, mark it clearly as a **dated snapshot, not a source of truth**, and stamp
   the source version.
2. **Never commit rendered pages into a docs tree as if they were source.** Legacy `docs-mkdocs/docs/`
   is derived state — the database is authoritative. This queue holds *change requests*.
3. **Cite evidence.** A claim that a page is wrong needs the observation that proves it — a command
   and its output, a metric, a DB query. "Looks stale" is not an entry.
4. **Set `Status: APPLIED`** (don't delete the file) once the change is live on DocPlane, so the PR
   doubles as the migration's audit trail.
5. **State your confidence** if you could not fully verify. An honest "unverified — could not reach
   vps3" is worth more than a confident guess.

## Index

| Entry | Legacy page(s) | Status |
|---|---|---|
| `2026-07-27-occupancy-cutover-doc-corrections.md` | `services/occupancy-pipeline-hub2/{client-validation,report-api}.md` | APPLIED |
| `2026-07-27-dinard-tv-geo-doc-corrections.md` | 6 pages — managed-consumers, address-plan, dinard-fr-site, geo-execution, fire-tv-setup, engineering-backlog | APPLIED |
| `2026-07-28-cbre-occupancy-hub2-switchoff.md` | `operations/occupancy-hub2-switchoff.md` + 4 others | APPLIED |
| `2026-07-29-browan-fw-dev-vm-move.md` | `services/microshare-dev/browan-fw-dev.md` | APPLIED |

## Snapshots vs fetch-on-demand

Entries generally do **not** carry full-page copies: while legacy docs-api is alive the content is
retrievable on demand, and a stored copy only goes stale. Each entry instead records the page
version it depends on, so an import can be verified against it.

**This breaks the moment legacy docs-api is decommissioned.** Before that happens, every entry
still marked PENDING must have its referenced pages exported — otherwise the change is lost with
the source. Treat that as a gate on switching legacy off, not an afterthought.
