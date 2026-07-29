<!-- DATED SNAPSHOT — NOT A SOURCE OF TRUTH -->
> **Snapshot of `operations/occupancy-hub2-switchoff.md` at v9, legacy docs-api,
> 2026-07-28 15:27:44 UTC.** Fallback only, for the case where DocPlane's copy predates v8
> and the unified diff will not apply. **Prefer the `.diff`.** Do not paste this over a page
> that may have been edited since the timestamp above — re-export first.

---

# Occupancy hub2 Switch-Off Tracker

**Lifecycle:** ACTIVE
<!-- lifecycle: ACTIVE -->

!!! info "Retirement gate (2026-07-20) — migration, not historical archive"
    The gate for retiring hub2 occupancy is:

    1. **successful migration to the replacement occupancy platform**, and
    2. that platform being the **operational source of truth**.

    Both now hold. The pre-retention dump recorded during the 2026-07-07 Option B decision
    is **historical provenance only** — it is **not** an active prerequisite for teardown,
    and retirement is not gated on locating or re-verifying it.

    `cold_archiver.py` was never scheduled and never became operational; it is not a
    dependency of this retirement.

    This does not alter the teardown steps below — the Phase 4 archive is a **forward**
    action taken at teardown time, and remains part of the procedure.

**Status:** In progress — **Phase 1 + 2 complete 2026-07-28** (Actility stopped delivering to hub2 at 15:06:59Z; DM route 46 disabled 15:22); Phase 3 soak running

Living record of the deliberate decommission of the incumbent CBRE occupancy
pipeline (`cbre_pipeline` / `*.microshare.eu`) from **hub2**, now that the
**Trevarn platform on vps3** owns occupancy. Every decision and every change is
logged here as it happens. Precedent: the [parking decommission](../services/parking.md)
(2026-06-11).

> **Sequencing (owner decision, 2026-07-20):** *cutover + soak, then teardown.* Do the
> route/ingest cutover now, let it run idle for a soak window, remove containers later.
> vps3 TP-X ingest parity is **signed off** — the hub2 Actility leg may be cut without a
> fresh parity check.

---

## Context — where occupancy stands

Occupancy reads and the client dashboard are already served from vps3; only the
hub2 **ingest leg** and the idle stack remain. Confirmed 2026-07-20:

| Name | Resolves to | Notes |
|------|-------------|-------|
| `api.microshare.eu` | **vps3** (51.83.77.153) | reads flipped to vps3 (`trevarn-ms-compat`) ~2026-07-19 |
| `app.microshare.eu` | **vps3** | login flipped to vps3 |
| `ingest.microshare.eu` | **hub2** (51.68.235.106) | **LIVE** — Actility still delivers the microshare.eu leg here |
| `auth.microshare.eu` | hub2 | **Authelia SSO — not occupancy.** Stays. |
| `occupancy.microshare.eu` | hub2 | HTTP 404, DNS-only, no DM route — dead; cleanup at teardown |
| `dx-occupancy.microshare.eu` | Cloudflare → CT2151 `:8000` | client dashboard, canonical CT2151 report instance |

---

## Decisions log

| # | Date | Decision | Rationale |
|---|------|----------|-----------|
| D1 | 2026-07-20 | Sequencing = **cutover + soak, then teardown** | Owner call; keeps containers as hot rollback during soak |
| D2 | 2026-07-20 | vps3 TP-X ingest parity **signed off** — cut hub2 Actility leg without a fresh parity check | Owner call; `project_tpx-parallel-source` shows TP-X-only, ~0% miss |
| D3 | 2026-07-20 | Retire the dead `api`/`app.microshare.eu` DM routes **now**, decoupled from the ingest cut | They are already dead at DNS (traffic goes to vps3); pure drift cleanup |
| D4 | 2026-07-20 | **Disable** (not delete) dead routes now; hard-delete at teardown | Reversible during soak; matches parking precedent (routes disabled, removed later) |
| D5 | 2026-07-20 | `auth.microshare.eu` (Authelia) and `coverage`/`docs`/`rodent.microshare.eu` are **out of scope** — not occupancy | Verified backends via Domain Manager |
| D6 | 2026-07-20 | `wg-occupancy` tunnel (hub2:51825 ↔ vps3) **stays** through teardown | It is the ops/monitoring path (`10.201.0.1`), not just occupancy transport |
| D7 | 2026-07-20 | Refactor occupancy Prometheus alerts; **retire anything Spacen in full** (owner directive) | CBRE serving is on vps3; `I-SPACEN-RETIREMENT-1` explicitly scopes monitoring |
| D8 | 2026-07-20 | Re-home the two LIVE vps3 host alerts (node-down, disk-low) to a clean `vps3-node` group; keep hub2 `cbre.yml` through the soak | vps3 is now the primary occupancy platform; CBRE ≠ Spacen |
| D9 | 2026-07-20 | Richer vps3 occupancy instrumentation (pipeline/device/client-freshness) = **follow-on** | vps3 exposes only drain metrics today; needs Trevarn `/metrics` first (authored on VM1124 per Inv 11) |
| D10 | 2026-07-20 | The hub2 cbre **active** jobs (`cbre-device-health`, `cbre-retention`, `cbre-robot3{,-weekly}`, `cbre-comparison`) **stay through the soak**; all retire at teardown | hub2 still ingesting during soak — keep the pipeline coherent + parity comparison live |
| D11 | 2026-07-20 | vps3 occupancy monitoring model = a single **API synthetic-probe (black-box)**, NOT exporters. **Supersedes D9** (Trevarn `/metrics` exporters). Probe = productionised `cbre_client_validation.py` model | Owner steer: "bring these together against the API rather than a separate exporter" |
| D12 | 2026-07-20 | **KEEP** the `trevarn-ingest.yml` drain metrics as a **documented white-box exception** — a leading indicator (dead-letter/spool backlog) the client-API cannot see | Owner: keep the exception and document it |
| D13 | 2026-07-20 | **Re-home the probe to vps3 now** (Trevarn component, authored on VM1124 per Inv 11) so client-reporting alerts survive the hub2 teardown | Owner: do it now; last "about-hub2" thread |
| D14 | 2026-07-20 | Per-location silent detection, **filtered to ACTIVELY-TWINNED devices only** (untwinned/inactive → no alerts). Active-twinning sourced authoritatively from the Trevarn **platform-api** | Owner: active twinning is what we care about |
| D15 | 2026-07-20 | vps3 monitoring must be **tenant × solution** aware — cover **all tenants across all Trevarn solutions**, not the single-tenant CBRE view. Copy hub2/CBRE *patterns*, apply platform-wide | Owner: Trevarn is multi-tenant |

---

## Change log

| # | Date (UTC) | Change | Path / verification | Rollback |
|---|-----------|--------|---------------------|----------|
| C1 | 2026-07-20 ~08:35 | **DM route 119 `api.microshare.eu` → disabled** | `POST /api/domains/119/disable` (hub2:8001) → HTTP 200. Verified: no `api.microshare.eu` router left in hub2 Traefik; `https://api.microshare.eu` still 302 from 51.83.77.153 (vps3). | `POST /api/domains/119/activate`; snapshot `hub2:~/dm_apiapp_snapshot_20260720.json` |
| C2 | 2026-07-20 ~08:35 | **DM route 17 `app.microshare.eu` → disabled** | `POST /api/domains/17/disable` → HTTP 200. Same verification. | `POST /api/domains/17/activate`; same snapshot |
| C3 | 2026-07-20 ~08:53 | **Retire Spacen monitoring + re-home vps3 host alerts** — rm `rules/occ-platform.yml`, `targets/occ-platform.yml`, the `occ-platform` scrape job; `rules/vps-occupancy.yml` → `rules/vps3-node.yml` (drop dead `vps_occupancy_soak` group); rename job `vps-occupancy` → `vps3-node`. | Worktree off `origin/main`, **PR #240**. `promtool` SUCCESS 2 rules; YAML valid; no dangling refs; Alertmanager clean. **DEPLOYED + verified 2026-07-20 ~09:37** — PR #240 merged (`6ba6d35`) → prod `git pull` (#232→6ba6d35) → `docker restart charliehub_prometheus` (WAL replay ~2.5s, config+rules loaded clean). Verified via container IP: prometheus healthy; `vps3_node` group live (`Vps3NodeDown`,`Vps3DiskLow`); jobs `vps3-node`+`vps3-trevarn` up; occ-platform/vps-occupancy gone. | Revert PR #240; prod `git pull` restores |
| C4 | 2026-07-20 | **`cbre-backup` cron disabled by owner** (`.disabled-20260720-hub2-retiring`) — daily 02:10 px1 `pg_dump` stopped | Ahead of teardown. ⚠ `CbreBackupStale`/`CbreBackupNeverRan` (critical) will fire ~26h out — silence during soak. Final manual `pg_dump` still required at Phase 4. | Re-enable the cron file |
| C5 | 2026-07-27 | **Retired the CT2151 pre-cutover parity pipeline.** Domain Manager record 161 was changed only from backend port 8002 to 8000 (rev 12→13), the canonical route soaked successfully, the two `-vps3` timers and port-8002 service were disabled, and fabric-only parity record 239 was disabled/deleted through ccm-domain. The obsolete units/environment were archived root-only. | `dx-occupancy.microshare.eu` now uses the authoritative port-8000 service; no ccm-domain route targets CT2151 port 8002; `cbre_reports_vps3` is frozen rollback evidence through 2026-08-10. | Restore `/root/cbre-parity-retire-20260726T2235Z`, recreate record 239 from the preserved representation if required, re-enable the parity services, and use the governed record-161 API to restore port 8002. |
| C6 | 2026-07-28 15:22 | **DM route 46 `ingest.microshare.eu` → disabled — Phase 2 complete.** Actility stopped delivering to hub2 at **15:06:59Z**; confirmed by 9 consecutive probes over 4.5 min with zero new rows in `ingest_raw`, against a prior steady ~11–12/min from 85 distinct devices. vps3 continuity verified concurrently (gateway `1000B6AD` last uplink 1 s, 57 devices heard/30 min). | `POST /api/domains/46/disable` → HTTP 200. `routes.yml` regenerated 15:22:11 with zero `ingest.microshare.eu` refs; Traefik router `ingest-microshare-eu@file` gone; `POST https://ingest.microshare.eu/uplink` → **404**. `charliehub_routing_drift_router_mismatch` showed the documented 1→0 propagation transient, then 0 across 5 consecutive samples; all other drift gauges 0. | `POST /api/domains/46/activate`; pre-change snapshot `hub2:~/dm_ingest46_snapshot_20260728.json` (revision 7, status=active) |
| C7 | 2026-07-28 15:25 | **`cbre-comparison.timer` stopped + disabled.** Its D10 rationale (*keep parity comparison live*) expired the moment the hub2 leg was cut. **Finding: the job was already dead** — `ExecStart` runs `/opt/cbre-pipeline/scripts/continuous_comparison.py`, which **does not exist**; every invocation exits in ~25 ms with `status=2/INVALIDARGUMENT`. 86 failures and 0 successes in the retained journal (earliest retained failure 2026-07-21 — the journal was vacuumed to 1 GB on 2026-07-28, so the true start is likely earlier). Any assumption that hub2↔vps3 parity was being continuously compared during the soak is unfounded. | `systemctl is-active` → inactive, `is-enabled` → disabled; removed from `timers.target.wants`. Remaining CBRE timers: `cbre-robot3`, `cbre-robot3-weekly`. | `systemctl enable --now cbre-comparison.timer` — note this restores a unit that has never successfully run |

> Note: the naive `PUT {"status":"disabled"}` was correctly rejected (`422` — `status` is
> lifecycle-immutable). The Domain Manager owns the lifecycle transition; use
> `POST /api/domains/{id}/disable` / `/activate`.

---

## Open drift to reconcile (docs, at Phase 5)

| # | Drift | Where |
|---|-------|-------|
| DF1 | `api`/`app.microshare.eu` DNS now → vps3, but the migration doc still shows `CNAME → edge-occupancy → hub2` | `operations/microshare-eu-cloudflare-migration.md` |
| DF2 | Says "occupancy cutover not yet done"; reads are cut over | `control-plane/architecture.md` (v22), `services/occupancy-pipeline-hub2/*` |
| DF3 | **Resolved 2026-07-27:** `dx-occupancy` is converged onto authoritative CT2151 port 8000; the port-8002 parity instance is retired. | `services/occupancy-pipeline-hub2/report-app-deployment.md` |
| DF4 | `occupancy.microshare.eu` → hub2 404, DNS-only, no route (dead record) | Cloudflare zone / DNS cleanup |

---

## hub2 occupancy footprint (teardown inventory)

Confirmed on hub2, 2026-07-20:

- **Containers:** `cbre_pipeline` (:18001), `cbre_postgres` (:5432), `cbre-auth` (:18003), `charliehub_cbre_probe` (9105), `cbre_postgres_exporter` (9187)
- **Filesystem:** `/opt/cbre-pipeline` (341 MB)
- **cron.d** (scripts in `/opt/cbre-pipeline/ops/`, repo `data-occupancy-eu`):
  - `cbre-backup` — 🔴 **DISABLED by owner 2026-07-20** (`.disabled-20260720-hub2-retiring`); was daily 02:10 `backup_pg_dump.sh` (px1 dump). ⚠ disabling it makes `CbreBackupStale`/`CbreBackupNeverRan` (critical) fire ~26h later — silence during soak; final manual `pg_dump` still required at teardown.
  - `cbre-device-health` — 🟢 ACTIVE hourly `check_device_health.sh` (connectivity state machine + device alerts) — **keep through soak**
  - `cbre-retention` — 🟢 ACTIVE daily 03:00 `enforce_retention.sh` — **keep through soak** (prunes; final dump before teardown)
  - `cbre-retention.bak-20260609`, `cbre-retention.disabled-bak-20260707` — inactive backups
- **systemd timers** (all → `/opt/cbre-pipeline/ops/` scripts) — **keep through soak**:
  - `cbre-robot3.timer` — hourly → R3 production aggregation (`people_counter_agg`)
  - `cbre-robot3-weekly.timer` — weekly Mon 05:00
  - `cbre-comparison.timer` — every 2h → hub2-vs-vps3 parity comparison — 🔴 **STOPPED + DISABLED 2026-07-28 (C7)**; had been failing on every run (missing script), never a live comparison
- **`.env`:** `CBRE_DATA_SOURCE_NAME`
- **Prometheus:** `cbre.yml` (30 alerts) + `cbre-probe` / `cbre_postgres_exporter` jobs remain (retire at teardown). Spacen `occ-platform.yml` / `vps-occupancy.yml` retired via **PR #240** (C3).

**Teardown (Phase 4) disables/removes ALL 3 cron.d jobs + 3 systemd timers (+ their `ops/` scripts)** alongside the containers.
- **DM routes:** `ingest`(46) **active/live** · `api`(119) disabled · `app`(17) disabled · `admin`(106) disabled · `files`(108) disabled

---

## Trevarn multi-tenant monitoring (design, D11–D15)

**Correction (D15):** vps3 monitoring must be **tenant × solution** aware — Trevarn is multi-tenant (RLS), not the single-tenant CBRE world hub2 had. Copy the hub2/CBRE *patterns*, but apply them across **all tenants × all Trevarn solutions** (occupancy, parking, contact).

**Grounding (docs read 2026-07-20):**
- Tenant registry = `platform.tenants`; **enabled solutions per tenant = `platform.tenants.settings.products`** (e.g. `['occupancy','parking']`), surfaced via `/api/v1/auth/me`.
- Solutions are schema-per-use-case (D8): `occupancy`, `parking`, contact — each its own worker.
- Active-twinning = `occupancy.device_twinning.active` per tenant (the D14 filter).
- **GAP-018**: no `GET /api/v1/tenants` endpoint → discovery is blind; must be built (closes the gap). Enumeration must satisfy **I-ACT-LIST-RECONCILE-1** (deterministic + count-reconciled).
- **GAP-017**: platform-api is loopback-only (`127.0.0.1:18000`) → the probe must run **on vps3** (reinforces D13).

**Model:** one black-box **health probe on vps3** (D11) that (1) discovers tenants + their `settings.products`, (2) per `(tenant, solution)` runs the solution-appropriate API health check, (3) emits `{tenant, solution}`-labelled metrics → alerts fire **per-tenant-per-solution**.

| Solution | Per-tenant health (via vps3 API) | Silence filter |
|---|---|---|
| occupancy | client-visible freshness (latest agg/hourly bucket age), per-location silence | **actively-twinned only** (D14) |
| parking | actuation/downlink delivery (queue stuck, dead actuations), sensor freshness, space-state staleness | active spaces/sensors only |
| contact | egress delivery to tenant queue, scan/detection freshness | active study only |

**Platform-scoped (not per-tenant):** ingest drain (`trevarn-ingest.yml` — D12 white-box exception; shared ingest) + vps3 host (`vps3-node.yml`).

**Build dependencies (authored on VM1124 per Inv 11):**
1. `GET /api/v1/tenants` (platform_admin) — discovery; **closes GAP-018**; I-ACT-LIST-RECONCILE-1.
2. Per-solution health endpoints where missing (occupancy freshness+coverage per tenant; parking delivery/sensor health; contact egress) — some exist (`/api/v1/ops/gateways/health`), some to add.
3. `trevarn-health-probe` container (discover → probe → emit labelled metrics), runs on vps3.
4. Prometheus scrape + `{tenant,solution}`-labelled alert rules.

**Retire (AFTER vps3 coverage live + validated):** all `cbre.yml`, `cbre_postgres_exporter`, and the D9 "add Trevarn /metrics exporters" plan (superseded).

## Phase plan & status

| Phase | Action | Status |
|-------|--------|--------|
| 1 | Cut hub2 Actility ingest leg (ThingPark API; disable the AS-routing connection → `ingest.microshare.eu`) | ✅ **done 2026-07-28** — last uplink 15:06:59Z |
| 2 | Reconcile dead DM routes: `api`(119) ✅, `app`(17) ✅, `ingest`(46) ✅ | ✅ **done 2026-07-28** (C6) |
| 3 | Soak (suggest 7 days, aligns with `MICROSHARE-WILDCARD-RETIREMENT-1`), monitor vps3 ingest continuity + `cbre_client_validation.py` | 🟢 **running from 2026-07-28 15:22** — earliest teardown 2026-08-04 |
| 4 | Archive (`pg_dumpall` cbre_postgres + config → px3-nas + px5) then remove containers/volumes/networks, cron, prometheus, `.env`, `/opt/cbre-pipeline`, `occupancy.microshare.eu` DNS | ⏳ |
| 5 | Doc/authority reconciliation (DF1–DF4; `occupancy-pipeline-hub2/*` → ARCHIVED; hub2 → zero customer workloads) | ⏳ |

---

## See also

- [Occupancy Data Pipeline (hub2)](../services/occupancy-pipeline-hub2/architecture.md) — the stack being retired
- [Smart Parking (decommissioned on hub2)](../services/parking.md) — decommission precedent
- [microshare.eu → Cloudflare](microshare-eu-cloudflare-migration.md) — DNS zone
- [CharlieHub Architecture](../control-plane/architecture.md) — hub2 (control plane) vs vps3 (customer platform)