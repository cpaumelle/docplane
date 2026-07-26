# Page trust and maintenance

DocPlane treats content revision, metadata classification and verification as separate authorities.

- A page has a stable resource identity and a mutable content revision.
- Classification records its workspace, publication state, knowledge class, criticality and accountable owner.
- Verification applies to one exact content revision.
- Any later content revision automatically invalidates current verification.
- Verification expiry and maintenance queues create accountable review work; they never rewrite, reclassify or archive pages automatically.
- Human and agent principals use the same scoped, idempotent APIs and workspace roles.
- Usage statistics are not part of page trust and remain owned by the dashboard domain.

Imported pages enter the migration-import workspace with metadata review required. DocPlane does not infer durable semantic truth from legacy Markdown lifecycle labels.
