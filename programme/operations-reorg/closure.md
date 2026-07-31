# operations/ reorg — closure record

*docplane#104. Sessions 1 and 2 published (Session 3 is deliberately deferred to
separate follow-up issues, not part of this closure).*

## Publications (all rehearsed, validated, published; certification CURRENT after each)

| Change | Change ID | Content |
|---|---|---|
| Session 1 — direct-root moves | `b5032f0b-8b44-4a09-ad37-2cab529ec024` | 3 MOVE_PAGE (b5a-fr-discovery→investigations/, cp-net-1-phase3-host-resolver→runbooks/, f-bootstrap-dispatch-1→follow-ups/) + CREATE_PAGE follow-ups/index.md + CREATE_PAGE runbooks/unifi-express-l3-adoption.md (content-conservation extraction) + REPLACE_DOCUMENT unifi-express-performance.md (pointer) + 4 inbound-reference repairs |
| Session 1 — own-link depth fix | `aad09788-7cc6-422b-9a8d-ec788724ea77` | 3 REPLACE_DOCUMENT (moved pages' own relative outbound links, computed via `plan_source_move`) |
| Session 2 cohorts B+C | `c752294b-96a9-47d8-8cb2-5d8b4907e444` | 4 MOVE_PAGE (break-glass-procedure + wg-key-rotation-split-recovery → security/; postmortem + transit-apply-syncconf-on-absent → incidents/) + 1 PATCH_METADATA (knowledge_class=EVIDENCE) + 1 ADD_REDIRECT (repoint a stale existing redirect chain) + 6 inbound-reference repairs |
| Session 2 cohort A | `9c439889-bb38-4223-8882-428e99bb658c` | 4 MOVE_PAGE (edge-cache-bootstrap, fr-ccm-lan-onboarding, france-manual-failback, hub2-transit-bootstrap → network-fabric/edge-transit/, new subsection) + CREATE_PAGE index.md (mandatory landing, verified live against `missing_index`) + 10 inbound-reference repairs |
| Session 2 cohort A — own-link depth fix | `70796228-6d85-46df-bb27-3cd507fb1cfe` | 4 REPLACE_DOCUMENT (moved pages' own relative outbound links) + docs.charliehub.net (retired host) citation repointed to the current docplane.charliehub.internal corpus |
| Session 2 corrective | `20e6db82-64eb-4b7d-998e-e8c128abb79e` | 1 REPLACE_DOCUMENT — fixed a real bug caught by the post-publish full-corpus stale-reference sweep: `wg-key-rotation-split-recovery.md`'s own content had 2 unfixed sibling links (transit-core-break-glass.md) and 3 references to the postmortems page that moved to incidents/ in the same session — a cross-cohort blind spot in the original cohort-B+C repair list, not caught before publish, found and fixed by verification after |

**11 pages physically moved** across the two sessions (3 direct-root + 8 nested), 3 new
pages created, 1 page content-split (unifi-express-performance.md), 1 metadata patch,
1 redirect chain repaired, 20 bounded inbound-reference repairs, 2 own-content depth
fixes.

## Final verification (fresh corpus fetch, live `corpus_structure.build()`, not asserted)

| | direct pages | score | severity |
|---|---:|---:|---|
| Baseline | 66 | 69 | HIGH |
| **Final (matches plan §5 forecast exactly)** | **63** | **66** | **HIGH** |

- `operations/runbooks/`: 35→31 direct, score 37→32 HIGH (`REPEATED_FILENAME_STEM`
  `wg-*` resolved to 2 members — no longer triggers).
- `operations/network-fabric/edge-transit/`: 5 direct pages (4 + index), **not a review
  candidate**, `missing_index` empty — confirmed live, the defect v3 caught before
  publish never materialized.
- `operations/follow-ups/`: 2→4 direct, `missing_index` empty (index bundled).
- `operations/security/`: 6→8 direct. `operations/incidents/`: 4→6 direct.
  `operations/postmortems/`: dissolved, 0 pages, directory gone.
- `find_duplicate_routes`: 0. `find_page_section_conflicts`: 0.
- Full-corpus stale-reference sweep (all 11 old paths, `find_links` against live body
  content): 0 remaining, after the one corrective fix above.
- Certification: CURRENT, working==deployed, state_version 191.
- All 11 old routes and all new/moved routes verified 200.

## Closing statement (per approved plan §5 — do not imply the structural condition is resolved)

All currently confirmed ordinary durable misplacements were resolved under the existing
lifecycle/path conventions: 3 direct-root pages moved to their body-confirmed existing
subsections, 1 page split (content-conservation extraction) rather than moved whole,
8 nested pages reconciled into their correct subsection (including a new,
nav-precedent-justified `network-fabric/edge-transit/` subsection with its own mandatory
landing page), and 1 candidate (`control-plane-enforcement.md`) deliberately held out
pending a canonicality question this review could not resolve from the evidence
available.

**The `operations/` structural problem itself was not resolved** — `corpus_structure.py`
still reports HIGH (score 66, down from 69). The remaining condition is dominated by
deliberately root-retained lifecycle and governance material (archived, board/backlog/
hold/authoring, one thin landing page) for which no physical-relocation convention
currently exists anywhere in the 590-page corpus (verified: only 2 pages corpus-wide
physically live under an `archive/` path, both unrelated to operations). That is a
separate, ownable policy question — opened as its own issue below, not silently
absorbed into this closure.

## Follow-ups opened separately (Session 3 — not resolved by this closure)

- `control-plane-enforcement.md` canonicality (§2a) — the claimed supersession by
  `control-plane/foundational/enforcement-model.md` could not be corroborated (that page
  never mentions the direct-SQL gate mechanism this page is the corpus's sole detailed
  source for); its broken `control-plane/authority-model.md` citation needs fixing
  regardless of the placement decision.
- `invariant-roadmap.md` — needs a content split (durable methodology vs. a stale status
  table) before any path move, requires control-plane/ owner review.
- `w4-1-5c-g1a-evidence.md` / `w4-1-5c-g1a-execution-plan.md` — `nav_path` correction to
  `Archive/Evidence/...` (metadata only, no physical move) to match their 2 sibling pages.
- `docplane-redaction-remediation-register.md` — proposed `operations/security/
  registers/` new subsection, only 1 page named so far, needs 2+ more before creating it.
- 5 subsections still lacking `index.md` (`incidents`, `investigations`, `platform`,
  `runbooks`, `security`).
- Minor nav-label inconsistencies (`Security & Access` vs `Security`; the 5-item
  Tracker/board set's inconsistent nav tagging).
- **The archive/root-placement policy question** — ownership of root-retained lifecycle
  material, whether lifecycle should remain nav-only or gain a physical convention,
  whether the generator's threshold should distinguish deliberate lifecycle-root content
  from unexplained flatness.
