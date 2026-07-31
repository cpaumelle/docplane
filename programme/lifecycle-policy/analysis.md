# Corpus-wide lifecycle/governance placement policy — analysis for docplane#107

*Fresh baseline 2026-07-31. Independent of docplane#104 (operations/ reorg, CLOSED) and
its Session-3 follow-ups (#108, #109). This is the policy question #104 deliberately
deferred rather than resolving by fiat.*

## 0. Correction to #107's opening premise

#107 was opened stating "only 2 pages in the entire 590-page corpus physically live
under an `archive/` path." That count was scoped to `status=active` pages only (the
API's default filter) and is **wrong once the full corpus is counted**. A fresh
`GET /api/v1/pages?status=all` fetch returns **1027 pages total: 582 active + 445
archived** — the corpus is not 590 pages, it is 1027, and DocPlane already has a
working, heavily-used DB-level archive mechanism (43% of the corpus). Of those 1027
pages, **27 physically live under an `archive/` path** (mixed status), not 2. This is a
scoping correction, not a blame — the prior figure was computed honestly against the
default active-only listing. Any future corpus-size claim should state whether it
includes archived pages.

## 1. Current-behaviour matrix (read from source, not asserted)

Read in full: `docs-api/app/corpus_structure.py`, `app/publication.py`, `app/agent_api.py`,
`app/runtime.py`, `app/generator.py`, `app/operation_contract_api.py`,
`tests/test_redirect_serving.py`.

| Surface | Behaviour, verified from source |
|---|---|
| **Structured lifecycle field** | There is no such field. `docs.pages.status` is a **binary** `active`/`archived` DB column (`publication.py`). The 6-value vocabulary the corpus actually uses (`REFERENCE, OPERATION, ACTIVE, PAUSED, BACKLOG, ARCHIVED`) is a **content convention**: a `**Lifecycle:** X` bold-text line or `<!-- lifecycle: x -->` comment, regex-parsed by `corpus_structure.lifecycle_of()`. It is informational only. |
| **Navigation** | `runtime.deploy_current_state()` filters `active = [p for p in pages if p["status"]=="active"]` and calls `generator.run(active, ...)` — **only DB-active pages are ever rendered into nav or the site.** Archived pages produce no output at all. |
| **Search (site)** | `search.build_index(active)` — same filter, archived pages are never in the shipped search index. |
| **Search (API)** | `GET /api/v1/search` defaults to active-only but accepts `include_archived=true` — archived pages ARE reachable through the authoring API's own search, just not the default. |
| **Default listing** | `GET /api/v1/pages` defaults to `status=active`; `status=all`/`status=archived` are explicit opt-ins. |
| **Direct retrieval** | `GET /api/v1/pages/{resource_id}` has **no status filter at all** — an archived page's content, history and identity remain fully retrievable by ID regardless of status. Stable identity is preserved (matches invariant). |
| **Route on archive** | `ARCHIVE_PAGE` (`publication.py:402`) does exactly one thing: `status="archived"`, `publication_state="ARCHIVED"`. **No path change, no redirect, no tombstone.** Since only active pages render, the old route simply stops existing in the next release — a **silent 404**, not a redirect and not a banner. |
| **Restore** | `RESTORE_PAGE` flips back to `status="active"`; because the path never changed, the original route reappears automatically. Reversible, no metadata reconstruction needed — matches the reversibility invariant. |
| **Redirects (MOVE_PAGE)** | Fully implemented and tested (`test_redirect_serving.py`, docplane#65 regression cover): `create_redirect` on `MOVE_PAGE` records `docs.redirects`, folded into the mkdocs-redirects plugin config, validated against chains/cycles/target-missing/self-redirect/shadowing-an-active-page. This machinery **only fires on MOVE_PAGE**, never on `ARCHIVE_PAGE`. |
| **Inbound-link validation on archive** | **None.** Confirmed by reading the full `ARCHIVE_PAGE` apply path and operation contract: no check that other active pages reference the page being archived. Verified empirically below (§7) — this is a real, exploitable gap, not theoretical. |
| **Certification / working==deployed** | `state_identity()` hashes `(resource_id, path, nav_path, revision, status)` for **every** page including archived ones — an archive/restore transition is captured and auditable in the state hash and deployment history. Certification does not check link health; that's a separate, unlinked audit step (`migration/links.py`, run manually). |
| **Structural-review scoring input** | `corpus_structure.build()` computes `direct_pages`/`HIGH_DIRECT_PAGE_COUNT`/`REPEATED_FILENAME_STEM` etc. **only from `status=active` pages** — DB-archived pages are excluded from `directories[...]` membership entirely, and from `review_candidates` scoring, except for the one dedicated `ARCHIVED_ONLY_SECTION` reason (a directory with archived descendants and zero active descendants). |
| **Body-content lifecycle vs scoring** | The parsed `**Lifecycle:**` value is **never consumed by any scoring reason**. It only feeds two purely informational `signals`: `missing_lifecycle`, `unknown_lifecycle`. The generator's own self-description states `"legacy_lifecycle_canonical": False` — the generator authors already flag this signal as non-canonical. |
| **knowledge_class** | A genuine structured DB column (`docs.pages.knowledge_class`), settable via `PATCH_METADATA`, used in facets and maintenance policy — orthogonal to lifecycle, not itself examined further here (out of scope; #104 already used it correctly for one EVIDENCE reclassification). |

**Conclusion of §1**: the corpus already has exactly the two-tier structure the user's
central question poses ("physical paths stay domain-based, DocPlane distinguishes
deliberately-retained material") — via `status`. It is real, working, and used at scale.
The open question is not whether to build it; it's why it isn't being used for the
material that's inflating `operations/`'s score, and what's missing to make it safe to use.

## 2. Corpus-wide ledger (the actual scope, not the #104-era estimate)

Fresh `status=all` fetch, full body content for all 1027 pages, `corpus_structure.build()`
re-run live (fingerprint `78db6794ba709639`).

| | count |
|---|---:|
| Total pages | 1027 |
| Active (`status=active`) | 582 |
| Archived (`status=archived`) | 445 |
| Active pages missing a `**Lifecycle:**` marker | 47 |
| Active pages with an unrecognised marker value | 0 |
| Pages physically under an `archive/` path (any status) | 27 |
| Pages with `nav_path` starting `Archive/` | 73 (26 of those also physically under `archive/`; 47 are nav-only) |

**The load-bearing cross-tab** — active pages whose body already self-declares a
retired/paused state that the DB status has never been updated to match:

| body-declared lifecycle | active pages, corpus-wide | of which in `operations/` |
|---|---:|---:|
| `ARCHIVED` | 61 | 28 |
| `BACKLOG` | 22 | 8 |
| `PAUSED` | 4 | 3 |
| **total mismatch** | **87** | **39** |

`operations/` direct-root alone (63 currently active direct pages) accounts for 39 of
these 87 — **62% of the pages holding `operations/` at HIGH are pages whose own authors
already declared them archived, paused, or backlogged**, while the DB row backing them
still says `active`. This is not distributed evenly across the corpus: `operations/`
(56 total incl. nested), `services/` (11), `control-plane/` (10), `network/` (6) are the
only sections with a material count.

Full per-page ledger (path, status, lifecycle marker, knowledge_class, nav_path) is at
`/tmp/docplane-lifecycle-policy-audit/full-ledger.json` on hub2; committed alongside this
report at `programme/lifecycle-policy/manifest/full-ledger.json`.

## 3. Policy-model evaluation

### Model A — domain path stays authoritative; `status` (already existing) governs visibility
**This is not a proposal — it is what's already implemented and already carries 445
pages.** Nothing new to build. Risk profile matches the user's stated advantages
exactly: stable paths, subject context preserved (a page never leaves its owning
domain directory), trivial restoration (flip one field back).

### Model B — physical lifecycle relocation (`operations/archive/`, or similar)
**Rejected, on direct evidence, not by assumption.** The corpus already tried this
pattern at `archive/` (27 pages: `archive/plans/*`, `archive/services/*`,
`archive/reports/*`, `archive/guides/*`, `archive/network/*`,
`archive/operations/idnet-migration.md`) and it exhibits exactly the risk the user's
brief warned about: a heterogeneous dumping ground with no domain cohesion
(`archive/services/ct1112-postgresql.md` sits next to `archive/network/ux-sarah-office.md`
next to `archive/guides/isp-testing-technicolor-bridge.md` — a PostgreSQL service record,
a UX/wifi placement note, and an ISP test procedure, unrelated except by age). Simulating
a corpus-wide status-fix (§4) shows the mechanism problem this creates in miniature: once
`archive/plans/` is fully reconciled to `status=archived` it immediately trips a NEW
`ARCHIVED_ONLY_SECTION` finding (15 MEDIUM) — a physical lifecycle namespace manufactures
its own structural-review noise. Model B would repeat this at `operations/` scale
deliberately. Not recommended.

### Model C — separate active/records planes
Evaluated and found to be a description of Model A, not a distinct model: `status` already
IS a separate projection axis, orthogonal to path, that the generator/search/nav layers
already treat as an active/records split. Building a second, parallel mechanism (new
content type or storage plane) would duplicate what `status` already does. Not recommended
as an addition; worth naming explicitly in documentation so future contributors don't
propose reinventing it.

### Model D — hybrid, per-class rules
Partially correct, but the axis is wrong. The user's hybrid draft splits by *document
class* (archived reference vs. evidence vs. active project vs. paused work). The evidence
here shows the real split that matters is **semantic finality**, not class:
- `ARCHIVED` (genuinely dead/superseded) → safe to flip `status`. No information is lost;
  nothing needs it discoverable by default.
- `BACKLOG` / `PAUSED` (still open, intentionally not being worked this cycle) →
  **flipping `status` is actively harmful.** These pages need to stay searchable/navigable
  so the open work isn't lost — DocPlane has no "backlog board" surface other than an
  active page in nav. Treating BACKLOG/PAUSED the same as ARCHIVED would hide live,
  unresolved work from default discovery.

## 4. Invariants (evidence-checked against the user's draft list)

| Invariant | Status against current behaviour |
|---|---|
| Domain context preserved on lifecycle transition | **Holds** under Model A (path unchanged by `ARCHIVE_PAGE`); would NOT hold under Model B (proven by the existing `archive/` tree). |
| Stable identity | **Holds** — `resource_id` and full revision history survive archive/restore; `GET /pages/{id}` always works regardless of status. |
| Canonicality (no two active canonical copies) | **Holds** — archive/restore is a status flip on one row, never a copy. |
| Link safety | **Does NOT hold today.** `ARCHIVE_PAGE` has no inbound-reference check (§1, §7). This is the one invariant the current implementation actually violates. |
| Evidence integrity | **Holds** — archiving never rewrites content. |
| Reversibility | **Holds** — `RESTORE_PAGE` needs no metadata reconstruction, path is untouched. |
| Structural-review honesty | **Does NOT hold today.** The generator has the data (`legacy_lifecycle_signal` per directory) but doesn't score it, and explicitly flags it non-canonical. `operations/` reports HIGH for reasons the generator itself can already compute but doesn't surface as a distinct, actionable reason. |
| No lifecycle dumping ground | **At risk under Model B**, evidenced by the existing `archive/` tree; **not at risk under Model A**, since nothing physically moves. |

Two invariants fail today, both independent of which model is chosen: **link safety**
and **structural-review honesty**. Fixing them is the actual #107 deliverable — not a
model choice, since Model A is already in place.

## 5. Recommended model and rationale

**Retain Model A (existing domain-path-plus-`status` architecture). Do not build a new
physical or schema mechanism.** The evidence is direct, not inferred: the mechanism
already carries 445 pages corpus-wide; a physical alternative (`archive/`) already
exists in miniature and already shows Model B's predicted failure mode; and simulating
the fix (§7) resolves the majority of `operations/`'s HIGH condition using nothing but
the existing `ARCHIVE_PAGE` operation.

This closes the user's central question directly: **lifecycle state should change
default visibility, not physical path** — and DocPlane's schema already agrees. The
`operations/` HIGH condition is a **generator/policy mismatch compounded by an
operational-discipline gap**, not missing architecture:
1. Authors/agents have been writing `**Lifecycle:** ARCHIVED/PAUSED/BACKLOG` in prose
   without ever issuing the corresponding `ARCHIVE_PAGE` operation.
2. The generator doesn't score that mismatch, so nothing forced the reconciliation.
3. `ARCHIVE_PAGE` itself has a real safety gap (no inbound-link check, no tombstone)
   that would make blindly correcting (1) unsafe.

## 6. Structural-review scoring proposal

Two additive changes to `corpus_structure.py`, both non-destructive (no existing reason
code removed or reweighted):

1. **New reason code, e.g. `LIFECYCLE_STATUS_MISMATCH`**: for a directory, count active
   pages whose parsed `lifecycle` is `ARCHIVED` (BACKLOG/PAUSED excluded — see §3 Model D
   finding, they are not mismatches). Report it as its own reason, distinct from
   `HIGH_DIRECT_PAGE_COUNT`, so a candidate's score legibly separates "pages that need an
   `ARCHIVE_PAGE` operation" from "pages that need an actual placement decision."
   Do **not** silently subtract these from `HIGH_DIRECT_PAGE_COUNT`'s raw count — keep
   both numbers visible (per the user's explicit "do not simply suppress" instruction):
   ```
   operations/: score 66, HIGH
     HIGH_DIRECT_PAGE_COUNT        measured=63  (raw)
     LIFECYCLE_STATUS_MISMATCH     measured=28  (of which are ARCHIVED and reconcilable now)
     REPEATED_FILENAME_STEM        measured=3
   ```
2. **Refine `ARCHIVED_ONLY_SECTION`**: exempt directories whose own path is (or is
   nested under) a directory whose sole documented purpose is archival, OR — better,
   since path-based exemption risks becoming its own dumping-ground signal — split the
   reason into `ARCHIVED_ONLY_SECTION` (an ordinary working directory that emptied out,
   worth a human look) vs a non-actionable `INFO`-severity `ARCHIVE_PURPOSED_SECTION`
   for directories under a corpus-documented archive namespace. This must be resolved
   deliberately (documentation first, per doctrine), not silently threshold-tuned.

Both changes are additive/informational; neither changes what gets rendered, searched,
or archived. They only make the existing HIGH score legible — replacing "deliberately
retained, trust us" with a machine-checked, re-runnable number.

**On the `DIRECT_PAGE_THRESHOLD=10` question**: even after the full 39-page mismatch is
corrected, `operations/` direct-root drops only to 24 (still >10); after the safe
28-page (ARCHIVED-only) correction, to 35. The threshold itself remains a meaningful
cheap trigger for "look at this directory" — recalibrating it would mask real structure.
What needs recalibrating is not the threshold but the *severity arithmetic feeding off
of it*, once (1) above exists and is used.

## 7. Representative-page simulation (live `corpus_structure.build()`, not asserted)

All three simulations below ran the actual production module
(`docs-api/app/corpus_structure.py`, unmodified) against the fresh live corpus with
in-memory `status` overrides — no page was mutated.

**Corpus-wide, all 87 mismatches (ARCHIVED+BACKLOG+PAUSED) corrected:**
review_candidates go from 42 (11 HIGH/10 MEDIUM/21 LOW) to 47 (11 HIGH/12 MEDIUM/24 LOW).
`operations/` 66→24 HIGH, `control-plane/` 23→15 (drops out of HIGH), `services/trevarn`
20→17 (drops out of HIGH), but **6 new `ARCHIVED_ONLY_SECTION` findings appear**
(`archive/` 26 HIGH, `archive/plans/` 15 MEDIUM, `operations/investigations` 3 LOW,
`services/contractor-access` 1 LOW, `services/parking` 4 LOW, `test/` 30 HIGH) — exactly
the §6 gap, confirming it's real and not hypothetical. **This is why the safe subset
below, not the full 87, is the actual recommendation.**

**`operations/` only, safe subset (28 pages, `ARCHIVED` marker only, BACKLOG/PAUSED
excluded per §3):**
```
operations/: 66 HIGH -> 36 HIGH
  HIGH_DIRECT_PAGE_COUNT   63 -> 35
  REPEATED_FILENAME_STEM    3 ->  1   (paris-* and w4-1-5c-g1a-* families fully resolved)
```
Still HIGH (35>10), but the two repeated-stem families this programme's #104 closure
called out as unexplained flatness are gone — because they were never "deliberately
retained," they were unreconciled self-declared-archived clusters.

**Link-safety check on the 28-page safe subset** — this is the finding that changes the
recommendation from "just run it" to "fix the gap first, then run it":
**26 of 28 have live inbound references from other active pages** (overwhelmingly from
`operations/index.md`'s own catalogue, plus cross-references from `control-plane/`,
`network/`, `services/`, `reference/`). `ARCHIVE_PAGE` has no check for this (§1) — running
it today on these 26 would silently 404 every one of those links, with no tombstone and
no automatic redirect. This is exactly the user's §5 "link safety" and §8 "no plain 404"
concerns, demonstrated on real data, not hypothesised.

Only **2 of the 28** have zero detected inbound references from other active pages:
`operations/paris-cutover-readiness-assessment.md` and
`operations/paris-identity-inventory-analysis.md`. These are the low-risk, reversible
canary candidates called for in the user's brief.

## 8. Archive route behaviour — decision needed before any bulk correction

Current: silent 404, no tombstone, no redirect, not blocked by any validation. This is
**not acceptable** per the user's own stated bar, and independently confirmed unsafe by
§7's inbound-link data. Two ways to close the gap, either sufficient on its own:

- **(a) Process-only, no code change**: treat every `ARCHIVE_PAGE` operation like a
  "move to nowhere" — require a companion reference-repair pass in the same governed
  change (same two-phase pattern #104 already used for physical moves), computed from a
  fresh `find_links` sweep before publish. Zero schema/API change; purely an operational
  discipline change, enforceable today.
- **(b) API-level safety net**: add an inbound-reference check to `ARCHIVE_PAGE`,
  mirroring `ADD_REDIRECT`'s existing `REDIRECT_SOURCE_IS_ACTIVE_PAGE` pattern — reject
  (or warn-and-require-force) archiving a page with live inbound references. This is a
  real code change requiring its own scoped implementation issue; out of scope for this
  policy pass to build unreviewed.

Recommendation: adopt (a) immediately as operating discipline for any archive work
(including the canary below), and open (b) as a scoped follow-up implementation issue
since it closes the gap structurally instead of relying on every future operator
remembering to check.

## 9. Canary proposal (not yet executed — requires approval)

Archive the 2 zero-inbound `operations/` pages identified in §7
(`paris-cutover-readiness-assessment.md`, `paris-identity-inventory-analysis.md`) via a
single governed change: `ARCHIVE_PAGE` × 2, full rehearse → validate → publish → verify
cycle, re-run `corpus_structure.build()` live afterward to confirm the forecast
(operations/ direct 63→61, no severity change expected — this is a mechanism proof, not
a scoring fix by itself). Rollback: `RESTORE_PAGE`, single operation, path unchanged.

**Not executed in this session** — per the explicit constraint not to begin bulk
lifecycle relocation in the same step as policy discovery, and per this project's
standing planning-contract requirement to get approval before any publish, even a
2-page reversible one.

## 10. Migration impact if the full safe-subset correction (28 pages) is later approved

- 28 `ARCHIVE_PAGE` operations, zero path changes, zero new pages, zero schema change.
- Requires: a reference-repair pass first (§8a) — computed via `migration/links.py
  find_links` against the live corpus, likely touching `operations/index.md` (nearly
  every one of the 26 inbound hits originates there) plus ~15 other active pages across
  `control-plane/`, `network/`, `services/`, `reference/`.
- `operations/` direct-root: 63 → 35. Severity: HIGH → HIGH (unchanged tier, score 66→36).
  `REPEATED_FILENAME_STEM`: 3 families → 1.
- Fully reversible per page (`RESTORE_PAGE`), independent of every other page in the
  batch — no batch-level rollback dependency.

## 11. Documentation updates needed regardless of implementation timing

- Correct #107's own opening premise (§0) — the "2 of 590" claim.
- Document `status` as the authoritative lifecycle-visibility mechanism in whatever
  doctrine page governs DocPlane content conventions — it is currently undocumented as
  a policy, only implemented as a mechanism.
- Document the `archive/` physical tree's actual (heterogeneous, not domain-scoped)
  usage so it isn't mistaken for an endorsed convention by a future contributor.

## 12. Follow-up issues if implementation proceeds

- **New, scoped**: add inbound-reference validation to `ARCHIVE_PAGE` (§8b) — API/generator
  change, its own review cycle.
- **New, scoped**: `LIFECYCLE_STATUS_MISMATCH` reason code + `ARCHIVED_ONLY_SECTION`
  refinement in `corpus_structure.py` (§6).
- Existing #108 (control-plane-enforcement.md / invariant-roadmap.md canonicality) and
  #109 (nav hygiene) are unaffected by this analysis and remain independent.

## Final statement

The remaining `operations/` HIGH score is **not unexplained structural debt and not a
missing physical-path convention** — it is a **generator/policy legibility gap** (the
generator already has the signal, doesn't score it, and calls it non-canonical) **plus
an operational-discipline gap** (56 corpus-wide, 39 direct-root pages self-declared
archived/paused/backlogged in prose, never transitioned through the mechanism that
already exists and already carries 445 other pages). Of the 63 current direct-root
pages, a corrected, safe (ARCHIVED-only, inbound-link-checked) pass would move 28,
leaving 35 — still over the review threshold, appropriately, since 35 genuinely active
or open pages in a domain root is itself a legitimate (if separate) finding. Zero pages
need to physically move under this recommendation. Future reorganisation programmes
should count their denominator as **direct pages with `status=active` AND no
`ARCHIVED`-marker mismatch** — not raw direct-page count — once §6's reason code exists.


## Addendum (2026-07-31) — canary preflight result: both candidates rejected

Policy decision approved (this document, PR #111 merged `8b5b414`); follow-ups #112
(link-safety/route semantics) and #113 (scoring) opened. Proceeding to the two-page
zero-inbound canary surfaced a real content-level abort condition — reported here
rather than overridden.

### Manual content preflight on the two proposed candidates

`operations/paris-cutover-readiness-assessment.md` (`45b0c207-fb53-5e19-b5ae-cd50de4fb8aa`)
and `operations/paris-identity-inventory-analysis.md`
(`0df2184e-1e62-503b-866c-5b8b967ac4df`) both confirm **zero live inbound references**
from other active pages (unchanged from §7's finding — re-verified fresh, not stale).

Reading both full bodies, however, surfaces a distinct problem neither the link graph
nor the lifecycle marker alone would catch: **both pages are explicit, hand-authored
redirect stubs.** Each carries identical boilerplate: *"Retained as a stub so existing
links resolve... Do not cite this page as authority."* One points to
`paris-cutover-runbook-v1.md#phase-0-readiness-assessment...`, the other to
`pi4-gateway-appliance-v1-inventory.md#scenario-2-identity-disposition...` and
`paris-pi4-identity-inventory.md`. This phrase is unique to these two pages corpus-wide
(checked: no other page uses it).

Their stated purpose is explicitly **public-route preservation** — not a dependency on
any other DocPlane page (already confirmed zero), but on the *route itself* continuing
to resolve for inbound links this corpus's own `find_links` sweep cannot see: external
bookmarks, historical chat/commit references, or links from outside the corpus. `find_links`
only covers references authored inside the corpus; it was never capable of detecting
this class of dependency, and reading the page bodies was the only way to catch it.

This is precisely the abort condition specified in §4 of the approved plan: *"no
evidence-preservation dependency requiring the public route."* Both candidates fail it.
`ARCHIVE_PAGE` today creates no tombstone and no redirect (§1) — archiving either page
would produce a silent 404 for exactly the audience its own content says it's being
kept to serve. Archiving these two pages would not just risk breaking a link; it would
directly contradict the disposition their author already recorded in the page itself.

**No `ARCHIVE_PAGE` operation was executed.** Both pages remain `status=active`,
unchanged, at revision as fetched.

### Consequence for the canary

The 28-page safe pool (§7) has exactly these 2 pages as its only zero-inbound members;
the other 26 all have live internal inbound references and are correctly held back
already (remediation ledger below). There is currently no `operations/` page that is
simultaneously (a) zero internal inbound, (b) genuinely disposable at the route level,
and (c) marked `ARCHIVED`. The canary cannot proceed against `operations/` on the
current pool without either revising these two pages' disposition or accepting the
residual external-route risk explicitly.

This is itself useful evidence for #112: a page can pass every corpus-internal safety
check and still have a public-route dependency invisible to the link graph. #112's scope
should explicitly cover this case (e.g., a page-level "route must remain resolvable"
flag, distinct from ordinary inbound-reference counting) alongside the inbound-link
validation already scoped there.

## Remediation ledger — the 26 held-back `operations/` pages

Full detail (resource ID, inbound sources, declared lifecycle) at
`programme/lifecycle-policy/manifest/remediation-ledger-26.json`. All 26 remain
`status=active`, untouched. Summary: **25 of 26 are referenced from
`operations/index.md`'s own catalogue** (the section landing page cataloguing
everything under `operations/`); the remaining cross-references are scattered across
`control-plane/`, `network/`, `services/`, `reference/`, and a handful of sibling
`operations/` pages (`completed.md`, `engineering-backlog.md`, `in-flight.md`,
`soaks.md`, `w4-1-x-docs-refresh-hold-point.md`). None has been assigned a disposition
yet (repoint / retain-access / tombstone-link / historical-citation / authoring-decision)
— that decision is blocked on #112 landing (or, per-page, on an explicit override
decision), consistent with "bulk archival may begin only after Issue A supplies safe
semantics or every inbound edge has been deliberately resolved."
