# Human authoring

The dashboard is a first-class client of the DocPlane API, not a second authority.

A human contributor searches for the canonical page, loads its exact current revision, edits lossless Markdown, inspects the diff, validates the candidate and publishes it. There is no approval gate and no direct database or filesystem save path.

The browser forwards a named contributor token. PostgreSQL records the author, change, operations, prior page versions and audit events. Generated Markdown and MkDocs output remain disposable release artifacts. A stale edit is rejected and must be rebased; a prior version may be restored through the same validated publication path.
