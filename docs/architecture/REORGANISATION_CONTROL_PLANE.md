# Reorganisation control plane

Documentation reorganisation is a governed product workflow, not a filesystem operation.

```text
PLAN
→ ANALYZE IMPACT
→ VALIDATE
→ SUBMIT
→ APPROVE
→ EXECUTE
→ CERTIFY
→ STABILIZE
→ CLOSE OR COMPENSATE
```

The dashboard is the first-class human control surface. Humans and agents use the same versioned plan and change APIs.

A plan records exact page identities and revisions, ordered structural operations, base and candidate state identities, collisions, inbound references, redirect and navigation effects, search/MCP reindex requirements and compensating operations.

Analysis and approval never mutate authored state. A validated plan converts into the ordinary DocPlane change-proposal contract. Execution remains unavailable until the WP8 candidate-state guard, immutable release build, validation, promotion and certification path is implemented.

No supported workflow performs direct SQL, filesystem moves, container edits or changes to generated MkDocs release files.
