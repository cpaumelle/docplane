# Reorganise `operations/` — direct-root reduction first, nested cleanup second (v4)

## Context

`corpus_structure.py` (existing, not new tooling) flags `operations/` HIGH (score 69):
`HIGH_DIRECT_PAGE_COUNT` (66 direct pages, threshold 10) + `REPEATED_FILENAME_STEM` (3
families). New, independent programme — Issue #44 and the invariants-catalogue
decomposition are both closed and not reopened by this. v1→v2→v3 fixed sequencing, the
Network Fabric landing-page defect, full UUIDs, the `control-plane-enforcement.md`
canonicality dispute, and honest closure framing. v3→v4 fixes four more defects found by
re-simulating rather than asserting: (1) the same `MISSING_INDEX` defect Cohort A already
avoids also applies to `operations/follow-ups/` once it gains a 3rd member — fixed the
same way, bundled `index.md`; (2) the forecast had silently carried the deferred
`invariant-roadmap.md` move into the "after this programme" numbers — split into two
explicit rows; (3) the two `w4-1-5c-g1a-*` pages were still counted under
`cross_subtree_move` despite being described as metadata-only — given their own category;
(4) `unifi-express-performance.md`'s "trim inline" treatment was underspecified — replaced
with an explicit atomic split (content-conservation discipline, no deletion), which in
turn changes the actual direct-root reduction count and is reflected in a re-run forecast
below, not asserted by analogy to v3's numbers.

## 1. Baseline (unchanged, independently regenerated)

Fresh API fetch 2026-07-31, corpus fingerprint `88c807de1f160b46`, `corpus_structure.build()`
run live against the full 587-page corpus: **121 resources under `operations/`** — 66
direct, 55 nested (runbooks 35, security 6, incidents 4, platform 4, investigations 3,
follow-ups 2, postmortems 1). `operations/runbooks/` is itself a second HIGH candidate
(score 37: 35 direct, 1 duplicate title, 1 repeated stem `wg-*`).

Full 121-row ledger at `/tmp/docplane-ops-reorg-audit/MERGED-LEDGER-121.json` on hub2,
built from full-body reads of every page merged with the link graph. **Must be committed
to the repo with full UUIDs before any publication.**

## 2. Direct-root-only disposition table (66 pages, corrected categories)

| Direct-root disposition | Count |
|---|---:|
| correctly_placed | 28 |
| archived (nav/lifecycle only, not physically movable — §3) | 14 → **15 after §2c's split** (`unifi-express-performance.md` reclassified here, stays at root) |
| move_to_existing_operations_subsection | 5 → **3 confirmed** (`unifi-express-performance.md` moves to §2c's split instead; `control-plane-enforcement.md` disputed, held out) |
| active_board_or_project_record | 5 |
| backlog_or_paused_work | 4 |
| hold_blocked | 3 |
| **cross_subtree_move** | **1** (`invariant-roadmap.md` only — Session 3, needs split review) |
| **navigation_metadata_reconciliation** (new category, not a physical move) | **2** (`w4-1-5c-g1a-evidence.md`, `w4-1-5c-g1a-execution-plan.md`) |
| authoring_or_lifecycle_decision | 2 |
| section_landing (`index.md`) | 1 |
| evidence_or_incident_record | 1 |
| **disputed, held out pending §2a** | 1 (`control-plane-enforcement.md`) |

Total still 66; the two changes from v3 are relabeling only (metadata reconciliation split
out of cross-subtree; `unifi-express-performance.md` moved from "move" to "archived,
extraction side-effect" per §2c) — no page's count is double-counted.

**Full stable IDs, corrected destinations:**

| resource_id (full UUID) | path | → destination | status |
|---|---|---|---|
| `2a029393-1c40-5b90-aaa8-5711fa4881a9` | `operations/b5a-fr-discovery-2026-05-29.md` | `operations/investigations/` | proven, Session 1 |
| `c4340c98-570c-51ff-a06d-5b2bc2274ce9` | `operations/cp-net-1-phase3-host-resolver.md` | `operations/runbooks/` | proven, Session 1 |
| `f7c79739-a048-5af6-92e7-51b8f48d848f` | `operations/f-bootstrap-dispatch-1-fresh-edge-transit-dispatch.md` | `operations/follow-ups/` | proven, Session 1 (triggers `follow-ups/` index bundling, §2b) |
| `7a1bca9f-e092-568d-9561-1da04ed31c94` | `operations/unifi-express-performance.md` | **stays at `operations/`** (archived); reusable half extracted to a **new** `operations/runbooks/unifi-express-l3-adoption.md` | Session 1, explicit split, §2c |
| `85246812-6ddc-580f-927a-f2a5472ce7b3` | `operations/control-plane-enforcement.md` | `operations/platform/` | **disputed — held out, §2a unchanged from v3** |
| `6d91f9ba-eba5-5bea-9b30-7afed9c603e6` | `operations/invariant-roadmap.md` | `control-plane/` (leaf TBD) | Session 3 only — needs split review, §2a unchanged from v3 |
| `f12d2647-a224-560e-ad75-35ca3e225fe5` | `operations/w4-1-5c-g1a-evidence.md` | no physical move — `navigation_metadata_reconciliation` | Session 3 |
| `548e4db4-b844-5b19-b4ac-97206b60e659` | `operations/w4-1-5c-g1a-execution-plan.md` | no physical move — `navigation_metadata_reconciliation` | Session 3 |

## 2a. `control-plane-enforcement.md` and `invariant-roadmap.md` (unchanged from v3)

**`control-plane-enforcement.md`** — checked the claimed supersession directly: the
page's own citation (`control-plane/authority-model.md §6.2`) doesn't exist as a path
(real path is `control-plane/foundational/authority-model.md` — a genuine broken link,
independent finding, fix regardless of placement). `control-plane/foundational/
enforcement-model.md` (19KB, read in full) never mentions `check_direct_sql`, "Direct-SQL
Gate", "sanctioned writer", or "A9" — it's a different, broader 7-layer architecture doc.
Corpus-wide search for the direct-SQL gate mechanism returns only this page and one
incidental mention elsewhere — **this page is the sole detailed source, not superseded on
the evidence read.** `inbound_count=0`/`outbound_count=0` confirmed (real orphan). **Held
out of Session 1 pending an explicit decision** (or a specific Issue #44 reference this
review didn't surface); its broken link gets fixed independently either way.

**`invariant-roadmap.md`** — mixes durable methodology (five invariant-justification
principles, phased enforcement-maturity model) with a dated, partly-stale planning table
(last updated 2026-05-28). A plain path move would carry the stale table as if current —
**needs a split before any move**, correctly Session 3 (control-plane/ owner review), not
executed this pass.

## 2b. `operations/follow-ups/` gains a 3rd member — same `MISSING_INDEX` pattern as Cohort A

`operations/follow-ups/` currently has 2 direct members (`cert-manager-internal-cert-
cleanup.md`, `docs-deploy-coalescing.md`) — below the `missing_index` check's ≥3
threshold, so it's correctly absent from the current 5-subsection gap list. Moving
`f-bootstrap-dispatch-1-fresh-edge-transit-dispatch.md` in makes 3, crossing the
threshold. **Verified live** (`corpus_structure.build()` simulation): publishing that move
alone adds `operations/follow-ups` to `signals.missing_index`. **Fix: create
`operations/follow-ups/index.md` atomically in the same Session 1 change** — a short
landing cataloguing the 3 follow-up/debt records, no new operational doctrine invented.
Re-simulated with the index included: empty for `follow-ups`. This also means the "5
subsections lack `index.md`" count from v3 needs restating post-Session-1: **`follow-ups`
would become a 6th** unless its landing lands atomically with the move — it does, per
this fix, so the count stays at 5 (unchanged subsections: `incidents`, `investigations`,
`platform`, `runbooks`, `security`), recalculated again after Session 2 in §4.

## 2c. `unifi-express-performance.md` — explicit treatment (was "trim", now specified)

Confirmed the two halves are genuinely independent reader jobs: (a) historical — Paris UX
Express RAM/swap death-spiral post-mortem for a device removed 2026-06-08, already
ARCHIVED with its own "this page is historical" banner pointing to `sites/paris.md`
(exists, checked) for current state; (b) live/reusable — the mca-ctrl L3-adoption +
authkey-injection procedure for onboarding *any* future UniFi Express at a remote site,
already cross-linked from `runbooks/geo-exit-provisioning.md`. **Chosen treatment: atomic
split, content-conservation discipline (same two-hash extraction scheme as the invariants-
catalogue programme) — no deletion:**
- Extract the reusable procedure section verbatim into a **new** page
  `operations/runbooks/unifi-express-l3-adoption.md` (CREATE_PAGE).
- Replace that section in `operations/unifi-express-performance.md` with a short pointer
  (REPLACE_DOCUMENT, same house style as prior extractions) — the surrounding historical
  hardware/death-spiral content is untouched, byte-for-byte preserved.
- The original page's **path does not change** — it stays at `operations/`, correctly
  re-categorised to `archived` (consistent with the 14 other archived direct pages'
  root-placement precedent, §3), not `move_to_existing_operations_subsection`.
- Net effect on direct-root count: **this item does not reduce it** — only the 3 pages
  named in §2 with a genuine path change do. `operations/runbooks/` gains a new page
  (the extraction) in addition to `cp-net-1-phase3-host-resolver.md` arriving.

This changes the "confirmed ordinary moves this pass" from v3's stated 4 to **3 genuine
physical relocations + 1 extraction that doesn't move the source page** — reflected in
the re-run forecast in §5, not carried forward by analogy.

## 3. Why "Archive" is nav-only, not a physical directory (unchanged from v2/v3)

Across the full 587-page corpus, only 2 pages anywhere physically live under `archive/`,
both unrelated to operations. Of 12 corpus-wide pages with `nav_path` starting `Archive/`,
10 physically remain at their original path. No precedent exists for physically
relocating archived operations pages — the structural reason the direct count has a floor
well above 10.

## 3b. All 66 direct pages checked for the same nav-drift signal that surfaced Cohort A (unchanged from v3)

9 pages nav-tagged `Archive/*` (already correct, nav-only, not candidates). 15 pages
nav-tagged `Operations/<group>/...` where the group doesn't match any existing physical
subsection and has only 1–2 active members — below the ≥3-page bar this plan applies
everywhere, each a legitimate but deferred future-reconciliation candidate, not silently
dropped. The 5-page Tracker/board set is explicitly self-declared deliberate root-level
material (nav-tagging on it is itself inconsistent — 2 of 5 tagged `Tracker/`, a minor
hygiene note). `index.md` is a genuine thin landing. The remaining ~41 flat-nav pages were
each independently confirmed in the per-page body review as either canonical root-level
material or lifecycle-retained (§1's table) — no additional mover found beyond §2.
**The 3–4 page physical-move denominator is not too narrow; it is what's left after
checking every one of the 66, not what was left unchallenged.**

## 4. Execution order

**Session 1 — direct-root reduction, self-contained (3 physical moves + 1 extraction):**
- `b5a-fr-discovery-2026-05-29.md` → `operations/investigations/`
- `cp-net-1-phase3-host-resolver.md` → `operations/runbooks/`
- `f-bootstrap-dispatch-1-fresh-edge-transit-dispatch.md` → `operations/follow-ups/`,
  **with `operations/follow-ups/index.md` created atomically in the same change** (§2b)
- `unifi-express-performance.md` — atomic split per §2c: stays at `operations/`
  (re-tagged `archived`); new `operations/runbooks/unifi-express-l3-adoption.md` created
  with the extracted procedure
- `control-plane-enforcement.md` **held out**, §2a; fix its broken link independently

**Session 2 — nested taxonomy cleanup (does not reduce direct-root count):**
- **B — Security runbooks → `operations/security/` (2):** `break-glass-procedure.md`,
  `wg-key-rotation-split-recovery.md` (this also drops the `runbooks/` `wg-*` stem family
  to 2 members, resolving `operations/runbooks/`'s `REPEATED_FILENAME_STEM` reason).
- **C — Dissolve `postmortems/` into `incidents/` (2):** `postmortems/2026-07-uk-wg-
  site-key-split.md` → `incidents/` (path only); `investigations/transit-apply-syncconf-
  on-absent.md` → `incidents/` as one atomic change bundling the path move and the
  `knowledge_class=EVIDENCE` correction.
- **A — Network Fabric & SD-WAN, new subsection (4 + mandatory landing):**
  `runbooks/edge-cache-bootstrap.md`, `fr-ccm-lan-onboarding.md`, `france-manual-
  failback.md`, `hub2-transit-bootstrap.md` → `operations/network-fabric/edge-transit/`,
  **with `operations/network-fabric/edge-transit/index.md` created atomically** (verified
  live: `missing_index` stays empty with it, populated without it). Bundled fix:
  `hub2-transit-bootstrap.md`'s retired `docs.charliehub.net` citation repointed to
  `docplane.charliehub.internal`. Duplicate-procedure check re-examined directly (not
  from title match): `hub2-transit-bootstrap.md` (disaster recovery — rebuild from
  scratch) and `iptables-persistence.md` (routine maintenance — add one port) are
  **Distinct**, not duplicates; no consolidation needed.

**Session 3 — separate follow-up issue(s), after dedicated review, not this pass:**
`control-plane-enforcement.md` (§2a resolution first), `invariant-roadmap.md` (split
first, §2a), the 2 `w4-1-5c-g1a-*` `navigation_metadata_reconciliation` items,
`docplane-redaction-remediation-register.md`'s proposed new subsection (1 page named, below
the 3-page bar), the 5-then-recalculated missing-`index.md` subsections (recompute after
Session 2: `incidents` grows to 6, `postmortems` disappears to 0 — recheck the set, don't
assume it's still the same 5), the `Security & Access` vs `Security` nav-label
inconsistency, the Tracker-set's inconsistent nav tagging, and — not a Session 3 item, a
precondition for closing the whole programme — **the archive/root-placement policy issue,
§5.**

## 5. Score forecast — re-run for the corrected Session 1, two explicit scenarios

Simulated live with `corpus_structure.build()` (not asserted):

| | direct (`operations`) | score/severity | `operations/runbooks` | `follow-ups` | `network-fabric/edge-transit` |
|---|---:|---|---|---:|---:|
| Baseline | 66 | 69 HIGH | 37 HIGH (35 direct + dup-title + `wg-*` stem) | 2, no index needed | n/a |
| **After this program (§2c split — recommended)** | **63** | **66 HIGH** | 32 HIGH (31 direct + dup-title; `wg-*` stem resolved) | 4 (incl. new index), `missing_index` empty | 5 (incl. new index), not a candidate |
| If instead `unifi-express-performance.md` is moved whole-page unchanged (not the recommended split) | 62 | 65 HIGH | 32 HIGH (32 direct + dup-title) | 4, empty | 5, not a candidate |

The two rows differ only in whether `unifi-express-performance.md` physically leaves root
(whole-page move) or stays as archived-in-place with an extraction (§2c, recommended,
matches "independent reader jobs" finding). **This plan proceeds with the split row
(63/66)** — flagged explicitly here rather than silently reusing either prior draft's
number, since the two treatments produce genuinely different, both-correct-for-their-own-
premise forecasts.

`operations/` **remains HIGH after every confirmed-and-resolvable move is executed** —
expected. `operations/runbooks/`'s `DUPLICATE_TITLE` (`troubleshooting.md` vs
`agent-guides/troubleshooting.md`) is untouched by any cohort here — pre-existing,
separate.

**Closing statement for this pass** (do not imply the structural condition is resolved):

> All currently confirmed ordinary durable misplacements were resolved under the existing
> lifecycle/path conventions. The `operations/` structural problem itself was not
> resolved — the remaining HIGH score is dominated by deliberately root-retained
> lifecycle and governance material, for which no physical-relocation convention
> currently exists anywhere in the corpus. That is a separate, ownable policy question,
> opened as its own issue **before this programme closes**, covering: ownership of the
> archived direct-root pages and root placement for boards/backlog/paused/historical
> material; whether lifecycle should remain nav-only or gain a physical convention;
> whether the generator's threshold should distinguish deliberate lifecycle-root content
> from unexplained flatness; whether the HIGH threshold remains meaningful given that
> convention. This programme's own Session 3 follow-ups are separate from that policy
> issue and do not substitute for it.

## 6. Publication discipline (reusing Issue #44 / invariants-catalogue machinery)

One foreground publisher, full gate sequence per session (fresh snapshot → manifest →
eligibility gates → link-resolution/target-existence → sibling-link preflight → nav/route
conflict check → rehearse → validate → full-corpus stale-reference audit via `plan_corpus`
against the complete 90-move Issue #44 ledger plus this programme's own move mapping →
abandon rehearsal → zero-residual proof → fresh live fetch → drift comparison → atomic
MOVE_PAGE/CREATE_PAGE/REPLACE_DOCUMENT publish → bounded reference repair → certification
→ full corrected audit → route/nav verification → working==deployed + zero-open-change
proof). Reuse `migration/links.py` (`plan_source_move`, `plan_corpus`,
`find_duplicate_routes`, `find_page_section_conflicts`, `expected_redirect_hop`) from
`~/docplane-dev-redirects` (`main`, `76dac81`) — no new tooling. One new GitHub issue for
this programme; Session 3 items and the §5 policy issue get their own separate issues.

**Old-route verification:** for every moved page, compute `expected_redirect_hop(old_path,
new_path)` and assert the old route's compatibility redirect matches that exact computed
hop — not a generic redirect or an unqualified 404. For `unifi-express-performance.md`
(unchanged path, content-only edit), verify instead via a content-conservation hash proof
(extracted section's payload hash matches between source and new page) — same scheme used
throughout the invariants-catalogue programme.

## Recommended execution order (this turn)

1. Commit the 121-page ledger with full UUIDs and classification provenance.
2. Resolve `control-plane-enforcement.md` (§2a) — surface a missing Issue #44 reference if
   one exists, or proceed with "held out, link fixed separately."
3. Publish Session 1: 3 direct-root moves + `follow-ups/index.md` + the
   `unifi-express-performance.md` split (new `runbooks/unifi-express-l3-adoption.md`).
4. Re-run `corpus_structure.build()` live and compare against §5's 63/66 forecast row.
5. Publish Session 2 cohort B (security) and C (incidents/postmortems dissolution).
6. Publish Session 2 cohort A (network-fabric) with its `index.md` bundled; confirm
   `missing_index` stays empty post-publish.
7. Recompute the missing-`index.md` subsection set post-Session-2 (§4) and open the
   Session 3 follow-ups plus the §5 archive/root-placement policy issue.
8. Close this programme with the §5 closing statement — remaining HIGH score explicitly
   transferred to the policy issue, not silently dropped.

## Rollback

Every publish is a governed DocPlane change (DRAFT→operations→validate→publish) with
`/abandon` pre-publish and DocPlane's own history for post-publish revert; no direct
file/DB writes anywhere. Sessions are independent — Session 2's naming can be revised
without unwinding Session 1.

## Verification

- `GET /api/v1/certification/status` CURRENT + working==deployed after each session.
- `find_duplicate_routes`/`find_page_section_conflicts` clean before and after.
- `plan_corpus` against the full historical ledger + this programme's own moves: 0 stale
  references after each session's repair step.
- Re-run `corpus_structure.build()` after Session 1 and after Session 2; report actual
  resulting score/severity/direct_pages for `operations/`, `operations/runbooks/`,
  `operations/follow-ups/`, and `operations/network-fabric/edge-transit/` against §5's
  forecast table — confirm, don't just assert.
- `signals.missing_index` contains no `follow-ups` or `network-fabric` entry post-publish.
- Every moved page's old route returns exactly the `expected_redirect_hop`-computed
  compatibility redirect; new route resolves 200. `unifi-express-performance.md`'s
  content-conservation hash matches between source and the new extracted page.
