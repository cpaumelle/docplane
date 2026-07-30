# Agent 45 remaining territory — decision drafts

*2026-07-30T15:00. Read-only drafting while Agent 44 works its own territory. No
rehearsal, no publication, no lane touched.*

## 1. CORRECTION: `topology-invariants/index.md` is NOT superseded by `invariants/index.md`

Earlier analysis assumed `control-plane/invariants/index.md` (v61) already covers
whatever the old topology index does, since both now share a directory. **That
assumption was wrong** — verified by reading both bodies in full, not by directory
adjacency:

- `control-plane/invariants/index.md` is explicitly self-declared as a **hand-maintained
  catalog/view**, scoped to a *different* invariant family: DNS authority, Authelia rule
  ownership, docs-api authority, render-integrity, generated-files immutability,
  database-first configuration, protected-file mutation. Its own abstract block says:
  *"The canonical source of truth for each invariant is its per-invariant page
  (`control-plane/topology-invariants/i-<slug>-N.md`...). This catalog is a view."* It
  acknowledges the topology-invariants family exists as a separate, canonical set — it
  does not attempt to enumerate it.
- Grepped all 7 invariant IDs named in the old topology index
  (I-IFACE-NAMING-1, I-SITE-TRANSPORT-AUTHORITY-1, I-TRANSPORT-HUB-1,
  I-SITE-NETWORK-AUTHORITY-1, I-EDGE-PARITY-1, I-GHOST-ROW-1, I-SEC-FAIL-CLOSED-1)
  against the new index's 95KB body: **zero occurrences.**

So the two pages serve genuinely different purposes that happen to now share a physical
directory after this session's moves. Retiring `topology-invariants/index.md` as
"redundant" would be wrong — it's the only index that covers the topology/transport/
interface/GEO invariant family at all.

**Options for an actual authoring decision** (not decided here):
1. Move it to a distinct path in `control-plane/invariants/` that doesn't collide with
   the existing `index.md` — e.g. `control-plane/invariants/topology-index.md` — and add
   a cross-link from the main index.
2. Merge its content as a new section inside the existing `invariants/index.md`, since
   both catalogs now live in the same directory and arguably should converge into one.
3. Leave it as a topic-scoped sub-index at a renamed path, matching how other
   topic-specific pages already work in this corpus.

Its content is stale regardless of which option is chosen — it says "41 invariant
pages" and lists only 7 by theme; the real count in the directory is now much larger
after this session's moves. Whichever option is picked, the content needs a refresh, not
just a path change.

## 2. `i-config-projection-1.md` — destination recommendation

Read the full body: it's a framework-level governance doctrine ("one canonical source,
many projections, one reconciliation path, drift is observable") applied uniformly
across secrets, code/scripts, configs, and generated artifacts. Nothing topology- or
transport-specific about it — it reads like the other broad doctrine pages already in
`control-plane/foundational/` (`authority-model.md`, `gateway-convergence-doctrine.md`,
`doctrine.md`), not like the structural, single-domain contracts in
`control-plane/invariants/` (interface naming, GEO routing precedence, etc.).

**Recommendation: `control-plane/foundational/i-config-projection-1.md`**, not
`control-plane/invariants/`. This is a recommendation, not a decision — flagging for the
operator call your earlier framing correctly anticipated.

## 3. `ccm.md` — hub-conversion inbound inventory (refreshed)

Fresh search: **16 inbound references** (17 total hits including self), confirming hub
status. Full inbound set as of this pass:

`agent-guides/docs-structure-dashboard.md`, `agent-guides/index.md`,
`control-plane/architecture/index.md`,
`control-plane/foundational/architecture-reconciliation-charter.md`,
`control-plane/foundational/authority-model.md`,
`control-plane/foundational/gateway-convergence-doctrine.md`, `control-plane/index.md`,
`control-plane/specs/ccm-data-model.md`, `control-plane/specs/gate-2-1-tm-interface-create-api.md`,
`control-plane/specs/gateway-convergence-v1-gate-2-design.md`,
`network/fabric-v2/lan-schema.md`, `network/fabric-v2/lan-watcher.md`,
`operations/w4-1-5b-runtime-manifest-architecture.md`,
`operations/w4-1-5c-g1a-prep-findings.md`, `operations/w4-1-5c-g1a-sql-authority-audit.md`
(new since the last count), `services/transit-manager.md`.

Destination: `control-plane/foundational/ccm.md` (nav_path already reads "Control
Plane/Foundational/CCM Contract"). Ready for an H1/H2-style hub-conversion publication
(move + dedicated inbound-repair pass across these 16 pages) whenever scheduled —
this is prep only, not queued for this session.

## 4. `ccm-interface-realization-capability.md` — relabeling draft

Re-read current body: still states *"PROVEN IN PRODUCTION (Paris canary)... 48h soak
armed 2026-05-29T04:38:12Z (earliest exit 2026-05-31T04:38:24Z)"* — unchanged from
earlier this session, still describing a soak that exited **two months ago** as if it
were in progress. No lifecycle or status-table edit has landed since.

**Draft relabel** (for operator approval, not applied): change the `Status` table row
from *"PROVEN IN PRODUCTION (Paris canary) — ... earliest exit 2026-05-31T04:38:24Z"* to
something like *"Completed — Paris canary proven 2026-05-28/29, soak exited
2026-05-31T04:38:24Z without incident. Retained as historical design record."* — matching
the pattern already used on its sibling `ccm-interface-realization-design.md`
("Implemented — historical design memo"). Once relabelled, the page reads as a settled
historical record rather than an in-flight sprint, and moving it to
`control-plane/design/` (alongside its already-moved siblings) becomes a low-risk,
uncontroversial follow-up rather than a live-status ambiguity.
