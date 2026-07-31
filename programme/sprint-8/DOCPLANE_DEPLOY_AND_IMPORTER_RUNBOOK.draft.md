# DocPlane deployment & meter-list importer — operator runbook (DRAFT)

> **Draft for corpus publication.** This is repo-side raw material, drafted per
> runbook discipline rule 2 from two real operator sessions: the Sprint 6
> landing of 2026-07-31 (path-contract stop, PR #102 corrective, rerun) and the
> Sprint 8 landing of 2026-07-31 (migration 009, artifact succession on the
> 1.2.0→1.3.0 contract transition, closing UNCHANGED pass). Publish it into the
> corpus through the normal change contract as an `OPERATION` page (suggested
> path `operations/docplane/deployment-and-importer.md`) so it carries
> verification state and appears in DocPlane search. Items marked
> `[operator-verify]` are fabric-side facts to confirm on first exercise;
> verifying them IS the page's first "last successfully followed" attestation.

**Scope.** Rolling out a merged DocPlane change to the production fabric and
reconciling the meter-list importer, including the drift and contract-change
cases. Authority model throughout: Git rules → meter-list importer; PostgreSQL →
desired state; docs-api → migration/publication; generated pages and Work items
are derived consumers. If a step seems to require inverting that arrow, stop.

## Preconditions

- [ ] The change is **merged to `main`** with combined-head CI green
      (fresh-instance `verify`). Never deploy an unmerged branch.
- [ ] A **database backup** taken and retention path noted
      (pattern from the Sprint 8 landing:
      `/home/ubuntu/docplane-deploy-backups/<UTC-stamp>Z`). `[operator-verify]`
      exact backup command in the deployment environment.
- [ ] A **rollback image tag** exists for the currently running docs-api
      (Sprint 6 pattern: `docplane-api:rollback-pre-<change>-<date>`).
- [ ] Migration ledger is **contiguous** before you start (`000–NNN`, no gaps);
      the ledger is append-only and byte-identical for already-applied files.
- [ ] You know whether this deploy carries a migration, a generator contract
      bump (importer `GENERATOR_VERSION`), or both — read the PR's operator
      notes; they are part of the merge contract.

## Procedure — application deploy

1. **Fast-forward the production checkout** to the merged main commit and
   record the SHA in your session notes.
2. **Apply migrations** (only if the deploy carries one):
   `python docs-api/migrate.py apply --dir db/migrations`
   Expected: only new numbers apply; prior entries byte-identical. A run with
   no new migration must report nothing to do — the ledger stays contiguous.
3. **Rebuild and recreate the changed images** (docs-api, dashboard, docs-web
   as the diff requires), tagging the outgoing api image with the rollback
   pattern first. Leave PostgreSQL and untouched services alone.
4. **Health checks**: container healthy with restart count 0 and correct OCI
   revision; API discovery (`/.well-known/docplane.json`), dashboard, and
   `/healthz` all answering. `[operator-verify]` compose service names.
5. **Re-render the publication** so pages pick up generator/UI changes:
   `POST /api/v1/publication/retry`
   Success: release completes and certification identities match (working ==
   deployed, certification `CURRENT`).

## Procedure — importer reconciliation

6. **Dry-read first**: run the importer and read the fingerprint line before
   accepting writes. Three outcomes:
   - `UNCHANGED <fp>` — nothing to do; receipts replay, zero mutations.
   - **Fingerprint changed** (source drift): STOP and confirm the upstream
     rule-repo commits are reviewed/expected before proceeding. This is the
     Sprint 8 pause: a changed fingerprint means writes.
   - **Error** — see failure modes below.
7. **Reconciling run** (source changed or first run after a contract bump):
   `bash scripts/run_meter_list_reconciliation.sh` (flock-guarded; same code
   path as manual). Read the receipt against these expectations:
   - Source edits only → `updated` counts on the touched rules; created/
     retired 0 when rule identities are stable.
   - **First run after a `GENERATOR_VERSION` bump** additionally rewrites all
     entity attributes once (e.g. 256 updated at 1.3.0), backfills link
     metadata (e.g. 92 links_updated), and **retires + succeeds the artifact**
     ("target set or generator contract moved") — one-time, by design.
   - Gap reconciliation fires automatically on changed runs: `created ≤ batch
     limit` (default 10), `resolved` reflecting gaps coverage no longer shows.
8. **Closing pass** — rerun immediately. Success is the literal
   `UNCHANGED <new-fp>` with all-zero reconcile counts, the successor artifact
   still DECLARED (no re-succession), observations and Work rows unchanged.
   A changed system must prove stillness before you walk away.
9. **Coverage sanity**: `GET /api/v1/observe/coverage` and
   `GET /api/v1/observe/coverage/work-items` — totals as expected; briefings
   carry rule identity, page path, authority pointer, runbook-discipline note.

## Success checks (the run is done when ALL hold)

- Certification `CURRENT`; working and deployed identities match.
- Migration ledger contiguous through the new head.
- Closing importer pass printed `UNCHANGED` with zero mutations and zero
  work-queue writes.
- Rendered routes answer HTTP 200 (spot-check
  `/observe/meter-list/<source-slug>/`).
- One NOMINAL `GENERATION` observation bound to the current fingerprint.

## Rollback

- **Application**: recreate docs-api from the rollback tag; other services as
  tagged. Publication re-render restores the prior rendered surface.
- **Migrations**: each migration ships a tested rollback; apply it only if the
  new schema is the actual problem — additive migrations rarely need rolling
  back to restore service.
- **Database**: the retained pre-deployment backup is the last resort; its
  existence is a precondition precisely so this line is boring.
- **Importer**: no special rollback — it is a reconciler. Reverting the rule
  repo (or the application) and rerunning converges the fabric to that
  authority; idempotency keys prevent replayed receipts from a refused
  version. Never hand-edit derived entities, pages, or Work items.

## Known failure modes (all observed in real sessions)

- **Importer refuses with a path-contract violation** (Sprint 6: dotted source
  key vs `publication._PATH_RE`). The refusal is the system working — fix in
  the repo (slug layer + tests, see PR #102), bump the generator version, and
  rerun. Do not work around it on the fabric; substituting identities live
  forks the source of truth.
- **Unexpected artifact retirement in the receipt.** Legitimate exactly when
  the target set or generator contract moved (Sprint 8). If neither moved,
  stop and investigate before the closing pass.
- **Stale-branch lease push failure** during corrective work: the remote
  branch was auto-deleted on merge — prune and re-push; do not force blindly.
- **`COVERAGE_GAP_PAGE_LINK_MISSING` (409)** from work reconciliation: the
  importer hasn't stamped page paths at the current contract — run the
  importer first, then reconcile.
- **Self-issue rate limited (429)** during API work: reuse an existing
  unexpired token; issuance limits are per source and global per hour.

## Scheduling note

`run_meter_list_reconciliation.sh` is timer-safe: UNCHANGED ticks are silent
(receipt-replayed observation, no Work writes — enforced by test). "Never a
blind cron" applies to verification triggers, not reconciliation loops. As of
2026-07-31 no timer is installed; daily is sufficient for rule-repo drift.
