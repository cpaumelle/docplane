# operations/ reorg — Session 3 (docplane#108/#109 partial resolution)

*2026-07-31. Follow-up to #104 (closed) and #107 (closed). Scoped to named-page
document resolution, not further archive-platform tooling — see [[project_lifecycle_policy_107]]
memory note on why #107 was not reopened for this.*

## Published (rehearsed, validated, published; certification CURRENT after each)

| Change | Change ID | Content |
|---|---|---|
| Move + nav corrections | `e2cfa1c2-890e-425f-938f-2666e3b2a940` | MOVE_PAGE `control-plane-enforcement.md` -> `operations/platform/` + REPARENT_NAV x3 (`w4-1-5c-g1a-evidence.md`, `w4-1-5c-g1a-execution-plan.md` -> `Archive/Evidence/...`; `filesystem-hardening.md` -> `Operations/Security/...`) |
| Own-content link fix | `8b55f613-f1f7-4c79-b6bc-65bfa6269686` | REPLACE_DOCUMENT — fixed the pre-existing broken `control-plane/authority-model.md` citation (real path: `control-plane/foundational/authority-model.md`) on the moved page, per #108 |

**Verification**: certification CURRENT, working==deployed, 0 open changes. Old route
(`operations/control-plane-enforcement/`) and new route
(`operations/platform/control-plane-enforcement/`) both 200. Fresh
`corpus_structure.build()`: `operations/` direct 63->62, score 66->65, still HIGH
(expected — a single page was never going to resolve that; see #107's closure).
`operations/platform/` now 5 members, not a review candidate.

## Resolved this session

- **`operations/control-plane-enforcement.md`** (#108) — re-checked the claimed
  supersession by `control-plane/foundational/enforcement-model.md` once more; still
  not corroborated (no new evidence surfaced beyond #104's original finding). Moved to
  `operations/platform/` as ordinary orphaned-but-current reference material; broken
  citation fixed.
- **3 nav_path corrections** (#109) — `w4-1-5c-g1a-evidence.md` /
  `w4-1-5c-g1a-execution-plan.md` to `Archive/Evidence/...`; `filesystem-hardening.md`
  to `Operations/Security/...`. All metadata-only, no physical moves.

## Reviewed, no action needed (verified current and correctly placed)

- **`control-plane/ccm-interface-realization-capability.md`** — read in full (23KB).
  Proven-in-production capability record (Paris canary, 6F/6G/6H passed 2026-05-28/29),
  actively cross-linked from the live `operations/in-flight.md` tracker, with 7 of 8
  W4 follow-ups still genuinely open (only W4-1 closed). Correctly placed under
  `control-plane/`, lifecycle marker (`REFERENCE`) reasonable for a completed-capability
  record still serving as the canonical account. No move or archive warranted.
- **`control-plane/cp-net-1-history.md`** — read in full (62KB). Explicitly self-described
  as an append-only history/archaeology ledger, companion to `cp-net-1/overview.md`
  (architecture) and `cp-net-1-driver.md` (current execution state) — by its own stated
  design, closed-phase outcomes get appended back into this doc, so it stays `status=active`
  while its `**Lifecycle:** ARCHIVED` marker correctly describes its *content* (closed
  phases), not its maintenance state. Correctly placed, correctly marked for its own
  documented convention. No action needed.

## Not attempted — precise blockers recorded, not forced

- **`operations/invariant-roadmap.md`** (#108) — needs a content split before any move
  (durable methodology vs. a dated Phase 0-5 status table), with control-plane/ owner
  review on where each half lands, per #108's own text. Proposed split boundary posted
  to #108 rather than executed unilaterally: durable = *Why this is on the roadmap*,
  *When to add a new invariant*, *Explicit non-goals*, *Design principles*, *Maturity
  model*; dated/status = *Current state*, *Filed but not yet implemented*, all of
  Phase 0-5, and the evidence appendices.
- **#109 remaining items** — 5 missing `index.md` landing pages, the
  `docplane-redaction-remediation-register.md` new-subsection question (still only 1
  page named, below the 3-page bar), and the 5-item Tracker/board nav-tagging
  inconsistency. These are content-authoring or subsection-creation tasks, not metadata
  corrections, kept separate rather than folded into this pass's deterministic fixes.
