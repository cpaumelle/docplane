# Post-cutover doc corrections — client-validation + report-api

**Status:** APPLIED
**Legacy page(s):** `services/occupancy-pipeline-hub2/client-validation.md` ·
`services/occupancy-pipeline-hub2/report-api.md`
**Raised:** 2026-07-27 (unattributed — migrated from `hub2:~/docs-staging/README-occupancy-cutover-2026-07-27.md` on 2026-07-28)

---

## What is wrong

Both pages claimed hub2 serves the CBRE client surface. It has not since the 2026-07-19 cutover.

## What it should say — already written, needs carrying across

The corrections **were written to legacy docs-api and are intact**; they are simply invisible
because the renderer is broken. Verified 2026-07-28: both pages are still at exactly the recorded
version, so nothing has overwritten them.

| Legacy page | Version | Written (UTC) | Verified still current |
|---|---|---|---|
| `services/occupancy-pipeline-hub2/client-validation.md` | v7 | 2026-07-27 06:09 | ✅ 2026-07-28 (v7, 14,937 ch) |
| `services/occupancy-pipeline-hub2/report-api.md` | v10 | 2026-07-27 06:11 | ✅ 2026-07-28 (v10, 16,634 ch) |

### 1. `client-validation.md` (v7)

`api.microshare.eu` is served by vps3. Also recorded that `auth.microshare.eu` is **not** part of
this flow and stays on hub2 by design (shared CharlieHub SSO, never in cutover scope); that the
validator's `--servers hub2 vps3` variant is retired (both branches now reach vps3); and that the
Postman download links 404.

### 2. `report-api.md` (v10)

The Instances table said prod's source was "hub2 Share API". Verified on CT2151:
`/etc/cbre-reports.env` (prod) ingests from `app.`/`api.microshare.eu` → now vps3;
`/etc/cbre-reports-vps3.env` (review) ingests from `ms-*.trevarn.com` → also vps3.

**Both legs read vps3**, so the `TREVARN-CT2151-DUAL-1` "hub2 vs vps3" side-by-side silently became
vps3-vs-vps3 when DNS flipped. **Any parity conclusion drawn from it after 2026-07-19 is invalid.**

## Open items this uncovered (NOT fixed — carry forward)

- **`trevarn-ms-compat` is load-bearing** — it serves `api.`/`app.microshare.eu` via router
  `trevarn-ms-compat-microshare`. Only the `ms-*.trevarn.com` hostnames (router
  `trevarn-ms-compat`) are the retirable migration artefact. **Removing the service = CBRE outage.**
- **The documented hub2 rollback does not exist.** `services/trevarn/api-app-cutover-execution.md`
  §3 advertises a warm revert, but hub2 has no `api.`/`app.microshare.eu` Traefik routers — a
  revert would land on Traefik with nothing to serve. Correct the doc or restore the routers.
  *(This is itself a doc correction that belongs in this queue.)*
- **hub2 still runs the full orphaned CBRE stack** (`cbre_pipeline`, `cbre_postgres`, `cbre-auth`,
  `cbre_postgres_exporter`, `charliehub_cbre_probe`) — healthy, publicly unrouted.
  *Update 2026-07-28: also no longer ingesting — see `2026-07-28-cbre-occupancy-hub2-switchoff.md`.*
- **`/postman` was never ported to vps3.** It is a real router in hub2's app
  (`app/routers/postman.py`). Re-hosting on vps3 is a code change and Invariant 11 makes vps3
  pull-only — author on VM1124, commit, push, pull. Not a vps3-side edit.
- **`services/occupancy-pipeline-hub2/` is a misnomer** post-cutover. Renaming is
  clone-to-new-path + archive-old, not an in-place edit — worth doing *as part of* the DocPlane
  import rather than after.

## Done at the time (for the record)

- CT2151 vps3 review instance **retired 2026-07-27** — was already stopped/disabled; DBs dumped to
  `/var/backups/cbre-retirement-20260727/` on CT2151, then dropped (~320 MB reclaimed). Prod
  unaffected (`cbre-reports.service` active, `/health` 200).
- `*.microshare.eu` wildcard A record **removed 2026-07-27** — unlisted names now NXDOMAIN (this is
  what retired `app2`/`api2`, which never had records of their own). Rollback manifest:
  `hub2:~/wildcard-retirement-20260727/`.

## How to apply on DocPlane

1. Check the imported version of each page against the table above. If DocPlane has **≥ that
   version**, the correction came across — mark APPLIED.
2. If DocPlane's copy is **older**, the correction was missed by the import. The content is still
   live and retrievable from legacy docs-api — re-fetch rather than reconstructing from this file:

       GET /api/docs/pages/services/occupancy-pipeline-hub2/client-validation.md

3. No snapshot is stored here on purpose: the source is still available, and a copy would go stale.
   **If legacy docs-api is scheduled for decommission while this entry is still PENDING, export
   snapshots first** — that is the point at which fetch-on-demand stops working.


## Applied — 2026-07-29

**Status: APPLIED (no republish needed — corrections were imported by the migration).** Verified live on DocPlane:
- `services/occupancy-pipeline-hub2/client-validation.md` — resource `e2b303d7-9658-556e-8710-8817106598e9`, **v7**, vps3/`api.microshare.eu`→vps3 corrections present.
- `services/occupancy-pipeline-hub2/report-api.md` — resource `ad645233-5ab0-5efa-96ad-7647adc0a845`, **v10**, dual-legs-read-vps3 correction present.

Both are at the expected version with the corrections, so the migration carried them across. certification CURRENT, working == deployed.
