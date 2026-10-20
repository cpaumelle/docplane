# Capture reads

The contributor API remains the authority for WORK and canonical redaction.
These reads add no mutation, credential, initiative transition or publication path.

## Contract

`GET /api/v1/work/captures/{capture_id}` requires a contributor and always
returns `capture_bodies=redacted`. Capture body, nullable triage note and origin
text pass through `migration.redaction.redact`, using the same refusal boundary
as initiative history. Each field has a boolean `<field>_withheld`; a refused
field is null. Origin is returned as redacted fenced JSON text (`origin_text`),
not arbitrary structured metadata. Identity, classification, attribution,
relationship IDs and timestamps are allowlisted separately. Unknown IDs return
`404 CAPTURE_NOT_FOUND`. There is no raw single-capture read option.

`GET /api/v1/work/captures?view=metadata&status=all&limit=200` returns only typed
metadata, never body, origin or triage text. It stamps `capture_view=metadata`
and `pagination=keyset-v1`. The old list default remains `view=raw` for existing
operator consumers; agent adapters must explicitly select and verify metadata.

Each page includes `count`, `total`, `truncated`, and nullable `next_cursor`.
Follow `next_cursor` with the same status until `truncated=false`. `total` is the
number matching the live filter below the upper boundary at the current request,
not the number remaining after the cursor. `truncated` means a next page exists.
A page is internally consistent through a read-only repeatable-read transaction.

Cursors are bounded, versioned base64 JSON containing status, the initial maximum
`(created_at, capture_id)` and the last returned position. They contain no content
or authority and do not require a signing credential. They are untrusted input:
validate exact shape, version, timezone-bearing timestamps, canonical UUIDs and
matching status before constructing parameterized SQL. Ordering is descending
creation time and UUID, using strict keyset comparison for subsequent pages.

## Consistency limits

This is bounded keyset traversal, not a durable database snapshot across calls.
New rows above the initial upper boundary are excluded. Tied timestamps do not
cause duplicates. Triage of already returned rows does not shift the cursor or
skip the following row, unlike offset pagination. Status-filter membership stays
live; status changes may remove or introduce matches. A transaction committed
late with an earlier creation key may appear below the upper boundary.

For consolidation, enumerate `status=all`, retain capture IDs, then read each
capture immediately before proposing or applying a disposition. Track unseen
arrivals in a later pass. Do not equate a changing total with a failed traversal,
or claim point-in-time completeness. No bulk disposition is implied by a read.

## Security and verification

Clients use their existing governed WORK identity. They must fail closed when
redaction/view/pagination stamps are absent or inconsistent, validate identity,
enums, timestamps and withheld flags, and allowlist output fields. Upstream
errors are content-free bounded codes. No raw fallback or client-side redactor.

Tests cover synthetic credential-shaped values in body, note and origin; refused
redaction; unknown capture; authentication; invalid cursors; timestamp ties;
new arrivals; concurrent triage; empty lists; and metadata-only output. Adapter
tests must also cover old servers ignoring unknown parameters, malformed
responses, repeated cursors and response field allowlisting.

No migration or generator change is required. Deployment requires reviewed exact
API and adapter commits, existing backup/rollback procedures, then read-only
acceptance through the declared agent consumer. Deploy the API before the
adapter; an adapter deployed early refuses the unavailable contract. Application
rollback restores the previous images; WORK data is unchanged. No publication or
triage is part of deployment.
