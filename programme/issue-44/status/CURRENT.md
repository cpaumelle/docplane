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
| Correctly placed | 79 | mixed — see audit |
| Moved under Issue #44 | 54 | fully independently verified |
| Archived | 42 | independently verified via fetched lifecycle field |
| Reported by Agent 44 (hub / evidence surface / active board-project / #43-blocked / section landing — flat, non-candidate) | 39 | **not individually re-derived this pass** — see audit |
| **Still requires movement** | **22** | fully independently verified |
| Backlog/paused | 4 | independently verified via fetched lifecycle field |
| Evidence surface | 4 | fully independently verified (PATCH_METADATA, knowledge_class=EVIDENCE) |
| Authoring decision required | 2 | fully independently verified |
| Deliberate non-move | 1 | fully independently verified |
| Hold-blocked | 1 | fully independently verified |

Sum = 248. Full per-page detail: `baseline/retrospective-baseline.json`.

**This is a retrospective current-state classification, not the original programme
baseline** — no original "pages requiring movement" denominator was ever frozen for
Issue #44 (confirmed: zero rows in `docs.reorganisation_plans`, no inventory-snapshot
event anywhere in 131 `docs.changes` records).

## Completion percentages (numerator/denominator always named)

- **Unique pages moved / confirmed movement set: 54 / 76 = 71.1%** (moved / [moved +
  still-requires-movement] — the only receipt-backed forward-looking set)
- No percentage is published against the 248-page combined subtree, and none should be
  computed from it as an IA-debt completion measure — see warning above.

## Next executable cohort

**22 control-plane flat-root pages**, independently verified (live fetch: `active`,
`PUBLISHED`, `criticality: NORMAL`, non-archived). Full list: `ledger/dispositions.json`
(disposition = "still requires movement").

**Operations flat-root queue is empty.** Its 4 active/durable candidates each already
carry an individual disposition:

- `operations/control-plane-enforcement.md` — deliberate non-move
- `operations/induce-recover-doctrine.md` — authoring decision required
- `operations/invariant-roadmap.md` — authoring decision required
- `operations/w4-1-5b-runtime-manifest-architecture.md` — hold-blocked (F11/F12 both OPEN)

### Provisional next-cohort grouping (from the 22 — must be regenerated from a fresh
snapshot before any rehearsal; grouped by evident topic only, not yet body-reviewed for
inbound/outbound coupling)

1. **Transit/gateway cluster**: `transit-convergence.md`, `gateway-runtime-responsibilities.md`,
   `site-network-authority.md`, `transport-plane-authority.md`
2. **`ccm-*` cluster**: `ccm-dhcp-resolver-ownership-capability.md`,
   `ccm-interface-realization-design.md`, `ccm-interface-realization-step6-runbook.md`
3. **Remaining design/spec/reference**: `architecture-overview.md`, `authentication-model.md`,
   `bootstrap-authority-map.md`, `charliehub-mcp.md`, `class-b-hosts.md`, `current-state.md`,
   `docplane.md`, `docs-gen-1.md`, `hub2-authority-crosswalk.md`, `hub2-host-net.md`,
   `i-gen-no-vcs-1.md`, `i-traefik-empty-tcp.md`, `lw-r1-design-notes.md`,
   `schema-migrations.md`, `system-map.md`

## Shared-branch lifecycle recommendation

Once Agent 44's receipts, this baseline, the independent ledger, this first complete
status, and the ownership rules in `README.md` are all present and verified (they now
are, as of this commit), `programme/issue-44-shared-record` should be opened as a normal
PR against DocPlane's default branch and merged — not preserved indefinitely as a hidden
coordination branch. Further publication receipts and status refreshes should use normal
short-lived branches/PRs afterward. Agent 45 is not merging that PR itself; this is a
recommendation for whoever owns repository workflow.
