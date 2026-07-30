# Partial cluster migration rule — mandatory preflight

*Agent 45, 2026-07-30. This is a publication-design rule, derived directly from real
damage observed this session (22 broken links across 5 pages — see
`2026-07-30T1410-independent-regression-audit.json`), not a hypothetical concern.*

## The two real bug classes found

1. **Own-page relative-link depth change.** A page's *own* body contains relative links
   (sibling or `../…`) written correctly for its *old* directory depth. When the page
   moves to a directory at a different depth (`control-plane/foo.md` → `control-plane/design/foo.md`
   is depth 1→2; `control-plane/foo.md` → `operations/runbooks/foo.md` is a cross-subtree
   depth-1→2 move too), every one of those links silently resolves to a different,
   usually nonexistent, target. `migration/links.py` does not catch this — its `plan`/
   `apply`/`scan` are scoped to *inbound* references to the pages named in the manifest,
   never to the moved page's own outbound links. Caused 19 of this session's 22 new
   regressions.

2. **Partial-cluster sibling reference.** A page moves as part of a *subset* of a
   semantic cluster (e.g. 5 of 23 `topology-invariants/` pages). Its body references a
   *sibling* by same-directory relative path (`i-geo-placement-1.md`, no `../`). If that
   sibling is **not** in the same move batch, the reference — correct before the move —
   now resolves into the *new* directory, where the un-moved sibling does not exist.
   Caused the other 3 (2 agent-45, 1 agent-44, plus a related but distinct own-link-depth
   variant on `i-gen-no-vcs-1.md`).

Both classes share a root cause: **nothing in either agent's process modeled the
directed link graph among candidate-and-sibling pages before publishing.** The gate that
was missing is graph-shaped, not a simple per-page check.

## The mandatory preflight

Before any cohort (full cluster or subset) is rehearsed:

1. **Build the directed internal-link graph** restricted to two node sets: (a) every
   page in the candidate cohort, (b) every page reachable from a candidate via a
   same-directory or ancestor-relative link (i.e. every page a candidate's own body
   links to, and every page that links to a candidate) — this must include the
   candidates' **own outbound links**, not just their inbound references, which is the
   exact gap `migration/links.py` currently has.

2. **Classify every edge as in-cohort or cross-boundary.** In-cohort: both endpoints are
   moving together in this publication. Cross-boundary: exactly one endpoint moves.

3. **For every cross-boundary edge, require exactly one of:**
   - **(a) Expand the cohort** to include the complete strongly connected component —
     move the sibling too, atomically, in the same publication.
   - **(b) Rewrite to a root-absolute canonical link** (`/control-plane/foo.md`, as the
     `f18538be` repair this session correctly did) before or during the move — this is
     immune to *either* endpoint moving again later, which is why it is the preferred
     fix over re-computing a new relative path that will just break again next time
     either page moves.
   - **(c) Preserve deliberately**, only where an explicit evidence-surface/compatibility
     exception applies (matching the existing accepted-baseline pattern of ~18-20
     protected refs already documented in this program) — and only when a redirect is
     proven to serve the old target with HTTP 200.
   - **(d) Exclude the page from the cohort** entirely, deferring it to a later batch
     where its cross-boundary edges can be resolved by one of (a)/(b)/(c).

4. **A page's own outbound links must be checked against its own post-move location**,
   not just against other pages' references to it. This is the fix for bug class 1:
   before publishing, recompute what every relative link *inside the moving page* would
   resolve to at the *destination* path, and treat any that would resolve to a
   nonexistent target as a preflight failure exactly like a cross-boundary edge.

5. **Rehearsal must fail, not warn, on any unresolved cross-boundary edge or
   depth-changed self-link.** "Corpus scan clean" must mean *corpus-wide target
   existence for every page touched or referenced*, not merely "no new stale references
   to the pages this specific cohort moved" — the exact distinction Agent 44's own
   commit message identified as the verification gap.

## Concretely: what changes in the tooling

`migration/links.py`'s `plan`/`apply`/`scan` need a new mode — call it `self-check` —
that, given the same move manifest, additionally:

- Parses each **moving** page's own body for internal links.
- Resolves each one against the *post-move* path.
- Reports any target that would not exist post-move as a `self_link_break`, with the
  same `{line, old_target, new_target, resolved}` shape `plan` already uses for inbound
  rewrites.
- For sibling-style links to un-moved same-directory pages, this doubles as the
  partial-cluster detector described above (rule 2), since a same-directory link from a
  moving page is exactly the case that needs re-resolution.

`scan`'s existing corpus-wide mode (used for the final existence-aware audit) should
also be run **before** every cohort's rehearsal, not only after every publication — this
session's damage would have been caught by a pre-rehearsal full-corpus scan rather than
discovered after the fact.

## Acceptance criteria

1. A cohort manifest where a candidate page contains a same-directory link to a
   non-candidate sibling **must fail rehearsal** with a named cross-boundary edge,
   sibling path, and line number — not merely pass with a warning.
2. A cohort manifest where a candidate page's own depth changes (source dir ≠ destination
   dir depth) and its body contains any relative link **must have every such link
   checked against the post-move location**; any link that would not resolve is a
   preflight failure.
3. Re-running this session's actual 4 agent-45 cohorts (ccm design pair,
   ccm-interface-realization-step6-runbook, site-geo-fabric, framework-registry-invariants)
   through this preflight, using the pre-move corpus snapshot already captured this
   session, must reproduce all 19 real self-link breaks and the partial-cluster sibling
   breaks on `i-lan-geo-bypass-1.md` as preflight failures, not passes — this is a
   regression test against real, already-observed damage, not a synthetic case.
4. A cohort where every cross-boundary edge has been resolved via (a), (b), or (c) above
   must pass preflight and, after publication, produce zero new broken targets under the
   full corpus-wide `scan`.
5. Preflight failure output must be structured (JSON), not prose-only, so it can gate a
   CI-style rehearsal step automatically rather than requiring a human to read a log.
