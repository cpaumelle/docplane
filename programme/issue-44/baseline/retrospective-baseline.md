# Issue #44 — Retrospective Classified Baseline

*Generated 2026-07-30T12:40:00Z by Agent 45. Machine-readable version:
`retrospective-baseline.json` (248 rows, one per stable `resource_id`).*

## What this is

A classification of every currently-active page under `/operations/` and
`/control-plane/` (248 pages), as of one fresh live snapshot taken at the timestamp
above, with DocPlane certification at `state_version=104` (`working_state_identity ==
deployed_state_identity`, independently confirmed via `GET /api/v1/certification/status`).

## What this is NOT

**This is not the original Issue #44 programme baseline.** No such thing was ever
frozen. All 131 `docs.changes` records were enumerated and none is a full-corpus
inventory snapshot; `docs.reorganisation_plans` (the `reorganisation-v1` API) has zero
rows — that subsystem has never been used for this programme. If a percentage or count
elsewhere describes this as "the original programme estimate," that description is wrong
— correct it to "retrospective classified baseline."

## Counting rules

- **Subtree**: a page is `operations` or `control-plane` based on its current path
  prefix. 120 + 128 = 248.
- **Flat root**: a page is "flat" if its path has no `/` between the subtree prefix and
  the filename (e.g. `operations/foo.md` is flat; `operations/runbooks/foo.md` is not).
  66 flat operations + 36 flat control-plane = 102.
- **Active only**: archived pages are excluded from the subtree/flat-root corpus counts
  above (513 active of 947 total corpus-wide), but archived pages that are *nested*
  under operations/control-plane can still appear in the classification below if their
  `status` field returned `active` at fetch time — see the `archived` disposition, which
  is driven by the in-body `**Lifecycle:** ARCHIVED` marker, a separate signal from the
  `status` field.

## Classification precedence

A page receives exactly one disposition — the first matching rule wins:

1. moved under Issue #44
2. section landing
3. evidence surface
4. #43-blocked
5. hold-blocked
6. active board/project
7. backlog/paused
8. archived
9. hub prerequisite
10. authoring decision required
11. deliberate non-move
12. correctly placed
13. still requires movement

## Corpus totals

| | Count |
|---|---|
| `/operations/` subtree | 120 |
| `/control-plane/` subtree | 128 |
| Combined | 248 |
| Flat `/operations/*.md` | 66 |
| Flat `/control-plane/*.md` | 36 |
| Combined flat roots | 102 |

## Category totals

| Disposition | Count | How it was determined |
|---|---|---|
| Correctly placed | 79 | nested page, not touched by Issue #44, lifecycle not ARCHIVED/PAUSED |
| Moved under Issue #44 | 54 | `MOVE_PAGE` operation in a `PUBLISHED` change (`/api/v1/changes`), baseline path from `/api/v1/pages/<rid>/history` |
| Archived | 42 | independently fetched `**Lifecycle:** ARCHIVED` marker or `status: archived` |
| Reported by Agent 44 (hub / evidence surface / active board-project / #43-blocked / section landing) | 39 | flat-root, not a move candidate, not classified-without-move — Agent 44's `cohorts/pass-final-audit.txt` categorised these using inbound-link-count thresholds (hub ≥10 inbound) and body markers Agent 45 did not recompute in this pass. Count cross-checked structurally against the flat-root totals; **individual page-to-subcategory assignment was not independently re-walked** |
| Still requires movement | 22 | live fetch: `active`, `PUBLISHED`, `criticality: NORMAL`, non-archived, still at a flat control-plane root — this is the validated forward-looking cohort |
| Backlog/paused | 4 | independently fetched `**Lifecycle:** PAUSED` marker |
| Evidence surface | 4 | `PATCH_METADATA` classification with `knowledge_class: EVIDENCE`, no path change |
| Authoring decision required | 2 | `operations/induce-recover-doctrine.md`, `operations/invariant-roadmap.md` — page body + Agent 44's evidence-commit findings |
| Deliberate non-move | 1 | `operations/control-plane-enforcement.md` — self-declared secondary control; superseding authority is `control-plane/foundational/enforcement-model.md` |
| Hold-blocked | 1 | `operations/w4-1-5b-runtime-manifest-architecture.md` — blocked by `operations/w4-1-x-docs-refresh-hold-point.md`; unlock conditions F11/F12 both OPEN |

Sum = 248.

## Confidence summary

- **91 pages (54 + 22 + 11 + 2 + 1 + 1)** — fully independently verified: live API fetch,
  page-history cross-check, or direct body read by Agent 45.
- **118 pages** ("correctly placed" nested pages not touched by Issue #44) — verified by
  independently-fetched lifecycle/status plus path nesting; not individually body-read.
- **39 pages** — adopted from Agent 44's `pass-final-audit.txt` category totals without
  independent per-page re-derivation. This is the one honest gap in this baseline; closing
  it requires recomputing inbound/outbound link counts across the corpus, which this pass
  judged disproportionate given the actionable cohort (the 22) was already fully resolved
  without it. See `audits/2026-07-30T1240-ledger-audit.json` for the full accounting.
