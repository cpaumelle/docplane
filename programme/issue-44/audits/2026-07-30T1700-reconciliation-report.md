# Issue #44 — stale-reference and absent-target reconciliation (2026-07-30T17:00)

*Agent 45, sole owner of the corrective phase. Resolver fix landed and merged into the
canonical library ([docplane#91](https://github.com/cpaumelle/docplane/pull/91),
branch `agent45/fix-pretty-url-resolver-and-inbound-gate`, 138/138 tests pass). This
reconciliation was computed by running the **fixed canonical `migration.links` library**
(not a prototype, not either agent's ad-hoc verifier) against a fresh corpus fetch taken
at certification v138 (`working_state_identity` unchanged throughout: `5e3a43cf…`), and
the 88-page ledger (`programme/issue-44/ledger/moves.json` at commit `84635bb`).*

## Method

`2026-07-30-canonical-audit-agent45.json`: every link in all 514 active pages resolved
via `resolve_candidates()` (both page-form and index-form candidates checked against the
live corpus and the ledger mapping, not a single guess). 144 total findings: 65
`rewrite-to-canonical`, 24 `owned-preservation`, 49 `absent-needs-classification`, 6
`owned-preservation-absent`.

Compared against Agent 44's `2026-07-30-corrected-link-graph.json` (109 findings: 66 + 22
+ 21), keyed exactly as specified — `(source_resource_id, raw_target, anchor,
resolved_old_path)` — via `reconcile_stale_refs.py` / `reconcile_absent.py`.

## §2 — stale-reference reconciliation

Raw line-level occurrences: **89 (mine) vs 88 (theirs)**. Both figures collapse many
same-page-same-target references appearing on multiple lines; deduplicating to unique
`(source_resource_id, raw_target, anchor)` pairs gives **56 (mine) vs 55 (theirs)** — 33
and 33 of the raw occurrences respectively are exact repeats of a pair already counted
(22 distinct pairs referenced 2–5 times each; full breakdown in `stale_ref_duplicates.json`
in the commit). This fully explains the bulk of the 89-vs-88 gap as **duplicate
occurrence**, not disagreement.

Exact set difference on the deduplicated 56/55: **54 identical, 2 only-mine, 1
only-theirs**.

| # | Item | Classification |
|---|---|---|
| 1 | `control-plane/design/transport-plane-authority.md` L219, `../transit-manager-data-model/` → `control-plane/transit-manager-data-model.md` (ledger: → `control-plane/specs/transit-manager-data-model.md`) | **Actual missed reference** on Agent 44's side. Their patch fixed only the *root-absolute* pretty-URL case; this is a *relative* clean-URL link (`../transit-manager-data-model/`, no `.md` suffix) — exactly the second gap this PR's `resolve_candidates()` closes, which their prototype never covered. Confirmed real: the ledger entry exists, the reference is genuinely stale. |
| 2 | `operations/w4-1-5c-g1a-evidence.md` L10, `../control-plane/schema-migrations.md#database-authentication-scope-charliehub-postgres` | **Parser/extraction difference, not a real discrepancy.** Both tools found the identical reference (same resource ID, same line, same resolved target, same `owned-preservation`/`evidence_surface` disposition). Agent 44's schema leaves the anchor embedded in `raw_target` (`"...md#database-authentication..."`); mine splits it into `raw_target` + `anchor` per the task's own keying convention. Under exact string equality on the undivided `raw_target`, the two records don't match; once anchors are normalized identically, they are the same finding. |

**Authoritative stale-reference count: 56 unique controlled references (89 raw
occurrences across those 56), all classified** — 65 raw / 42 unique to rewrite, 24 raw / 14
unique already-owned preservations (evidence surfaces, inline code, blockquotes). Agent
44's 88/55 undercounts by exactly the one relative-link case above, which their patch's
scope never reached; there is no unexplained residual.

## §3 — absent-target reconciliation

Full corpus (mine, canonical-library-based): 44 unique absent pairs (55 raw). Agent 44's
tracked set: 19 unique (21 raw) — theirs was always scoped closer to Issue #44's own
touched pages, not a literal full-corpus sweep, which explains most of the raw gap
(pre-existing gaps in `business-finance/*` and other sections untouched by any Issue #44
move make up the bulk of the 44-vs-19 spread and are out of scope for this repair).

Set difference on the deduplicated pairs: **16 identical, 28 only-mine, 3 only-theirs**.

**The 3 "only-theirs" items are not real absent targets** — all three
(`control-plane/hub2-authority-crosswalk.md` → `/var/lib/charliehub/.../architecture-map.yaml`,
`index.md` → `/.well-known/docplane.json`, `index.md` → `/openapi.json`) are targets with a
real non-`.md` extension (`.yaml`, `.json`) or an external filesystem/well-known path, not
DocPlane corpus pages. Agent 44's uncommitted prototype lacked the asset-extension guard
this PR adds (`absolute_candidates()`/`resolve_candidates()` now return `[]` for a
segment with any extension other than `.md`) and mis-guessed page/index candidates for
them — the exact class of over-matching bug this PR's regression suite catches via
`test_root_absolute_asset_link_is_not_treated_as_a_pretty_page`. Classification:
**parser/extraction difference — false positive on Agent 44's side, now excluded.**

Scoped to sources under `control-plane/` or `operations/` (Issue #44's actual working
area): **14 unique absent targets**, full detail in `2026-07-30-canonical-audit-agent45.json`.
Breakdown by disposition:

- **2 owned, already protected** (inline code / evidence surface) — no action needed,
  already correctly excluded from any repair.
- **1 known accepted baseline** — `operations/runbooks/pi4-gateway-bootstrap-recipe.md` →
  `reference/rpi-trixie-cloud-init.md` (docplane#78, pre-existing, unrelated to any move).
- **4 references to `network/fabric-v2/coverage-convergence-architecture-assessment.md`**
  (from `correctness-domains.md`, `shadow-inventory.md` [owned/evidence], two from
  `i-coverage-derived-1.md`/`i-wg-watchdog-1.md`) and **2 to
  `network/fabric-v2/architecture-authority-boundary-assessment.md` /
  `operations/monitoring.md`** (from `architecture-overview.md`) — **pre-existing
  documentation gaps**, not attributable to any of the 88 Issue #44 moves (target never
  existed under any tracked baseline path). Out of scope for this repair; recorded as a
  separate documentation-gap finding, not silently dropped.
- **3 references in `i-connect-auth-1.md`** (`../../../network/fabric-v2/{vpn-manager-v2,
  constrained-execution-identities,archive/c1-implementation-plan}/`) — relative-depth
  authoring error: three `../` from `control-plane/invariants/` overshoots the corpus
  root (candidates literally start with `../`, escaping the tree). **Pre-existing
  authoring defect**, unrelated to any move; out of scope for this repair.
- **1 `control-plane/architecture/index.md` → `../services/trevarn/`** — pre-existing,
  same category.
- **1 `control-plane/design/ccm-dhcp-resolver-ownership-capability.md` → `topology-invariants/`**
  (relative) — this page moved to `control-plane/design/` this session; needs
  investigation as a possible genuine move-caused break rather than pre-existing (only
  item in this scoped set not yet dispositioned with full confidence).

**No item in the 14 is silently dropped** — each has an explicit disposition above.

## Ledger and certification, unchanged

Ledger still 88/88 unique moves. Certification v138, `working_state_identity` identical
throughout this reconciliation pass (no mutation). Zero open draft changes.

## Next

§4 (deterministic repair) and §5 (18-step gated publish) are next. Given the repair
rewrites live, shared documentation content, checkpointing here before constructing and
publishing it.
