# Decompose `control-plane/invariants/index.md` into a linked catalogue (v5)

## Context

`control-plane/invariants/index.md` is 95KB, 43 H2 sections. 42 other invariants already
live as individual files under `control-plane/invariants/*.md`, heavily cross-referenced
by ID. The index never got the same treatment. Goal: one canonical page per distinct
valid invariant — not one page per old section — with two-way discoverability, matching
the pattern `i-gen-no-vcs-1.md` already demonstrates.

v5 closes the last gap from three rounds of review: every one of the 73 final candidate
pages has now been individually checked against the rest of the corpus (not just its
named risk cluster), a contradiction-blocking gate is now explicit, the CCM-supersession
anchors are verified against real DocPlane behavior (not assumed), the 24 inbound
references are numerically frozen, and navigation ordering is confirmed by precedent.

## Canonicality matrix — all 73 final pages checked

Full 9-column matrix (rule / subject / authority / scope / status / relationship /
canonical source / compared candidates / decision) recorded for every page in the
frozen manifest (committed to the repo, not left in `/tmp`). Condensed here:

**Non-trivial relationships found (10 pages, detailed disposition):**

| Page(s) | Relationship | Decision |
|---|---|---|
| Single-Writer Architecture / Database-First Configuration / No Direct Database Access | Duplicate/subset of `ccm.md` I-3/I-1/I-2 (narrower, older, Domain-Manager-scoped restatement of the same three rules, later generalized) | Superseded — historical note each, no new page |
| `I-GEN-NO-VCS-1` index copy | Stale strict-subset duplicate of the current canonical `i-gen-no-vcs-1.md` (v4, 2026-07-20) | Duplicate — pointer, no new page |
| `I-DOMAIN-LIFECYCLE-1` / State Machine Invariant | Specialization pair (general FSM-must-exist vs the specific safe-DELETE convergence rule) — same established pattern as `I-COVERAGE-DERIVED-1`/`I-WG-WATCHDOG-1` elsewhere in the corpus | Distinct — publish both, cross-link parent/specialization |
| `Artifact Immutability` / `Generated Files Immutability` | Distinct axes (checksummed re-render/versioning vs read-only permissions) | Distinct — publish both, cross-link |
| Protected File Invariants (P1/P2/P3) / Generated Files Immutability | Specialization/complementary — P1-3 is a *stronger*, OS-level (`chattr +i`) enforcement tier for an explicit, generator-tracked list of 11 files; Generated-Files-Immutability is the general principle | Distinct — publish both, cross-link |
| `I-DOCS-AUTHORITY-1` / `I-AUTHELIA-RULES-AUTHORITY-1` (nested) / Authelia Rule Ownership | Three distinct concerns (docs-DB authority; the specific config-file-split/dual-loader architecture, externally cited on its own; the broader restart/schema/drift/policy-change contract) | Distinct — 3 pages |
| VPN Route Lifecycle / VPN Route Ownership | Source text itself frames as deliberately paired-but-distinct | Distinct — 2 pages |
| `I-DNS-AUTHORITY-1` vs 4 existing DNS IDs | Distinct axis (resolver-address authority vs writer/config/sourcing) | Distinct — publish |

**Remaining 63 pages: independent, no overlap found** against any other candidate or
existing file — each individually content-read and checked, not assumed from title
similarity. This includes the 8 pages flagged as "not obviously covered by the six
clusters" in review round 3 (`I-LOCALHOST-SERVICE-1`, `I-DRAFT-NO-CERT-1`,
`I-DRAINER-CRED-1`, `I-STORAGE-ORPHAN-1`, `I-CERT-PROBE-1`, `I-OBS-LIVENESS-1`,
`I-PROTECTED-FILE-1`, `I-VERSION-ALIGNMENT-1`) plus the 4 Actility invariants — all
individually read in full and confirmed narrow, verifiable-unique rules (e.g.
`I-STORAGE-ORPHAN-1` is Proxmox storage-volume orphan detection, unrelated to anything
else in the corpus; `Version Alignment` is cross-service git-SHA alignment before
mutation, likewise unique).

No contradiction was found anywhere in the 73-page set.

## Contradiction-blocking gate (new, explicit)

> No two active or proposed rules governing the same subject and scope may assign
> incompatible authorities, permitted actions, required states, or enforcement outcomes.

Outcomes per pair checked: **Distinct** (publish both, document the boundary) /
**Specialization** (publish both, cross-link parent/child) / **Duplicate-subset**
(preserve one canonical rule, alias the rest) / **Superseded** (historical note pointing
to the successor) / **Contradictory** (block for an authoring decision, do not publish
either as competing active statements) / **Composite** (split only where each resulting
rule is independently normative — used once, for the VPN pair; Protected-File's P1/P2/P3
stay one page since the source itself says "all three must hold simultaneously," not
independently normative). Every pair in the matrix reached one of the first four
outcomes; none reached Contradictory, so nothing is blocked pending an authoring
decision in this pass.

## CCM supersession — anchors verified, not assumed

Checked directly: `ccm.md`'s I-1 through I-5 are **bold paragraph text, not markdown
headings** — they have no individually-addressable anchor today. The three historical
notes therefore link to the section anchor `ccm.md#ccm-invariants` (verified to exist and
resolve), with the specific ID named in the note text:

| Old index section | Canonical replacement | Link target |
|---|---|---|
| Database-First Configuration | CCM I-1 | `ccm.md#ccm-invariants` (states "supersedes I-1") |
| No Direct Database Access | CCM I-2 | `ccm.md#ccm-invariants` (states "supersedes I-2") |
| Single-Writer Architecture | CCM I-3 | `ccm.md#ccm-invariants` (states "supersedes I-3") |

Giving I-1 through I-5 their own real headings/anchors in `ccm.md` would be a cleaner
long-term fix, but that's a structural change to an already-heavily-referenced page and
is **out of this pass's scope** — noted as a follow-up, not silently worked around.

**Final verification wording, corrected per review:** 4 of the 5 superseded/historical
notes (`Backend Host Stability` → `i-localhost-service-1.md`, plus the 3 CCM ones →
`ccm.md#ccm-invariants`) link to a canonical replacement; the retired `VLAN 50 Scope`
note has **no live successor** and says so explicitly, not glossed over.

## 24 inbound references — frozen numeric breakdown

- **14 active references** repointed directly to their new canonical page, in the same
  batch as that page's creation (the `I-CANONICAL-REPLACEMENT-1` cluster alone accounts
  for 7 of these — `lw-r1-design-notes.md`, `invariant-roadmap.md` ×4,
  `api-reference.md`, `transit-manager.md` — plus `ccm.md`→Transit-Peer-Integrity,
  `domain-delete-safe-path.md`→Domain-Lifecycle, `authelia-managed-rules-split.md`→
  Authelia-Rules-Authority, `sd-wan-invariants.md`→Obs-Liveness, `edge-ingress.md`→
  Routing-AllowedIPs, `ch-transit-core.md`→both VPN pages, `reference/scripts.md`→
  Version-Alignment).
- **2 historical/evidence references** (`specs/tm-localhost-registry-1.md` ×2) keep
  pointing at the preserved Backend-Host-Stability index anchor — correctly framed as
  background on the migration that led to supersession, not a live-rule citation.
- **8 index self-references** are resolved by the extraction itself (they become the
  stub), not separately repointed.
- 14+2+8 = 24. Exact source/target pairs recorded in the manifest, not just totals.

## Navigation — ordering confirmed by precedent, not assumed

Checked the generator (`docs-api/app/generator.py::build_nav`) and the existing
`/api/v1/pages` query (`ORDER BY p.path`): sibling ordering within a nav group is
alphabetical by page path, which the 42 existing `i-*.md` invariant pages already render
under — confirmed by their current live order. The 31 new pages inherit the same,
already-proven behavior; no new ordering mechanism needed. Usability checks before
freezing the nav manifest:

- **Proposed vs active distinguishable**: every stub and every new page carries an
  explicit `Status:` line (`ACTIVE` / `PROPOSED`); the 5 Actility+ARTIFACT-LIFECYCLE
  PROPOSED pages are visually distinct in the catalogue, not silently blended with active
  ones.
- **Superseded/historical notes are not presented as live pages**: they stay inline in
  `index.md` with no corresponding nav leaf — reader only reaches them via the catalogue,
  not a false sibling entry.
- **Catalogue remains the preferred discovery surface**: the index's grouping (by theme,
  matching the publication batches) stays the primary navigation path; the flat
  `Control Plane/Invariants/*` nav list is a secondary, already-existing surface, not
  the one being redesigned here.
- 73 flat nav siblings is more than 42 but not a new problem this pass introduces or must
  solve — noted as a legitimate follow-up (nav sub-grouping) for a separate, later
  decision, since fixing it would also mean touching the already-published 42.

## Scope count (unchanged from v4, now fully justified)

29 H2 sections → 31 new files; 1 duplicate fixed; 8 retained catalogue/meta; 5
retired/superseded historical notes = 43. Final: **42 existing + 31 new = 73 invariant
pages.**

## Execution — one issue, atomic per-batch publication

1. Open one new GitHub issue (Issue #44 is closed). `I-DNS-DUAL-RESOLVER-1` (cited 15×
   across 6 pages, defined nowhere) gets its own separate issue, not folded in here.
2. Commit the frozen manifest to the repo first: all 73 pages' canonicality-matrix rows,
   the 31 destination files + IDs + nav_paths, the 24 classified references with exact
   source/target pairs, the two-hash content-conservation scheme, the anchor-limitation
   note for `ccm.md`.
3. Publish in bounded, atomic thematic batches — each creates its page(s), replaces the
   corresponding index section(s) with stub(s)/historical notes, repoints its active
   references, adds sibling cross-links, all atomically:
   1. Domain, state and database authority (3 historical-note dispositions, not new pages)
   2. DNS, certificates and observability (7-reference `I-CANONICAL-REPLACEMENT-1` repoint)
   3. Documentation and generated artefacts (Authelia nested-extraction split)
   4. Protected files, render integrity and validation
   5. Transit, routing and VPN (`ccm.md` repoint, two-link VPN repoint)
   6. Authelia ownership
   7. Actility proposed invariants
   8. Existing-page backlink retrofit
   9. Final index meta cleanup + full-corpus audit
4. Before each batch, re-verify its target sections in the *current* `index.md` against
   the frozen manifest by heading/anchor identity + `source_span_hash`, not raw line
   numbers (index shrinks progressively across batches).
5. Reuse `migration/links.py` (`plan_source_move`, `plan_corpus`, `find_links`) from
   `~/docplane-dev-redirects` (`main`, post `#91`).

## Verification, per batch

- Content-conservation proof (two-hash scheme) for every extraction, plus the explicit
  relationship disposition (distinct/specialization/duplicate/superseded) recorded per
  the matrix, not silently applied.
- Contradiction gate: confirm no pair in the batch resolves to "Contradictory" before
  publishing (none found in this pass's matrix; re-check holds at publish time too, in
  case the frozen manifest and live content have drifted).
- Masking-equality proof for the backlink-retrofit batch.
- Post-batch gate: current-target existence, stale old-path detection, anchor
  resolution, route uniqueness, navigation-path uniqueness, content-conservation.
- Certification CURRENT, working==deployed, zero open changes after each batch.
- Final pass: all 73 pages have a working back-link; the index resolves to all 73; the
  5 historical/superseded notes point at their correct (or explicitly absent, for VLAN
  50) replacement; nav renders with PROPOSED/ACTIVE clearly distinguished.
