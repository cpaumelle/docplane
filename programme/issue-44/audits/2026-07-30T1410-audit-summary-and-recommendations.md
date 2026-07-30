# Issue #44 — concurrent-session audit, lease design, and resumption recommendation

*Agent 45, 2026-07-30T14:10:00Z. Read-only this pass — no rehearsal, no publication, no
corpus mutation. Full detail in the companion files in this directory.*

## 1. Independent regression audit — summary

Built a corpus-wide link-existence checker (2,732 internal links across all 513 active
pages, pre-repair) rather than trusting the manifest-scoped `migration/links.py scan`
that both agents relied on and that Agent 44's own commit correctly identified as blind
to this bug class.

**Pre-repair: 40 broken targets** (not the "34" cited by either Agent 44's commit or
this task's framing — see the discrepancy note in `2026-07-30T1410-independent-regression-audit.json`,
which I could not fully reconcile without the other tool's raw per-link output, but whose
methodology and every individual finding is verified directly against live page content).

- **18 pre-existing, unchanged** — matched byte-for-byte against the original 20-entry
  `protected_refs` baseline (+ the 1 Pi4 #78 defect) captured before any of this
  session's moves.
- **19 new, caused by Agent 45** — 4 pages whose own outbound relative links were never
  corrected for a directory-depth change their own move caused.
- **3 new, caused by Agent 44** — 1 disclosed (the commit's own admission re:
  `i-site-transport-authority-1.md`), **2 undisclosed** (`i-gen-no-vcs-1.md`'s own
  depth-change bug — the same class Agent 45 made — and a second missed occurrence on
  `i-transport-hub-1.md`, structurally identical to the one Agent 44 did disclose).

**After the observed repair (`f18538be`, 14:03:52): 20 broken targets remain.**
18 are the unchanged pre-existing baseline. **The 2 undisclosed Agent-44 regressions are
still broken** — the repair fixed only the pages Agent 44 named, not the ones this
independent audit additionally found. **Answer to "do only the accepted baseline
remain": no.**

**Final confirmation (2026-07-30T14:10, certification v128, working==deployed, 514
active pages, after Agent 44's second commit `17fc368` which added ledger reconciliation
and a new `programme/issue-44/gate/` tool but did not publish any further repair beyond
the same `f18538be`):** rechecked the live corpus fresh — **still exactly 20 broken
targets, identical list.** `i-gen-no-vcs-1.md` L16 and `i-transport-hub-1.md` L34 remain
broken right now. This directly contradicts commit `17fc368`'s claim of "0 new unowned
breaks" / "baseline is 10" — that claim rests on a different test definition (did this
raw link *ever* resolve to a live page, tracked by resolved-target, before either agent's
work this session) than a straightforward "is this specific occurrence broken right now,
and did it break because its source page moved this session" test. Both are legitimate
questions, but they produce different practical answers: **a reader following either of
these two links today gets a broken page**, regardless of which internal ledger they're
filed under. Their new `programme/issue-44/gate/link-baseline.json` (10 owned
exceptions) does not include the 6 original `protected_refs` entries this audit also
confirms still live (`invariant-catalogue-parity-triage.md`, `invariants/index.md`,
`ch-transit-core.md`, `authelia.md`, `transit-core-break-glass.md`, and the original
`i-transport-hub-1.md` L48 entry) — worth reconciling their gate's exception registry
against the full original 20-entry baseline this program has always used, not just the
subset relevant to this incident.

## 2. Ledger reconciliation — summary

**76 unique pages moved, 76 operations, 32 move-bearing publications, 0 moved twice** —
independently re-derived from a fresh `/api/v1/changes` pull, not carried forward.

The 74-vs-76 discrepancy is fully resolved: 74 was agent-45's count at the end of its own
session; Agent 44's regression commit lists "8 pages" but 6 were already in that 74 — only
`docs-gen-1.md` and `authentication-model.md` are genuinely new. 74 + 2 = 76.

**Zero moved-page overlap.** **One real reference-repair overlap**:
`i-site-transport-authority-1.md` was edited by both agents plus the later repair — three
separate `REPLACE_DOCUMENT` operations across the incident window, each correctly
revision-checked, applied sequentially with no lost update. No data was lost. But nothing
in the current design *guaranteed* that outcome — it happened to hold because both
agents re-fetched immediately before writing, not because the platform enforced it.

## 3–4. Lease design and concurrency model decision

Full design in `api-native-publication-lease.md`. **Recommending the global lease, not
scoped concurrent leases, and this session's own evidence is the reason, not merely the
task's framing**: zero page-level move overlap occurred, and real corpus damage happened
anyway (22 new broken links), because *link effects cross page-membership boundaries*.
A page neither agent moved (`i-transport-hub-1.md`) still broke, because it referenced a
page that moved. Scoped-by-source-membership leases cannot prevent this class of damage
by construction — the thing that needs to be exclusive is target-existence and reference
correctness across the *whole* corpus, which is exactly why certification and the
existence-aware audit are already corpus-wide in this system. Scoped concurrency should
only be reconsidered if DocPlane can prove disjoint reference-repair target sets, not
just disjoint source pages — the task's own framing anticipated this correctly, and this
audit provides the concrete counter-evidence to anyone tempted to argue "no page
overlap, so it was fine."

## 5. Partial cluster migration rule

Full rule in `partial-cluster-migration-rule.md`. Directly derived from the two real bug
classes found in part 1: own-page relative-link depth changes, and partial-cluster
sibling references. Both must fail rehearsal, not merely warn, going forward.

## 6. Recommendation on resuming Issue #44

Confirming the task's own suggested answer, now with independent evidence behind each
clause:

- **No concurrent publication** — proven necessary by 22 real regressions despite zero
  page-level overlap.
- **One explicitly assigned foreground publisher at a time** — until the API-native
  lease ships, this must be enforced by protocol/operator discipline, since the Git lane
  file is proven (this session) not to enforce it.
- **Parallel read-only preparation remains allowed** — nothing in this audit implicates
  read-only work; the damage came entirely from concurrent *publication*.
- **Normal moves resume after the corpus is repaired** — and per this audit, the corpus
  is **not yet fully repaired**: 2 undisclosed Agent-44 regressions remain live
  (`i-gen-no-vcs-1.md` L16, `i-transport-hub-1.md` L34). These should be fixed — using
  root-absolute rewrites, matching the good pattern `f18538be` already established —
  before treating this incident as closed.
- **Concurrent autonomous publication resumes only after the API-native lease is
  deployed and proven** against the acceptance criteria in `api-native-publication-lease.md`.

## Files in this delivery

- `2026-07-30T1410-independent-regression-audit.json` — full per-link findings, pre- and
  post-repair, methodology, and the unresolved discrepancy note.
- `2026-07-30T1410-ledger-reconciliation.json` — 74/76 resolution, ordering, overlap
  analysis.
- `api-native-publication-lease.md` — schema, endpoints, fail-closed matrix, audit trail,
  acceptance criteria.
- `partial-cluster-migration-rule.md` — the two real bug classes, the mandatory
  preflight, tooling changes, acceptance criteria.
