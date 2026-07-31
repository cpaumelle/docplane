# operations/ reorg — Session 4 (docplane#108/#109 closure)

*2026-07-31. Closes both remaining follow-ups from #104. #107 (policy) and #112/#113
(platform follow-ups) are separately owned and untouched by this session.*

## Published (rehearsed, validated, published; certification CURRENT after each)

| Change | Change ID | Content |
|---|---|---|
| invariant-roadmap.md split | `56918b3b-26c5-4d53-af97-2afcd79d49e1` | CREATE_PAGE `control-plane/foundational/invariant-governance.md` (durable methodology, content-conservation verified) + REPLACE_DOCUMENT `operations/invariant-roadmap.md` (retitled, relifecycled REFERENCE→BACKLOG, pointer inserted) + REPLACE_DOCUMENT `operations/index.md` (link text) |
| 5 section landings | `01cdba89-5d1b-4904-8dba-947b7a44475f` | CREATE_PAGE × 5: `operations/{incidents,investigations,platform,runbooks,security}/index.md`, member lists verified against a fresh snapshot before publish |
| Tracker nav normalization | `2f9fac63-d209-4cba-abd5-cfb4101bfd4f` | REPARENT_NAV × 3: `in-flight.md`, `completed.md`, `engineering-backlog.md` → `Operations/Tracker/...`, matching the 2 already-correct siblings |

Full content-conservation manifest for the split: `programme/operations-reorg/session4/roadmap-split-manifest.md`.

## #108 — closed

- **`control-plane-enforcement.md`**: resolved in Session 3 (moved to `operations/platform/`, broken citation fixed).
- **`invariant-roadmap.md`**: resolved this session. Durable methodology (when to add an invariant, non-goals, design principles, maturity model — 4 spans, exact offsets, sha256-verified) extracted verbatim to `control-plane/foundational/invariant-governance.md`. The retained page (`operations/invariant-roadmap.md`, path unchanged) now carries only its status/backlog/history content, retitled "Invariant Enforcement Status & Backlog", relifecycled BACKLOG (was REFERENCE — the old marker misdescribed a living backlog tracker as stable reference material). Content-conservation proof: the retained page's body, with the pointer section and header edits reverted, is byte-identical to the original minus exactly the 4 extracted spans. 3 corpus-wide inbound references checked for anchor targeting into the removed sections — none found; all point at the page level and are unaffected by the path staying the same.

## #109 — closed

- **5 missing `index.md` landings**: created for `incidents` (6 members), `investigations` (3), `platform` (5), `runbooks` (37, grouped by reader purpose for navigability — alert-response / migration & cutover / platform maintenance / planning notes), `security` (8). Every member list generated from a fresh snapshot and verified to match exactly before publish, not typed from memory. House style matches the two index.md pages #104 already created.
- **`docplane-redaction-remediation-register.md`**: re-verified corpus-wide — still exactly 1 page matching this reader purpose (searched for `CRED-FAMILY`, `credrot`, `redaction_family_id`, "remediation register" across the full live corpus). **Disposition: retained deliberately in place.** No new subsection (still below the 3-page bar #109 itself set) and no split: unlike `invariant-roadmap.md`, this register's sections are functionally interdependent (its own text: "a family-level summary must NEVER override a more precise occurrence-level disposition") — splitting it would break its function as a single crosswalk ledger, not produce two independent reader jobs.
- **Tracker nav-tagging**: `in-flight.md`, `completed.md`, `engineering-backlog.md` nav_path corrected to `Operations/Tracker/...`, matching `soaks.md`/`pickup-history.md`. Verified before publish: 0 page/section conflicts, 0 duplicate nav_paths introduced (`find_page_section_conflicts` against the live corpus with the 3 changes simulated). All 5 pages' physical paths are unchanged — this is nav-metadata only, not a physical-move count.

## Final verification

Certification CURRENT, working==deployed, 0 open changes after every publish. `missing_index` signal: 0 `operations/*` entries remain (was 5). 0 duplicate routes, 0 page/section conflicts, 0 duplicate nav_paths corpus-wide. 0 stale references to any path this session moved (`operations/control-plane-enforcement.md` confirmed gone; its replacement and all 3 new/modified pages confirmed active and resolving 200).

## Final structural findings (fresh `corpus_structure.build()`, fingerprint `2145092b6ec17b55`)

| Directory | Score | Severity | Reasons |
|---|---:|---|---|
| `operations/` | 65 | HIGH | `HIGH_DIRECT_PAGE_COUNT`, `REPEATED_FILENAME_STEM` |
| `operations/incidents/` | 1 | LOW | `REPEATED_FILENAME_STEM` (pre-existing, unrelated to this session) |
| `operations/investigations/` | — | not a candidate | |
| `operations/platform/` | — | not a candidate | |
| `operations/runbooks/` | 39 | HIGH | `HIGH_DIRECT_PAGE_COUNT`, `REPEATED_FILENAME_STEM` |
| `operations/security/` | — | not a candidate | |
| `control-plane/` | 24 | HIGH | `HIGH_DIRECT_PAGE_COUNT`, `DUPLICATE_TITLE`, `REPEATED_FILENAME_STEM` |
| `control-plane/foundational/` | 12 | MEDIUM | `HIGH_DIRECT_PAGE_COUNT` |

**`operations/` still scores HIGH — this is not claimed as resolved.** Per #107's closure
(separate, closed issue), the remaining condition is a lifecycle/status-scoring
legibility gap (56 corpus-wide self-declared-archived/paused/backlog pages never
transitioned via `ARCHIVE_PAGE`), owned by #112 (link-safety gap blocking that
transition) and #113 (scoring legibility) — not by #108/#109, and not reopened here.

**`control-plane/` (24, HIGH) and `control-plane/foundational/` (12, MEDIUM) are
pre-existing conditions, not created by this programme.** `control-plane/foundational/`
was already over the `HIGH_DIRECT_PAGE_COUNT` threshold (11 direct pages) before this
session; placing `invariant-governance.md` there — the correct destination on the
evidence (§ existing doctrine home, no other page covers this methodology) — brings it
to 12. This is disclosed, not hidden: the destination choice has a small real cost, and
the alternative (a new single-page directory) was explicitly worse per the "no new
directory for one page" instruction. `control-plane/`'s own HIGH condition and
`DUPLICATE_TITLE` finding are unrelated to anything #108/#109 touched.

**Pre-existing, out-of-scope, not touched this session**: a corpus-wide sweep found
586 links resolving to targets absent from the corpus entirely, overwhelmingly
originating from pages physically under `archive/` (old workstream records with
stale internal cross-references) and pointing at a `control-plane/specs/*` cluster
that appears to have never existed or was removed earlier. None originate from
content this session edited (confirmed: the only touched-page hits are from
`nav_path`-only changes, whose content was never modified). This is a real, separate
documentation-debt finding — not created or claimed-fixed here, flagged for a future
follow-up, not folded into #108 or #109's closure.

## Categorized outcome

- **Physical pages moved**: 1 (`control-plane-enforcement.md`, Session 3)
- **Pages split** (content-conservation verified): 1 (`invariant-roadmap.md` →
  retained status page + new `invariant-governance.md`)
- **Pages created as section landings**: 5 (`incidents`, `investigations`, `platform`,
  `runbooks`, `security` `index.md`)
- **Navigation-only corrections** (no physical move): 6 total across Sessions 3–4
  (`w4-1-5c-g1a-evidence.md`, `w4-1-5c-g1a-execution-plan.md`, `filesystem-hardening.md`,
  `in-flight.md`, `completed.md`, `engineering-backlog.md`)
- **Pages deliberately retained, no action**: `docplane-redaction-remediation-register.md`
  (re-verified below the subsection threshold); `ccm-interface-realization-capability.md`
  and `cp-net-1-history.md` (Session 3, verified already correctly placed and current)
- **Lifecycle/authoring follow-ups still outside these issues**: #112, #113 (both
  independent, unimplemented); `control-plane/`'s own HIGH condition and
  `DUPLICATE_TITLE` finding; the 586 pre-existing broken references under `archive/`
  and `control-plane/specs/*`

## Closing statement

The document-placement and navigation reorganisation work represented by #108 and
#109 is complete. `operations/` remaining HIGH is a separately-owned lifecycle/scoring
concern (#112, #113, both closed-#107-adjacent, both untouched here) — not
unexplained reorganisation debt. `control-plane/`'s own HIGH condition predates and is
unrelated to this programme.
