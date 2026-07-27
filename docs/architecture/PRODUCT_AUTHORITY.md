# Product authority

PostgreSQL is DocPlane's authored-state authority. The versioned API is the only supported mutation interface.

Every active named principal has the same contributor rights. Workspaces are classification boundaries, not authorization boundaries. The bootstrap secret is used only to issue or revoke named principals.

A document change is safe because it is:

1. bound to exact current revisions and, where applicable, section hashes;
2. validated against the complete candidate corpus, navigation and redirects;
3. committed atomically with prior-version snapshots and audit events;
4. rendered into a generated release;
5. recorded through deployment and certification receipts;
6. recoverable through version history, rollback and publication retry.

Review comments are optional and never gate publication. Direct SQL, manual Markdown edits and mutations of generated releases are unsupported.
