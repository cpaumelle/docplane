# Reorganisation control plane

Documentation reorganisation is a versioned product operation, not a filesystem move.

```text
PLAN → ANALYZE IMPACT → VALIDATE → PUBLISH
```

A plan records stable page identities, exact revisions, ordered structural operations, base and candidate state identities, navigation and redirect effects, collisions and inbound references. Validation re-runs those checks against current PostgreSQL state.

Any authenticated contributor may publish a validated plan directly. Publication applies the structural change atomically through the same change engine used for ordinary edits, records prior page versions and audit events, builds the MkDocs release, and updates deployment certification. Optional comments never gate publication.

No supported workflow performs direct SQL, manual filesystem edits or mutation of generated release files.
