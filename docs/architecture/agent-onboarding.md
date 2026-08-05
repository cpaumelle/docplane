# Agent onboarding and first publication

This guide is deployment-neutral. Replace `BASE` with the routed DocPlane URL supplied by the operator or platform. Do not assume a direct container port, a legacy documentation API, or a shared credential.

DocPlane has one authored-state authority: PostgreSQL through the versioned DocPlane API. Rendered Markdown and the MkDocs site are derived, certified releases.

## 1. Discover before authenticating

Start from the routed host, not from repository files:

```bash
BASE='https://docs.example.internal'

curl -fsSI "$BASE/" | grep -i '^link:'
curl -fsS "$BASE/.well-known/docplane.json" | jq .
```

The discovery document declares:

- the active access profile;
- the credential-acquisition procedure;
- required headers;
- read, replace and multi-operation workflows;
- the operation-contract endpoint;
- error codes and remedies;
- the authoritative API surfaces.

Also fetch the generated API contract:

```bash
curl -fsS "$BASE/openapi.json" > /tmp/docplane-openapi.json
curl -fsS "$BASE/api/v1/operation-contracts" > /tmp/docplane-operations.json
```

`operation-contracts` gives every supported `operation_type` its exact revision bindings, payload JSON Schema and complete request example. Never infer one operation's payload from another.

## 2. Acquire an individually attributable bearer token

Read `authentication.token_acquisition` from discovery. Do not invent an acquisition method.

### Private-fabric profile

When discovery advertises `self_service: true`, request a short-lived AGENT token through the routed host:

```bash
TOKEN=$(curl -fsS -X POST \
  -H 'Content-Type: application/json' \
  "$BASE/api/v1/auth/self-issue" \
  -d '{"display_name":"example-agent","client_context":"first DocPlane publication"}' \
  | jq -r .token)

test -n "$TOKEN" && test "$TOKEN" != null
```

DocPlane requires no bootstrap secret or server-side approval in this profile. The caller's own safety framework may still pause credential issuance for confirmation. Verify the routed endpoint, AGENT/CONTRIBUTOR scope and expiry before approving that caller-side gate.

Cache and reuse the returned bearer until it expires or is rejected; schema
discovery, conflict inspection and recovery do not require a new principal for
each request. If issuance returns HTTP 429, read
`detail.retry_after_seconds` or the equivalent `Retry-After` header and wait
that exact interval. Do not retry in a tight loop. Discovery publishes the
effective burst, sustained and global windows under
`authentication.token_acquisition.rate_limit`.

### Managed profile

When discovery advertises `self_service: false`, follow the operator-issued procedure it returns. Never request or expose the bootstrap credential.

For the rest of this guide:

```bash
AUTH="Authorization: Bearer $TOKEN"
```

Keep the clear token out of logs, shell history, documentation, issue bodies and publication receipts.

## 3. Search and resolve before creating

Avoid duplicate pages:

```bash
curl -fsS -H "$AUTH" \
  "$BASE/api/v1/search?q=example&limit=10" | jq .

curl -fsS -H "$AUTH" \
  "$BASE/api/v1/resolve?path=reference/example.md" | jq .
```

Use stable `resource_id` values for existing pages. Paths can move; resource identities persist.

### How `q` is matched (token-aware search)

`/api/v1/search` is token-aware, not a single contiguous-substring match. Read
`search.matching` and `search.maximum_terms` from `.well-known/docplane.json`
for the machine-readable contract; the behaviour is:

- **Tokenisation.** `q` is split into distinct terms. A term is a word run that
  keeps internal hyphens and dots, so `ceph-volume`, `fsid` and `v1.2` each stay
  one term. Purely non-word characters are separators and are dropped — `c++`
  searches for `c`, and `a & b` searches for `a` and `b`.
- **AND across terms, OR across fields.** Every distinct term must match at
  least one indexed field (`title`, `path`, `nav_path`, `content`). Terms need
  not be adjacent or in order: `stale ceph-volume activation fsid systemd`
  finds a page that contains all five terms anywhere.
- **Deduplication and cap.** Terms are deduplicated in first-seen order and
  bounded to `search.maximum_terms` (currently `12`). Terms beyond the cap are
  silently ignored, so put the most selective terms first.
- **Ranking.** An exact contiguous phrase match, and matches in `title` then
  `path`, rank ahead of `nav_path` and `content` matches; within that, a
  field-weighted score (title > path > nav_path > content, summed across terms)
  orders the rest, with `path` as the final tiebreak. Query the most specific
  phrase you have to keep the intended page at the top.

Each hit reports `matched_in`, `snippet`, `summary`, `workspace_key` and
`revision`. `matched_in` lists every field containing **at least one** query
term — it is a per-term hint, not a guarantee that a single field held the whole
query. Use `total` (the full match count) versus `count` (the returned page
count) to decide whether to narrow `q` or raise `limit` (max `100`).

The published reference page `reference/token-aware-search.md` ("Token-aware
document search") carries the full ranking table and worked examples; search
`token aware search` to find it.

## 4. Replace one existing page

The common single-page edit uses the audited one-call replacement endpoint.

```bash
PAGE=$(curl -fsS -H "$AUTH" \
  "$BASE/api/v1/pages?status=all&path=reference/example.md")

RESOURCE_ID=$(jq -r '.pages[0].resource_id' <<<"$PAGE")
REVISION=$(jq -r '.pages[0].revision' <<<"$PAGE")
CONTENT=$(curl -fsS -H "$AUTH" \
  "$BASE/api/v1/pages/$RESOURCE_ID?view=edit_context" | jq -r .content)

UPDATED_CONTENT="$CONTENT

Updated through the audited DocPlane API."

curl -fsS -X POST \
  -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: replace-$(uuidgen)" \
  "$BASE/api/v1/pages/$RESOURCE_ID/replace" \
  -d "$(jq -n \
    --arg revision "$REVISION" \
    --arg content "$UPDATED_CONTENT" \
    '{expected_revision:$revision,content:$content,purpose:"Correct the example page"}')" \
  | tee /tmp/docplane-replace-receipt.json | jq .
```

A stale revision is not a successful no-op. On `PAGE_REVISION_STALE`, re-read the current page, rebase the intended edit and retry with the returned current revision and a new idempotency key.

## 5. Create a page through an explicit change

Creation uses the multi-operation workflow. Consult the live operation contract before constructing the operation:

```bash
jq '.operations.CREATE_PAGE' /tmp/docplane-operations.json
```

Create the change:

```bash
CHANGE=$(curl -fsS -X POST \
  -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: change-$(uuidgen)" \
  "$BASE/api/v1/changes" \
  -d '{
    "title":"Create onboarding probe",
    "purpose":"Prove the complete agent publication path",
    "workspace_key":"reference"
  }')

CHANGE_ID=$(jq -r .change_id <<<"$CHANGE")
```

Add the operation:

```bash
CREATE_OPERATION='{
  "operation_type":"CREATE_PAGE",
  "payload":{
    "path":"reference/_agent-onboarding-probe.md",
    "title":"Agent onboarding probe",
    "nav_path":"Reference/Agent onboarding probe",
    "content":"# Agent onboarding probe\n\nDisposable end-to-end verification page.\n"
  }
}'

curl -fsS -X POST \
  -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: operation-$(uuidgen)" \
  "$BASE/api/v1/changes/$CHANGE_ID/operations" \
  -d "$CREATE_OPERATION" | jq .
```

Validate and inspect the candidate before publication:

```bash
curl -fsS -X POST -H "$AUTH" \
  "$BASE/api/v1/changes/$CHANGE_ID/validate" \
  | tee /tmp/docplane-validation.json | jq .

jq -e '.validation_summary.passed == true' /tmp/docplane-validation.json
```

Publish:

```bash
curl -fsS -X POST \
  -H "$AUTH" \
  -H "Idempotency-Key: publish-$(uuidgen)" \
  "$BASE/api/v1/changes/$CHANGE_ID/publish" \
  | tee /tmp/docplane-create-receipt.json | jq .
```

Do not report success from HTTP status alone. Require the receipt to show a completed deployment and current certification with matching working and deployed identities.

## 6. Verify and archive the disposable page

Resolve the created page and retain its current revision:

```bash
PROBE=$(curl -fsS -H "$AUTH" \
  "$BASE/api/v1/pages?status=active&path=reference/_agent-onboarding-probe.md")

PROBE_ID=$(jq -r '.pages[0].resource_id' <<<"$PROBE")
PROBE_REVISION=$(jq -r '.pages[0].revision' <<<"$PROBE")
```

Read it back through the authored API and, where appropriate, verify its rendered URL.

Archive is another explicit operation with an empty payload:

```bash
ARCHIVE_CHANGE=$(curl -fsS -X POST \
  -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: archive-change-$(uuidgen)" \
  "$BASE/api/v1/changes" \
  -d '{
    "title":"Archive onboarding probe",
    "purpose":"Remove the disposable end-to-end verification page",
    "workspace_key":"reference"
  }')

ARCHIVE_CHANGE_ID=$(jq -r .change_id <<<"$ARCHIVE_CHANGE")

curl -fsS -X POST \
  -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: archive-operation-$(uuidgen)" \
  "$BASE/api/v1/changes/$ARCHIVE_CHANGE_ID/operations" \
  -d "$(jq -n \
    --arg id "$PROBE_ID" \
    --arg revision "$PROBE_REVISION" \
    '{operation_type:"ARCHIVE_PAGE",page_resource_id:$id,expected_revision:$revision,payload:{}}')" \
  | jq .

curl -fsS -X POST -H "$AUTH" \
  "$BASE/api/v1/changes/$ARCHIVE_CHANGE_ID/validate" | jq .

curl -fsS -X POST \
  -H "$AUTH" \
  -H "Idempotency-Key: archive-publish-$(uuidgen)" \
  "$BASE/api/v1/changes/$ARCHIVE_CHANGE_ID/publish" \
  | tee /tmp/docplane-archive-receipt.json | jq .
```

Verify the path is absent from active pages and present in archived history.

## 7. Clean up failed probes

If a change is abandoned before publication, close it explicitly rather than leaving DRAFT or VALIDATED debris:

```bash
curl -fsS -X POST \
  -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: abandon-$(uuidgen)" \
  "$BASE/api/v1/changes/$CHANGE_ID/abandon" \
  -d '{"reason":"Probe no longer required"}' | jq .
```

Published changes are immutable audit records and cannot be abandoned.

## 8. Verify certification and deployment state

```bash
curl -fsS -H "$AUTH" "$BASE/api/v1/certification/status" | jq .
curl -fsS -H "$AUTH" "$BASE/api/v1/deployments/attempts?limit=10" | jq .
```

A successful close-out records content-free identifiers such as page resource ID, old/new revisions, change ID, deployment cycle ID and release ID. It never records clear bearer tokens or secrets.

## MCP boundary

The bundled DocPlane MCP server is a client of the same contributor API; it does not own document state. Deployments may expose a local MCP adapter for convenient `search_docs`, `read_doc_outline`, `read_doc_section`, `patch_doc`, `replace_doc_section`, `insert_doc_before_heading`, `insert_doc_after_heading`, `read_doc`, `list_docs`, `write_doc` and `archive_doc` tools. `patch_doc` is the discoverable default for small text edits; `replace_doc_section` and the `insert_doc_*_heading` tools operate on a whole explicit-`{#id}` section with an exact revision and section hash. Raw HTTP remains the complete and authoritative contract, and MCP success must preserve the same revision, publication and certification semantics.

### Redacted pages

Migration redaction markers such as `<REDACTED:...>` are sanitised authored bytes. They are not references that DocPlane rehydrates during publication, and clear replacement secrets must never be restored to documentation. The bundled MCP therefore fails closed around them:

- `read_doc` reports marker presence, count, and whether full-document replacement is allowed;
- `write_doc` refuses a full replacement when the current page or the submitted document contains a marker, and `patch_doc` refuses an edit that introduces or targets one;
- the section tools operate only on marker-free explicit sections with marker-free submitted content.

Marker-bearing sections belong to a separately governed redaction-remediation workflow through raw HTTP; ordinary agents should never replay, remove, relocate, or reconstruct redaction markers through a full-page rewrite.
