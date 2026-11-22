# Safe WORK reads for agents

Agents need to read WORK (initiatives, their history and captures) without ever
seeing a secret that someone pasted into a note. These reads give them that.
They add no write path, no credential, no lifecycle change and no publication.

Every text field an agent reads here passes through the canonical redaction
transform (`migration.redaction.redact`, see
[Redaction invariants](REDACTION_INVARIANTS.md)). A field the transform refuses
is withheld: its value is `null` and a boolean `<field>_withheld` is `true`.
There is no raw fallback and no client-side redactor.

Code: `docs-api/app/work_api.py`. Tests: `docs-api/tests/test_capture_reads.py`,
`test_capture_reads_e2e.py` and `test_work_read_surfaces.py`.

## Finding initiatives

`GET /api/v1/initiatives?view=summary` is the agent discovery read. It returns
only allowlisted typed fields (ID, key, state, priority, owner, owner principal
kind, dates) plus the title through canonical redaction, and stamps
`initiative_view: summary-v1`. A title that redaction would change is withheld,
not altered. `q` (2 to 100 characters) matches the key or title as a literal
substring. The default `view=full` is unchanged for existing callers.

## Reading an initiative's history

`GET /api/v1/initiatives/{id}?activity_bodies=redacted` is the agent read of an
initiative. Activity bodies pass canonical redaction, and the response is
stamped `activity_bodies: redacted` and `activity_metadata: allowlisted-v1`.

Free-form activity metadata is never returned. Only these typed projections
are, each through redaction:

- typed evidence `references` (`ACTIVITY`, `CONDITION`, `ARTIFACT`, `PR`,
  `COMMIT`);
- `source_capture_id`, the capture an attached activity came from;
- `provenance`: what a capture's producer reported about itself (a claim,
  separate from the authenticated author) and capture IDs it names as corrected,
  superseded, resolved, closed or related.

The default `activity_bodies=raw` remains for existing operator consumers.

## Reading one capture

`GET /api/v1/work/captures/{capture_id}` requires a contributor and always
returns `capture_bodies: redacted`.

- The capture body, the nullable triage note and the origin each pass canonical
  redaction and carry a `<field>_withheld` flag.
- The origin is returned as redacted fenced JSON text (`origin_text`), not as
  structured metadata. Decoded origin strings are redacted before JSON escaping,
  and a sensitive-key guard refuses leftover credential attributes. Any refusal
  withholds the whole origin.
- Identity, classification, attribution, relationship IDs and timestamps are
  allowlisted separately.
- An unknown ID returns `404 CAPTURE_NOT_FOUND`. There is no raw single-capture
  read.

## Listing captures

`GET /api/v1/work/captures?view=metadata&status=all&limit=200` returns typed
metadata only: never body, origin or triage text. It stamps
`capture_view: metadata` and `pagination: keyset-v1`. The list default remains
`view=raw` for existing operator consumers; agent clients must ask for
`view=metadata` and check the stamp.

Each page includes `count`, `total`, `truncated` and a nullable `next_cursor`.
Follow `next_cursor`, with the same status, until `truncated` is false.

- `total` counts the rows that match the filter below the starting point at the
  time of the request. It is not the number left after the cursor.
- `truncated` means another page exists.
- Each page is read in one read-only, repeatable-read transaction, so a single
  page is internally consistent.

### Cursors

A cursor is versioned base64 JSON holding the status, the starting upper bound
`(created_at, capture_id)` and the last position returned. It contains no
content and no authority, and it is not signed. The server treats it as
untrusted input: it checks the exact shape, version, timezone-bearing
timestamps, canonical UUIDs and matching status before building a parameterised
query. Rows are ordered by creation time then UUID, descending, with strict
keyset comparison.

### Consistency limits

This is bounded keyset traversal, not a snapshot that lasts across calls.

- Rows created above the starting bound are excluded.
- Tied timestamps do not produce duplicates.
- Triage of rows already returned does not shift the cursor or skip the next
  row, unlike offset pagination.
- Status membership is live: a status change may remove or add a match.
- A transaction that commits late with an earlier creation key may appear below
  the bound.

To consolidate captures, enumerate `status=all`, keep the IDs, then read each
capture again immediately before proposing or applying a disposition. Pick up
new arrivals in a later pass. A changing `total` is not a failed traversal, and
no read can claim point-in-time completeness. A read never implies a bulk
disposition.

## Client obligations

Clients use their own governed WORK identity. They must fail closed when the
redaction, view or pagination stamps are missing or inconsistent; validate
identities, enums, timestamps and withheld flags; and allowlist the fields they
pass on. Upstream errors should be passed on as short, content-free codes.

The read-only [`docplane-mcp-shim`](../../docplane-mcp-shim/README.md) is one
such client: its `work_history` tool refuses any response without both
`activity_bodies` and `activity_metadata` stamps. The WORK worker described in
[the Librarian](LIBRARIAN.md) applies the same rules to its reads.
