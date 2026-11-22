# Product authority

PostgreSQL is DocPlane's authority for pages, model, observations and WORK. The
versioned API is the only supported mutation interface. The rendered site,
generated pages and every agent's private notes are copies or evidence.

## Identities

Every request is made as a named principal. Most are **contributors** with the
same rights; there are no reader, editor, reviewer or merger tiers. Two
fail-closed modes are narrower (`docs-api/app/agent_auth.py`):

- `SCOPED_AUTOMATION` principals may write only the artifacts, operations and
  page-path prefixes they were granted (migration 019);
- `WORK_COLLABORATOR` principals may use only the addressed inbox for their
  granted initiatives (migration 020).

Workspaces are classification boundaries, not authorization boundaries. The
bootstrap secret is used only for operator administration: issuing or revoking
principals and tokens, managing inbox participants and grants, and a small set of
bootstrap model operations.

## Why a document change is safe

A document change is:

1. bound to exact current revisions and, where applicable, section hashes;
2. validated against the complete candidate corpus, navigation and redirects;
3. committed atomically with prior-version snapshots and audit events;
4. rendered into a generated release;
5. recorded through deployment and certification receipts;
6. recoverable through version history, rollback and publication retry.

Review comments are optional and never gate publication.

## Why a WORK record is safe

WORK writes follow the same pattern: idempotency keys with replayable receipts,
version or context checks (a stale [brief](WORK_BRIEF.md) update is refused),
canonical redaction of authored text, and append-only activities. A WORK record
is evidence, never authority: it cannot approve a deployment, release a hold
without a recorded decision, or mark an initiative complete by itself.

Direct SQL, manual Markdown edits and mutations of generated releases are
unsupported.
