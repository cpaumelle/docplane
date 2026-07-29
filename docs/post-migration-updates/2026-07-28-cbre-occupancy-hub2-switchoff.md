# CBRE occupancy — hub2 ingest cut, route 46 retired, and the pages that still deny it

**Status:** APPLIED
**Legacy page(s):** `operations/occupancy-hub2-switchoff.md` (already written, v9) ·
`control-plane/architecture.md` · `services/occupancy-pipeline-hub2/*` · `sites/ovhcloud/hub2.md` ·
`operations/microshare-eu-cloudflare-migration.md`
**Raised:** 2026-07-28 by claude-code (hub2 session)

---

## 1. Already written to legacy docs-api — needs carrying across

`operations/occupancy-hub2-switchoff.md` was updated **v8 → v9** on 2026-07-28 15:27:44 UTC
(`updated_by: claude-code`). The write succeeded; only rendering is stuck, so it is invisible on
the live site. If the DocPlane import took a snapshot before that timestamp, **this change is
missing there** and must be re-applied.

The delta is six edits, supplied as a unified diff alongside this file:

    2026-07-28-cbre-occupancy-hub2-switchoff.v8-to-v9.diff        <- prefer this
    2026-07-28-cbre-occupancy-hub2-switchoff.v9-SNAPSHOT.md      <- fallback only

Apply the diff, not a full-page copy — the page is actively maintained and a wholesale replace
would revert anyone else's later edits.

### What the six edits say

| # | Location | Change |
|---|---|---|
| 1 | Status line | Phase 1 + 2 complete 2026-07-28; Phase 3 soak running |
| 2 | Change log | New row **C6** — route 46 disabled, with evidence |
| 3 | Change log | New row **C7** — `cbre-comparison.timer` stopped, and the finding that it was already dead |
| 4 | Teardown inventory | `cbre-comparison.timer` marked STOPPED + DISABLED |
| 5 | Phase table | Phase 1 → ✅ done (last uplink 15:06:59Z); Phase 2 → ✅ done |
| 6 | Phase table | Phase 3 → 🟢 running from 15:22, earliest teardown 2026-08-04 |

---

## 2. What actually happened (evidence)

**Phase 1 — Actility ingest leg cut.** hub2 stopped receiving at **2026-07-28 15:06:59Z**.

Before the cut, `websecure-ingest-microshare-eu@file` was the second-busiest router on hub2's
entire edge at 0.18 req/s, matching ~12 rows/min into `ingest_raw` from 85 distinct devices. After
it: nine consecutive probes over 4.5 minutes, all zero.

    15:10:30  last=15:06:59  age=212s  new_60s=0
    15:14:35  last=15:06:59  age=456s  new_60s=0

The flat-then-zero shape rules out a draining retry queue, which would decay rather than stop dead.

**vps3 continuity confirmed concurrently** — gateway `1000B6AD` last uplink 1 s old, 57 devices
heard in 30 min, multiple gateways fresh. The fleet moved; it did not go dark.

**Phase 2 — DM route 46 retired.**

    POST /api/domains/46/disable  →  200  "Domain ingest.microshare.eu disabled"

Verified: `routes.yml` regenerated 15:22:11 with zero references; Traefik router
`ingest-microshare-eu@file` gone; `POST https://ingest.microshare.eu/uplink` → **404**;
`charliehub_routing_drift_router_mismatch` showed the documented 1→0 propagation transient then
held 0 across five samples, all other drift gauges 0 throughout.

Used the governed lifecycle endpoint, not `PUT {"status":"disabled"}` — the latter is correctly
rejected 422 because `status` is lifecycle-immutable.

**Retired rather than repointed at vps3.** vps3 already ingests via TP-X, and
`services/actility-crud/operations.md` scopes it TP-X-only; `parity.md` names `api`/`app` as the
compatibility endpoints, not `ingest`. Repointing would have added an undesigned second delivery
path that would double-ingest if `77762.AS` were ever re-enabled.

Rollback: `POST /api/domains/46/activate`; snapshot `hub2:~/dm_ingest46_snapshot_20260728.json`
(revision 7, status=active).

---

## 3. Finding — `cbre-comparison` was never running

Decision **D10** in the switch-off tracker kept this timer through the soak specifically to "keep
parity comparison live". It could not have been:

    /usr/bin/python3: can't open file '/opt/cbre-pipeline/scripts/continuous_comparison.py':
    [Errno 2] No such file or directory

**86 invocations, 0 successes**, each exiting in ~25 ms with `status=2/INVALIDARGUMENT`.

Confidence note: the earliest retained failure is 2026-07-21, but hub2's journal was vacuumed to
1 GB earlier the same day during a disk reclaim, so that is a **retention edge, not a start date**.
The true start is likely earlier and cannot be recovered.

Consequence: any conclusion drawn from "hub2↔vps3 parity was being continuously compared during
the soak" is unfounded. Same false-signal class as `maintenance_state` reading `clean` through a
genuine key split.

---

## 4. Pages that still say the cutover has not happened

This is the highest-value part of this entry — these are what someone reads to answer "where does
occupancy run today", and they currently answer wrongly. Tracked as **DF1/DF2/DF4** in the
switch-off tracker's own open-drift table.

| Page | Currently says | Should say |
|---|---|---|
| `control-plane/architecture.md` (v22) | "occupancy cutover not yet done"; CBRE pipeline listed as incumbent production path | Reads cut over to vps3 2026-07-19; hub2 ingest cut 2026-07-28; hub2 stack idle pending Phase 4 teardown |
| `services/occupancy-pipeline-hub2/*` | "current production system for CBRE and Airbus occupancy data" | Same correction. The directory name is itself a post-cutover misnomer — renaming is clone-to-new-path + archive-old, not an in-place edit |
| `sites/ovhcloud/hub2.md` | "incumbent, transitional… cutover is gated" | Cutover complete; hub2 runs an idle stack under soak |
| `operations/microshare-eu-cloudflare-migration.md` | `CNAME → edge-occupancy → hub2` for `api`/`app` | Both resolve to vps3 (51.83.77.153) |
| Cloudflare zone (not a docs page) | `occupancy.microshare.eu` → hub2, 404, DNS-only, no DM route | Dead record; cleanup scheduled at Phase 4 |

**Migration opportunity:** these corrections are worth making *as part of* the DocPlane import
rather than porting the wrong text and fixing it after.

---

## 5. Related false signals found the same day (not doc changes — recorded so they aren't lost)

- **`/usr/local/bin/hub2-backup.sh:31`** reads `"before hub2-pbs at 02:00 and CBRE at 02:10"`.
  There is no CBRE job at 02:10; the Tier-1 job dumps only `charliehub_domains` and DocPlane.
  Someone auditing backup coverage reads that and concludes CBRE is covered. Suggested fix is an
  explicit negative statement rather than deletion. The script was reconciled into charliehub-hub2
  by #333, so this goes through that repo, not a direct edit of `/usr/local/bin`.
- **`/etc/cron.d/cbre-backup.disabled-20260720-hub2-retiring`** header advertises
  `nightly pg_dump -> px1:/var/lib/vz/dumps/cbre-pipeline/`. That directory on px1 is **empty**.
- `/var/backups/cbre` (8 dumps, Jul 13–20) deleted 2026-07-28 by owner decision during a disk
  reclaim. Per the tracker's own retirement gate these were historical provenance only, not a
  teardown prerequisite — Phase 4 requires a fresh `pg_dumpall` regardless. The 2026-07-07
  pre-retention dump remains at `px3-nas:/mnt/pve/px3-nas/dump/cbre-preservation/`.
- `db-backups/pre-trevarn-vm1124/cbre_reports_pre-trevarn.pgdump` deleted 2026-07-28 — its entire
  table of contents was a single ACL grant on `public` (empty database), so the filename implied a
  recovery point that never existed.

---

## 6. Still blocking full hub2 occupancy retirement

Unchanged by this work. Per `services/trevarn/hub2-occupancy-retirement.md`, builds **#3** (daily
health-digest email + client-API-flow parity test) and **#4** (robot3 weekly quality detections,
`signal_quality`, `direction_check`) remain unshipped. Both are authored on VM1124 and deployed to
vps3 — not hub2 work. Teardown (Phase 4) stays gated behind them plus the 7-day soak.

## How to apply on DocPlane

1. Check whether `operations/occupancy-hub2-switchoff.md` on DocPlane already contains rows `C6`
   and `C7`. If yes, section 1 is done — set that part APPLIED.
2. If not, apply `2026-07-28-cbre-occupancy-hub2-switchoff.v8-to-v9.diff`.
3. Work section 4 into the imported pages (or fix during import).
4. Set **Status: APPLIED** at the top of this file; do not delete it.


## Applied — 2026-07-29

**Status: APPLIED.**
- §1 `operations/occupancy-hub2-switchoff.md` — live on DocPlane at **v9** (the v8→v9 change was imported); no republish needed.
- §4 (pages that still denied the cutover) — corrected through DocPlane publication:
  - `control-plane/architecture.md` — change `28aabb5e-2adc-4beb-bfc7-7ab7c800965e`, revision `f1683cba…` → `ae4ca2e0…`, release `3b84eb1d-4ddb-4468-b9ac-b84d626549b1` ("cutover not yet done" removed; now cutover-complete / hub2 stack idle pending Phase 4 teardown).
  - `sites/ovhcloud/hub2.md` — change `4abbafb1-667e-403d-bab0-7ce549497480`, revision → `bf7b341e…`, release `ddb29988-58dc-46d8-aee3-fd76b5dfb468`.
  - `services/occupancy-pipeline-hub2/*` already corrected under the occupancy-cutover entry; `operations/microshare-eu-cloudflare-migration.md` carried no cutover-denial (nothing to correct).
- certification CURRENT, working == deployed after each publication.
