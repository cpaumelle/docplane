# Invariants catalogue decomposition — closure

*docplane#100. All 9 batches published.*

## Result

`control-plane/invariants/index.md` went from a 95KB monolith of 43 embedded sections to
a lean catalogue linking to 73 dedicated invariant pages (42 pre-existing + 31 newly
extracted), plus a final "Additional Invariants" section closing a real gap found in the
last audit: the 42 pre-existing pages had never been listed in the index at all.

## Publications (all rehearsed, validated, published; certification CURRENT after each)

| Batch | Change ID | Pages created | Notes |
|---|---|---|---|
| 1 — domain/state/db authority | `398c8287` | 3 | 3 sections found superseded by ccm.md I-1/I-2/I-3 during canonicality review, no new pages for those |
| 2 — DNS/certs/observability | `5cbc58a8` | 8 | includes a bundled fix: a stale cp-net-1-driver.md reference found inline |
| repair (unscheduled) | `404b6032` | 0 | corrected a real bug: batch 2 built from a cached pre-batch-1 snapshot and silently reverted batch 1's index edits on publish; caught by direct verification, root cause fixed in `batch_lib.py` before continuing |
| 3 — docs/generated artefacts | `71e5c422` | 4 | I-AUTHELIA-RULES-AUTHORITY-1 extracted as a nested-H3 child (externally cited on its own); I-GEN-NO-VCS-1 duplicate fixed |
| 4 — protected files/render integrity | `087a4f30` | 5 | |
| 5 — transit/routing/VPN | `dc7f4d3b` | 6 | VPN Route Invariants split into 2 (source text itself frames as paired) |
| 6 — Authelia ownership | `829bcdcf` | 1 | |
| 7 — Actility proposed invariants | `374dd35f` | 4 | all marked PROPOSED |
| 8 — backlink retrofit (3 parts) | `996bb8ba`/`5d9e48a3`/`fb0514de` | 0 | added a back-link to 41 pre-existing pages that lacked one |
| 9 — full catalogue completion | `f27fc6f7` | 0 | closed the "42 pages never indexed" gap found in the final audit |

**31 new pages total**, matching the frozen manifest exactly.

## Final audit (fresh corpus, full 90-move Issue #44 ledger, fixed resolver)

- Route uniqueness: 0 duplicates.
- Page-vs-section conflicts: 0.
- Full-corpus stale-reference sweep (all 90 ledger entries): 0 pages need rewriting, 33
  legitimate owned preservations remain.
- Invariants catalogue: 73/73 pages have a working back-link to the index; 0 pages
  orphaned from the index (0 missing, after the batch-9 addendum).
- Certification: CURRENT, working==deployed, zero open changes.

## Known issues surfaced, not fixed here (tracked separately)

- `I-DNS-DUAL-RESOLVER-1` — cited 15× across 6 pages, defined nowhere. docplane#101.
- `ccm.md`'s I-1 through I-5 are bold paragraph text, not headings — no individual
  anchor. The 3 CCM-superseded historical notes link to the section anchor
  (`ccm.md#ccm-invariants`) as a documented workaround; giving them real headings is a
  legitimate follow-up, out of this pass's scope.
- The resulting 73-entry flat `Control Plane/Invariants/*` navigation list may warrant
  sub-grouping in the future — noted, not solved (would also mean touching the
  already-published 42).

## Process note

One real bug was found and fixed mid-execution: batch 2's build script read `index.md`
from a cached original snapshot instead of live content, so its publish silently
reverted batch 1's edits (the 3 new pages from batch 1 were unaffected — only index.md's
edits were lost). Caught via direct post-publish verification, corrected with a targeted
repair change, and the root cause fixed in the shared build library (`load_index()` now
always fetches live content) before any further batch was built.
