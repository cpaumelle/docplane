# Issue #44 — stale-reference repair, closeout (2026-07-30T17:15)

*Agent 45, sole repair publisher. Resolver fix merged ([docplane#91](https://github.com/cpaumelle/docplane/pull/91),
squash-merged as of certification checks passing, main advanced twice more from unrelated
parallel human work during this pass — both rebased through cleanly, neither touched
`migration/`). Fresh credential self-issued for this repair (`73a1f3b0…`, principal
`claude-code-agent45-issue44-repair`), independent of any token used in the reconciliation
pass or by Agent 44.*

## Pre-publication verification

- Fresh corpus fetched at each checkpoint (v139 pre-repair, v141 post-repair); the API's
  `/api/v1/pages` has no offset-based pagination (`limit` only, max 2000) — a minor
  operational gotcha worth documenting, not a doctrine issue.
- Certification drifted twice during this pass (v138→v139→v140) from an **unrelated**,
  independently-authored schema-catalogue generator run (`CREATE_PAGE` × 7, `base_state_identity`
  exactly matching the v138 snapshot, zero overlap with any of the 88 ledger pages or the
  56/14 finding-set pages) and ongoing human PR merges to `main` (#90, #92, neither
  touching `migration/`). Verified via `/api/v1/deployments/attempts` and the change
  record itself before proceeding — not assumed safe.
- Drift check on the 23 targeted pages specifically: **zero drift** at every checkpoint.

## A further resolver-methodology gap found and reconciled before publishing

Cross-checking my own quick diagnostic scan against the real `plan_corpus`/`plan_rewrites`
output surfaced one more real gap: my ad-hoc scan (and the original §2/§3 reconciliation)
checked "does any candidate match a live page" *before* "does any candidate match a
tracked old path" — so a bare section-root reference (`/control-plane/topology-invariants/`)
whose page-form candidate is a genuinely-moved page but whose index-form candidate
coincidentally collides with an unrelated, still-live section-index page was being
silently treated as "resolves fine," masking a real stale reference. `plan_rewrites`
itself has no such bug — it correctly prioritizes the ledger match, exactly per the
principle that a working redirect (or, here, a live coincidental collision) does not
excuse a stale reference. This added 5 rewrites (23 pages, 70 rewrites total, versus 65
in the earlier count) that my diagnostic tooling had missed; both are ledger-grounded,
titled Issue #44 moves (`H1: invariants hub becomes its section landing page`,
`structural cohort: i72-wg-mesh-relocate`), confirmed correct by inspection.

**Full reconciliation, canonical vs. independent, before publish**: combining
`plan_corpus`'s rewrites (70 raw / 45 unique pairs) with its preservations (28 raw / 16
unique pairs) gives 98 raw / 61 unique — and a from-scratch independent verifier
(reimplementing ledger-priority resolution, no `migration.links` import) found **exactly**
98 raw / 61 unique, with **zero** items only on either side. Absent-target scoped count
(14) matched exactly between both methods too.

## Repair executed

- **Change `674d306f-f626-4f9d-a6d1-34413920f985`**, published: 23 pages, 70 line-level
  rewrites, 100% masking-equality proven per page (link destinations only; labels,
  anchors, titles, surrounding Markdown untouched — enforced by `plan_rewrites` itself,
  independently re-verified).
- Rehearsal (`0bb6a9c4…`) run first: 23/23 operations validated, 0 errors, abandoned
  cleanly (zero residual, confirmed via `GET /api/v1/changes?status=DRAFT` → empty both
  before and after).
- 28 raw / 16–17 unique references correctly **left untouched** — evidence surfaces,
  blockquotes, inline-code quotations. Re-verified live post-publish: still present,
  unchanged, still correctly classified.

## Absent targets: zero repaired, by design

All 14 scoped absent targets retain their pre-repair disposition — full table in
`absent-target-disposition.json`. None had a canonical replacement provable without
guessing author intent (2 already protected, 1 known baseline docplane#78, 7 pre-existing
documentation gaps unrelated to any move, 3 relative-depth authoring errors, 1 relative-depth
error specific to a moved page's sibling reference). Per the explicit repair scope
("leave authoring decisions unresolved rather than guessing... do not create missing
pages"), zero absent-target corrections were published this session.

## Post-publish verification

- Certification: **v141, CURRENT, working==deployed** (`462a9398…`).
- Zero open draft changes, before and after.
- Canonical post-repair audit: `rewrite-to-canonical` disposition count is **zero** —
  no active controlled reference to an old Issue #44 path remains.
- Independent post-repair verifier: 28 raw / 17 unique stale-old-path matches remain,
  **exactly** matching the pre-publish preservation count (98−70=28 raw, 62−45=17
  unique) — arithmetically exact, confirming precisely the intended set was rewritten and
  nothing else.
- Spot-checked 3 rewritten routes and 2 redirect-serving old paths live: all 200.
- Spot-checked rewritten content on `hub2-authority-crosswalk.md` directly via the API:
  matches the plan exactly.
- Ledger unchanged: **88 unique pages moved / 88 move operations** — no move operations
  in this change, confirmed by the change record (23 `REPLACE_DOCUMENT` operations, zero
  `MOVE_PAGE`).

## Acceptance conditions — status

| Condition | Status |
|---|---|
| No active controlled reference to an old Issue #44 path | ✅ confirmed, canonical + independent |
| All historical preservations are exact, owned exceptions | ✅ 28/16 unique, all blockquote/evidence_surface/inline_code |
| All 14 absent targets retain an explicit disposition | ✅ `absent-target-disposition.json` |
| Canonical and independent audits reconcile | ✅ exact match, both pre- and post-repair |
| No new unowned broken target | ✅ verified via drift check + post-repair audit |
| Certification CURRENT | ✅ v141 |
| Working equals deployed | ✅ |
| Zero open changes | ✅ |
| Rehearsals abandoned with zero residual | ✅ |
| Ledger remains 88 unique moved pages | ✅ unchanged |

## Remaining actionable reorganisation queue

- **Resolver/library**: none — landed and merged.
- **14 absent-target items**: each needs a human authoring decision (not guessable) —
  candidates for a small, separate documentation-fix session, not a repair-mechanics one.
- **Hub reclassifications** (already recorded in the prior reconciliation pass): `ccm.md`
  (23 inbound), `cp-net-1-driver.md` (17 inbound, hub prerequisite), `hub2-authority-crosswalk.md`
  (9 inbound, withdrawn from "plain move" list) — still awaiting their own structural
  move/hub-conversion session.
- **API-native publication lease**: still undeployed; concurrent publication remains
  disabled per standing instruction until it exists and is adversarially tested.
- Recommend the next substantial serial session focus on the hub-conversion moves
  (`ccm.md`, `cp-net-1-driver.md`, `hub2-authority-crosswalk.md`) as a single-owner,
  single-lane session, given their now-confirmed high inbound counts.

No ordinary page moves were published this session, per instruction.
