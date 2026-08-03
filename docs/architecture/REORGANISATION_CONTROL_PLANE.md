# Reorganisation control plane

Documentation reorganisation is a versioned product operation, not a filesystem move.

```text
PLAN → ANALYZE IMPACT → VALIDATE → PUBLISH
```

A plan records stable page identities, exact revisions, ordered structural operations, base and candidate state identities, navigation and redirect effects, collisions and inbound references. Validation re-runs those checks against current PostgreSQL state.

Any authenticated contributor may publish a validated plan directly. Publication applies the structural change atomically through the same change engine used for ordinary edits, records prior page versions and audit events, builds the MkDocs release, and updates deployment certification. Optional comments never gate publication.

No supported workflow performs direct SQL, manual filesystem edits or mutation of generated release files.

## Human navigation workspace

The Dashboard is the human navigation-authoring surface; `docs-web` remains a
read-only generated consumer. Its organizer loads the revision-bound
reorganisation tree, supports drag-and-drop and keyboard ordering, and stages a
plan only after the author reviews the resulting navigation, paths and URLs.
Browser gestures never write individually.

Page sibling order is authored as `docs.pages.nav_order` and preserved in page
history. The generator applies this order at every navigation level, with an
exact `Overview` leaf pinned first as the invariant special case. Moving a page
between sections may change both `nav_path` and canonical Markdown path.

A `MOVE_PAGE` creates a compatibility redirect by default and deterministically
repairs live Markdown references in the same publication transaction. Analysis
reports rewritten references and deliberately preserved evidence references;
unsafe or uncertain parsing fails the plan closed. The link graph is derived on
demand from authoritative Markdown rather than maintained as a second source of
truth.
