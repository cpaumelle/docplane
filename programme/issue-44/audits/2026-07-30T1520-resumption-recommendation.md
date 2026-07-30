# Issue #44 — resumption recommendation (2026-07-30T15:20)

*Agent 45. Read-only this pass. Agent 44 has not yet published a repair — they fixed the
shared resolver and produced a corrected audit, then deliberately stopped before §4 (the
66-reference repair publication), §2 (fixtures), §5 (classifying the 21 absent targets),
and §8 (updated baseline). No live mutation, lane not taken, certification untouched —
independently confirmed (v138 unchanged, 0 open changes, 88-page ledger unchanged).*

## Verification summary

Built a fresh, from-scratch verifier (`independent_verifier_v3.py`), importing nothing
from `migration.links` or `programme/issue-44/gate/`. Found and fixed two real bugs in my
**own** two prior attempts along the way (documented in the audit JSON) — including the
exact same class of defect Agent 44 found in the shared resolver (pretty-URL links
silently skipped). Reporting this honestly rather than presenting a clean-first-try.

**Strong convergent validation**: all three corrected inbound counts I independently
derived match Agent 44's exactly — `ccm.md` 23/23, `hub2-authority-crosswalk.md` 9/9,
`cp-net-1-driver.md` 17/17. Old-Issue-44-path count is a close match (89 vs 88).
"Genuinely absent" counts do not match exactly (55 full-corpus / 15 scoped-to-Issue-44
vs their 21) — full breakdown and honest scoping caveats in the audit JSON; this is
reported as an open discrepancy, not forced into agreement.

## Hub/candidate classification corrections (from the corrected inbound graph)

- **`cp-net-1-driver.md`**: 17 confirmed inbound. Was sitting in the "not individually
  re-derived" bucket — **should be reclassified as a hub prerequisite**, same treatment
  as `ccm.md`, not a plain move.
- **`hub2-authority-crosswalk.md`**: 9 confirmed inbound (was reported as 0, clearly a
  resolver-bug artifact). Was in agent-45's original flat-control-plane "still requires
  movement" list — **withdrawing that classification**. Needs hub-aware treatment
  (dedicated inbound-repair pass), not a plain structural-cohort move.
- **`ccm.md`**: inbound corrected 15/16 → 23. Disposition unchanged (hub, deferred) —
  the correction changes the repair-pass size, not the classification.

## Resumption recommendation: **NOT YET**

Per the stated conditions, ordinary moves should resume only if all of the following
hold. Checking each against what's independently confirmed right now:

| Condition | Status |
|---|---|
| Independent current-target audit reconciles with the canonical gate | **Partial** — inbound counts match exactly; old-path counts nearly match; genuinely-absent counts do not fully reconcile (scoping difference, not yet resolved) |
| No unowned current broken target remains | **Not yet checked** — the repair (§4) hasn't happened; 89 old-path references and up to 15 in-scope "genuinely absent" items are still live |
| Old-path controlled references eliminated or explicitly protected | **Not yet** — 89 unrepaired instances remain |
| Certification CURRENT | ✅ v138, confirmed |
| Working equals deployed | ✅ confirmed |
| Shared ledger consistent | ✅ 88/88, confirmed unchanged |

**Do not resume ordinary moves yet.** The repair itself (§4) hasn't landed — this pass
establishes the pre-repair truth precisely so the post-repair recheck has a real
baseline to compare against, per the task's own sequencing. Once Agent 44 (or whoever
takes the lane) publishes the repair:

1. Fetch a fresh corpus snapshot.
2. Rerun this same independent verifier (not the patched `links.py`).
3. Confirm every one of the 89 old-path references was rewritten or is an explicit
   owned exception.
4. Confirm every genuinely-absent target (of the ones actually in Issue #44 scope) has
   a disposition — not silently dropped.
5. Compare against Agent 44's post-repair gate output, investigate any mismatch by
   source stable ID and raw target, same as this pass did.

## Operating model, once approved

- Parallel read-only preparation only.
- One explicitly assigned publisher per substantial session.
- No watchers.
- Git lane file is coordination metadata, never treated as a lock.
- No concurrent autonomous publication until the API-native lease exists, is deployed,
  and is adversarially tested (design already committed: `programme/issue-44/design/api-native-publication-lease.md`).

## Also fixed this pass (unrelated to the resolver, found while building the ledger cross-reference)

`ledger/moves.json` had 12 stale `current_path` values (my own 12-page cohort's rows,
recorded from page-history fetched *before* the move published rather than refreshed
after). Corrected in place using live `/api/v1/pages` data — no ledger totals changed
(still 88/88), only the per-row `current_path` field for those 12 entries.
