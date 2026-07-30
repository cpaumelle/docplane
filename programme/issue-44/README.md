# DocPlane Issue #44 — shared programme record

Governed information-architecture reorganisation of `/operations/` and `/control-plane/`.

This directory is the **shared, durable coordination surface** for Issue #44. It exists
because chat summaries are not programme authority: numbers quoted in prose have already
drifted once, and the correction had to be rebuilt from publication receipts.

**Read `authority.json` first.** It names every authority surface, both GitHub issues,
the baseline exception, and — importantly — where the raw evidence commit actually lives.

---

## What is authoritative, and what is not

| Surface | Authority for |
|---|---|
| GitHub (this directory) | programme coordination, durable evidence, agreed classifications |
| DocPlane | current page content and state |
| `/api/v1/changes` + `/api/v1/pages/<rid>/history` | **publication history — the only authority for what was published** |
| Prose reports, commit bodies, issue comments | nothing. Derived claims, always re-derivable from receipts |

**There is no frozen original "pages requiring movement" denominator.** Issue #44 began
without one. The retrospective baseline under `baseline/` is a *current* classification,
clearly labelled as such. It must never be presented as the original programme estimate,
and the full corpus must never be used as a completion denominator unless the percentage
is explicitly described as corpus coverage.

---

## Where the raw evidence commit lives

Evidence commit **`ee97ceab`** is in **`cpaumelle/charliehub-hub2`**, on branch
**`agent/issue-44-canary-evidence`**, under `programme/issue-44/cohorts/`.

It is **not** in `cpaumelle/docplane`, and it is **not** on a default branch. Searching
the docplane default history for that identifier finds nothing — that is exactly the
earlier confusion over "commits" absent from repository history. Raw cohort receipts,
body-review transcripts and audit text live in that repository; this directory is the
structured shared surface built from them.

---

## Layout and file ownership

No file has two writers. Aggregates are generated, never hand-edited.

```text
programme/issue-44/
├── README.md                     agent-44
├── authority.json                agent-44
├── ledger/
│   ├── agent-44-receipt-ledger.json   agent-44  (immutable source evidence)
│   ├── publications/<pub-id>.json     agent-44  (immutable, one per move publication)
│   ├── moves.json                     agent-45  (generated: one row per resource ID)
│   └── dispositions.json              agent-45  (generated)
├── baseline/                     agent-45  (retrospective classified baseline)
├── status/                       agent-45  (generated aggregates)
├── handoff/publication-lane.json agent-44
└── audits/                       agent-45
```

**Agent 44 (publication owner)** — cohort execution, rehearsals and abandonment, move and
rewrite publications, certification, immutable receipt files, the publication lane.
Agent 44 must not hand-edit cumulative counts in `CURRENT.md`.

**Agent 45 (accounting and classification owner)** — independent receipt verification,
the move and disposition ledgers, the retrospective baseline, audits, generated status.
Agent 45 must not rehearse or publish while acting as accountant, and must not copy
cumulative totals from Agent 44's prose.

---

## Publication records

One immutable file per move-bearing publication, keyed by **full** publication ID. Each
records the full stable resource IDs, exact `from_path` and `to_path`, navigation
changes, redirect count, source-content corrections, reference rewrites, the paired
reference-repair publication, the rehearsal and its abandonment, the certification
transition, and the post-publication audit.

Five of the 21 predate the receipt-emitting runner. Their missing local fields are
listed explicitly in `_unavailable` rather than being back-filled or inferred. Everything
sourced from the live API is present for all 21.

### Agent 44's receipt-derived result, awaiting independent reconstruction

| | |
|---|---|
| move-bearing publications | **21** |
| move operations | **54** |
| unique stable resource IDs | **54** |
| pages moved more than once | 0 |
| unresolved `from_path` | 0 |
| already-at-destination moves | 0 |

Excluded from the count: `REPLACE_DOCUMENT` (rewrite-only), `PATCH_METADATA`
(metadata-only), `CREATE_PAGE`, `ARCHIVE_PAGE`, `RESTORE_PAGE`, `REMOVE_REDIRECT`, and
all ABANDONED changes (rehearsals — never applied).

Any discrepancy between this and Agent 45's independent reconstruction must be **reported
before** the aggregate count is changed.

---

## Regenerating the ledger from scratch

1. List published changes: `GET /api/v1/changes?limit=500`. Keep `status == PUBLISHED`.
2. Keep changes containing at least one `MOVE_PAGE` operation.
3. `MOVE_PAGE` payloads carry `to_path` but **not** `from_path`. For each moved page call
   `GET /api/v1/pages/<resource_id>/history`; the superseded version whose `change_id`
   equals the publication is the pre-move state, and its `path` is the exact `from_path`.
   This is a direct receipt link, not a timestamp heuristic.
4. Count unique `resource_id`, not operations, for "pages reorganised".

### API gotchas that have caused real measurement errors

- **`/api/v1/pages` silently truncates at `limit=500`.** The corpus is larger. Request
  `limit=2000` and assert `count == total` before trusting a snapshot. A truncated
  snapshot understates every inbound count.
- **`offset`, `page` and `cursor` are ignored** by the page listing. There is no pagination.
- **A link that resolves is not a link whose target exists.** Path resolution normalises a
  relative link regardless of whether the target page is active. A broken-link audit must
  test target existence, not resolution.
- **Evidence-surface links are deliberately preserved** and therefore point at pre-move
  paths on purpose. Those old paths are carried by redirects and serve HTTP 200; they are
  historical record, not defects.

---

## Publication-lane protocol

`handoff/publication-lane.json` records coordination only — it grants nothing. The live
API gates remain the real mutation authority.

- Only the lane holder may rehearse or publish.
- Agent 45 may update accounting while the lane is `released`.
- Before Agent 44 takes the lane again it must pull Agent 45's latest ledger/status commit.
- Before publication Agent 44 must regenerate all cohort membership and live-state inputs
  from a fresh snapshot. **Prepared manifests are provisional after any publication.**

---

## Related, deliberately kept separate

A DocPlane platform-security follow-up (self-issued agent credentials have no read-only
tier and cannot be self-revoked) is tracked as its own narrowly scoped issue. It is not
part of Issue #44 and must not be folded into this programme.
