# Dinard TV GEO + managed-consumer doc corrections

**Status:** APPLIED
**Legacy page(s):** `operations/managed-consumers-migration-plan.md` · `sites/dinard-fr-site.md` ·
`operations/engineering-backlog.md` · `network/fabric-v2/address-plan.md` ·
`network/geo-execution.md` · `services/various/fire-tv-setup.md`
**Raised:** 2026-07-27 (unattributed — migrated from `hub2:~/docs-staging/README-dinard-tv-geo-2026-07-27.md` on 2026-07-28)

---

## What is wrong

Two classes of problem: one load-bearing architectural claim that was retired but never removed,
and a set of factual addressing errors.

### The load-bearing one — the GEO override does NOT follow a relocated device

`managed-consumers-migration-plan.md` (principle 5, "Runtime implications") and
`address-plan.md` (the managed-consumer authority-chain diagram) both presented
`tv_background.lan_sync` polling LAN Watcher and firing `apply_exit(new_ip)` + `clear_exit(old_ip)`
as **live architecture**, stating "no operator action required".

That mechanism was retired with `tv_devices` in **sdwan-v4.5 E3 (2026-07)**. The migration plan
even carries an E3 danger banner saying so — but the principles and runtime sections below it were
never rewritten, so **the page asserted both things at once**.

`transit.routing_overrides` is keyed on **source IP**. Relocating a device therefore strands its
override on the old address and it **silently** loses its geo: no error, and
`GET /api/transit/exit/lan-device/<old-ip>` still cheerfully reports the intended geo. Anyone
following the page as written would have hit this.

Added `### Sequencing a per-device move` (pre-stage the override on the destination IP → relocate →
deploy dnsmasq → deploy transit → force renewal → verify → delete the old row), linked from 4
places across 3 pages. This is the order actually used for the Dinard pair, which had no coverage
gap.

### Factual corrections

- `10.35.1.118` ("Dinard TV") appeared in the migration inventory, the rollout sequencing table,
  `dinard-fr-site.md`, `geo-execution.md` and `fire-tv-setup.md`. **It was never a live address.**
  The real devices are `firetv-dinard` `10.35.1.10` (`20:be:b8:2d:cf:85`) and the Apple TV
  `fr-apple-unknown` `10.35.1.180` (`28:ff:3c:99:79:fd`), now reserved at `10.35.4.10` / `.11`.
- `fire-tv-setup.md` listed Fire TV Dinard's geo as `fr`. Its actual `routing_overrides` row is
  **`uk`**. Corrected — this one was wrong about *behaviour*, not just addressing.
- FR1/Dinard GEO enforcement was "Not yet verified (no TV on .4.x yet)". Now proven: Apple TV
  Dinard `geo=us` egressed `98.115.202.204` (UCG Rock), confirmed on-device — first FR-site LAN
  consumer on the central US path.
- `engineering-backlog.md` — `CCM-LAN-DEPLOY-SCOPE-1` updated with fix status: symptom cleared by
  setting CCM-LAN scope `ad/1` to `draft`; code fix open as PR #332.

## What it should say — already written, needs carrying across

All six corrections **were written to legacy docs-api and are intact**. Verified 2026-07-28: every
page is still at exactly the recorded version, so nothing has overwritten them.

| Legacy page | Version | Written (UTC) | Verified still current |
|---|---|---|---|
| `operations/managed-consumers-migration-plan.md` | v21 | 2026-07-27 13:14 | ✅ 2026-07-28 (v21, 49,423 ch) |
| `sites/dinard-fr-site.md` | v8 | 2026-07-27 13:14 | ✅ 2026-07-28 (v8, 11,110 ch) |
| `operations/engineering-backlog.md` | v40 | 2026-07-27 13:15 | ✅ 2026-07-28 (v40, 79,944 ch) |
| `network/fabric-v2/address-plan.md` | v32 | 2026-07-27 13:17 | ✅ 2026-07-28 (v32, 47,893 ch) |
| `network/geo-execution.md` | v11 | 2026-07-27 13:17 | ✅ 2026-07-28 (v11, 7,287 ch) |
| `services/various/fire-tv-setup.md` | v14 | 2026-07-27 13:17 | ✅ 2026-07-28 (v14, 18,094 ch) |

`GET /api/docs/lint` was clean after every write (506 pages, `errors: []`).

## Root cause of the non-render (why these are invisible)

**`/opt/charliehub/docs-api/migrations/003_redirect_register.sql` was never applied.**

    docs schema tables: audit_log, meta, page_versions, pages, sections
    docs.redirects:     absent in every schema

`003_redirect_register.sql` exists in the repo and on disk, and
`POST /api/docs/pages/{path}/add-redirect` is a live endpoint against a table that does not exist.
A `schema_migrations` ledger exists; the `docs` schema contains none of the tables 001–009 would
create, so the whole series looks unapplied. `DOCS_DEPLOYMENT_MODE=legacy` appears to have been set
to work around this — it restored the **write** path, but the legacy render path still queries
`docs.redirects`, so **writes succeed with HTTP 200 while rendering fails every ~30 s**.

This is the **"Migration ledger integrity (ordered-migration holes)"** P1 class already in the
backlog, with a live casualty. Same root cause breaks `GET /api/docs/anchors/check` (500).

Written up as `DEFECT-docs-legacy-render-missing-redirects-table.md` in charliehub-hub2 (#334).
**Not fixed** — applying migrations is a schema change, and a `docplane` stack was started
2026-07-27 ~12:30; applying 003 underneath an in-flight cutover could collide. Needs an owner
decision: apply 003 (+ possibly 004–009) to the legacy stack, or let the DocPlane cutover supersede
the legacy renderer.

## Side effect worth knowing

Every page fetched through `read_doc` / the docs MCP reports a `last_updated` that is the **frozen
render timestamp**, identical across the whole corpus, rather than the page's own `updated_at`.
While rendering is down, do not trust `last_updated` from the MCP to tell you whether a page is
current — query `docs.pages.updated_at` or the API's `version` instead.

*(Confirmed still true 2026-07-28: the whole corpus reported `2026-07-27T13:45:44Z`, a newer freeze
point than the `2026-07-25T18:10` noted when this record was written — so the render has re-frozen
at least once since.)*

## How to apply on DocPlane

1. Check the imported version of each page against the table above. **≥ recorded version** → the
   correction came across; mark APPLIED.
2. **Older** → the import missed it. Content is still live in legacy docs-api — re-fetch rather
   than reconstructing from this file.
3. No snapshots stored here on purpose (these are large, actively-edited pages — `engineering-backlog.md`
   alone is 80 KB — and a copy would go stale). **If legacy docs-api is scheduled for decommission
   while this entry is still PENDING, export snapshots first.**
4. Priority order if applying selectively: the **`managed-consumers-migration-plan.md` + `address-plan.md`**
   pair first — those carry the retired-mechanism claim that actively misleads an operator into
   stranding a device's geo.


## Applied — 2026-07-29

**Status: APPLIED (no republish needed — corrections were imported by the migration).** All six pages are live on DocPlane at exactly the recorded versions:

| Page | Version on DocPlane |
|---|---|
| operations/managed-consumers-migration-plan.md | v21 |
| sites/dinard-fr-site.md | v8 |
| operations/engineering-backlog.md | v40 |
| network/fabric-v2/address-plan.md | v32 |
| network/geo-execution.md | v11 |
| services/various/fire-tv-setup.md | v14 |

certification CURRENT, working == deployed.
