# Issue #44 — Current Status

*Generated 2026-07-30T12:40:00Z by Agent 45 from `ledger/`, `baseline/` and
`audits/2026-07-30T1240-ledger-audit.json`. Generated file — do not hand-edit.*

## Historical execution (receipt-backed, independently confirmed)

| Metric | Value |
|---|---|
| Move operations | 54 |
| Unique pages moved | 54 (0 moved more than once) |
| Move-bearing publications | 21 |
| Pages resolved without movement (metadata classification only) | 11 |
| Pages touched by Issue #44 | 65 |
| Rewrite-only publications | 24 |
| Rewrite-only operations | 217 |
| Rehearsals (excluded from all counts) | 33 |

**Independent confirmation vs. Agent 44's committed ledger:** Agent 44's
`ledger/agent-44-receipt-ledger.json` reports 51 pages / 18 move-bearing publications.
Agent 45's fresh reconstruction from `/api/v1/changes` + page history finds 54 / 21. This
is **not a discrepancy in substance** — the 3-page gap is exactly the three cohorts
(`transit-deploy-state-machine`, `edge-observability-pattern`, `tls-cert-strategy`) that
`handoff/publication-lane.json` itself names as the reason the lane was released; their
publication files are committed, but `agent-44-receipt-ledger.json` wasn't regenerated to
include them. Agent 44's 51 is a strict subset of Agent 45's 54 — zero conflicting rows.
Full per-page proof: `audits/2026-07-30T1240-ledger-audit.json`.

## Current corpus (live snapshot, 2026-07-30T12:40:00Z, certification v104)

| | Count |
|---|---|
| `/operations/` subtree | 120 |
| `/control-plane/` subtree | 128 |
| Combined subtree | 248 |
| Flat `/operations/*.md` | 66 |
| Flat `/control-plane/*.md` | 36 |
| Combined flat roots | 102 |
| Total corpus (incl. archived) | 947 (513 active) |

## Retrospective classified baseline (248 pages — current-state, NOT historical)

Classification precedence applied (a page gets exactly one, first match wins): moved
under Issue #44 → section landing → evidence surface → #43-blocked → hold-blocked →
active board/project → backlog/paused → archived → hub prerequisite → authoring decision
required → deliberate non-move → correctly placed → still requires movement.

| Disposition | Count | Confidence |
|---|---|---|
| Correctly placed | 56 | mixed — see audit |
| Moved under Issue #44 | 54 | fully independently verified |
| Archived | 42 | independently verified via fetched lifecycle field |
| Reported by Agent 44 (hub / evidence surface / active board-project / #43-blocked / section landing — flat, non-candidate) | 39 | **not individually re-derived this pass** — see audit |
| **Still requires movement** (flat control-plane) | **22** | fully independently verified |
| **Reorganisation candidate pending semantic clustering** (nested, legacy path) | **22** | fully independently verified — see correction below |
| Backlog/paused | 4 | independently verified via fetched lifecycle field |
| Evidence surface | 4 | fully independently verified (PATCH_METADATA, knowledge_class=EVIDENCE) |
| Authoring decision required | 2 | fully independently verified |
| Hub-style retirement candidate | 1 | fully independently verified (old `topology-invariants/index.md`) |
| Deliberate non-move | 1 | fully independently verified |
| Hold-blocked | 1 | fully independently verified |

Sum = 248. Full per-page detail: `baseline/retrospective-baseline.json`.

**Correction (2026-07-30T13:10:00Z):** 23 pages under the legacy
`control-plane/topology-invariants/` path (22 invariants + `index.md`) were previously
classified "correctly placed" solely because they are nested, not flat. That default was
wrong: 18–23 sibling pages under the exact same path pattern were already moved to
`control-plane/invariants/` by six earlier cohorts. Nesting under a path the programme has
already retired elsewhere is not correct placement. Reclassified to "reorganisation
candidate pending semantic clustering" (22) and "hub-style retirement candidate" (the old
index, 1). Full body review: `audits/2026-07-30T1310-topology-invariants-body-review.json`.

**This is a retrospective current-state classification, not the original programme
baseline** — no original "pages requiring movement" denominator was ever frozen for
Issue #44 (confirmed: zero rows in `docs.reorganisation_plans`, no inventory-snapshot
event anywhere in 131 `docs.changes` records).

## Completion percentages — withheld this pass

The forward-looking denominator changed from 76 (54 moved + 22 still-requires-movement)
to a provisional **45-candidate pool** (22 flat control-plane + 23 newly-corrected nested
topology-invariants pages) that has not yet been fully classified end-to-end. **No
percentage is published against 45, 76, or any other figure this pass** — publishing
against an incomplete or moving denominator would mislead. Re-publish only once both the
22 and the 23 are fully resolved to terminal dispositions.

No percentage is published against the 248-page combined subtree either, and none should
be computed from it as an IA-debt completion measure.

## Next executable cohort — revised, two disjoint candidate pools

**Pool A — flat control-plane, 22 pages** (unchanged from before). Full list:
`ledger/dispositions.json` (disposition = "still requires movement").

**Pool B — nested `control-plane/topology-invariants/` remainder, 23 pages, NEW.**
Body-reviewed and split into 6 semantic sub-cohorts (not one 23-page batch):
`audits/2026-07-30T1310-topology-invariants-body-review.json`. Recommended order:

1. **Site / GEO-Fabric Edge** (5 pages) — **safest first cohort**: lowest average
   coupling, no PROPOSED-status pages, no cross-cohort dependency.
2. **Framework / Registry Invariants** (4 pages) — 2 of 4 pages still PROPOSED status
   (not yet ratified content-wise; safe to move, flag as open work).
3. **GEO Consumer Convergence — Placement & Recovery** (3 pages)
4. **GEO Consumer Convergence — Core** (4 pages) — includes the single highest-coupling
   page in the review (18 inbound)
5. **Interface Domain** (4 pages)
6. **`i-config-projection-1.md`** (1 page) — authoring decision needed first: may belong
   in `control-plane/foundational/` rather than `control-plane/invariants/` given its
   governance-wide (not topology-specific) scope
7. **`i-interface-realization-1.md`** (1 page) — sequence with the CCM design-pair move
   (below); three CCM pages hold relative links to it. One shared reference-repair pass
   covers both, without merging the move publications.
8. **`control-plane/topology-invariants/index.md`** — hub-style retirement (not a plain
   move), since `control-plane/invariants/index.md` (v59) already supersedes it. Needs a
   body diff between the two indexes before deciding archive vs. content-merge-and-archive.

**Pool C — `ccm-*` cluster, unchanged, kept separate from Pool B** (per instruction — do
not enlarge a publication by combining CCM with topology-invariants):
`audits/2026-07-30T1252-cohort-prep-ccm-and-invariants.json`. Clean pair
(`ccm-interface-realization-design.md`, `ccm-dhcp-resolver-ownership-capability.md`) →
`control-plane/design/`; `ccm.md` hub prerequisite; `ccm-interface-realization-capability.md`
authoring decision; `ccm-interface-realization-step6-runbook.md` cross-subtree to
`operations/runbooks/`; two pages need no action (already archived / evidence surface).

**Operations flat-root queue is empty.** Its 4 active/durable candidates each already
carry an individual disposition:

- `operations/control-plane-enforcement.md` — deliberate non-move
- `operations/induce-recover-doctrine.md` — authoring decision required
- `operations/invariant-roadmap.md` — authoring decision required
- `operations/w4-1-5b-runtime-manifest-architecture.md` — hold-blocked (F11/F12 both OPEN)

### Lane status

**Transit/gateway cluster — IN PROGRESS, lane held by `agent-44`**:
`transit-convergence.md`, `gateway-runtime-responsibilities.md`,
`site-network-authority.md`, `transport-plane-authority.md`. Do not touch these pages.
(Note: `site-network-authority.md` here is the flat control-plane page in Pool A, a
different page from `i-site-network-authority-1.md` in Pool B's Site/GEO-Fabric cluster.)

**Pool D — remaining design/spec/reference** (unchanged from before, not yet body-reviewed):
`architecture-overview.md`, `authentication-model.md`, `bootstrap-authority-map.md`,
`charliehub-mcp.md`, `class-b-hosts.md`, `current-state.md`, `docplane.md`,
`docs-gen-1.md`, `hub2-authority-crosswalk.md`, `hub2-host-net.md`, `i-gen-no-vcs-1.md`,
`i-traefik-empty-tcp.md`, `lw-r1-design-notes.md`, `schema-migrations.md`,
`system-map.md`

All groupings above remain provisional and must be regenerated from a fresh snapshot
before any rehearsal.

## Shared-branch lifecycle recommendation

Once Agent 44's receipts, this baseline, the independent ledger, this first complete
status, and the ownership rules in `README.md` are all present and verified (they now
are, as of this commit), `programme/issue-44-shared-record` should be opened as a normal
PR against DocPlane's default branch and merged — not preserved indefinitely as a hidden
coordination branch. Further publication receipts and status refreshes should use normal
short-lived branches/PRs afterward. Agent 45 is not merging that PR itself; this is a
recommendation for whoever owns repository workflow.
