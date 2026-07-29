# DocPlane

DocPlane is a documentation control plane. PostgreSQL is the authored source of truth; MkDocs output is a generated, certified release.

Every approved active identity is a **contributor**. There are no document-reader, editor, reviewer, merger or workspace-owner tiers. Workspaces classify content and active work; they do not grant authoring rights.

## Authentication and deployment boundary

DocPlane always uses named bearer credentials for protected API reads and writes. How those credentials are obtained is switchable by deployment:

- **`managed`** — safe repository default for public, external or partner-facing installations. Credentials are operator-issued and self-service issuance is disabled.
- **`private_fabric`** — for installations whose routed hostname is already protected by a private VPN, SD-WAN or equivalent internal-fabric boundary. A reachable agent can self-issue a short-lived, individually attributable AGENT contributor token with no bootstrap secret or DocPlane-side approval round-trip.

The complete threat model, trusted-front requirements, token constraints and configuration are documented in [Authentication profiles](docs/architecture/authentication-profiles.md). Do not enable `private_fabric` on a publicly reachable hostname.

Private-fabric issuance is dual-gated: docs-api must be configured for `private_fabric`, and the request must arrive through the trusted routed front, which injects an internal admission marker on the exact self-issue route. Direct docs-api reachability does not admit issuance.

Clients must start from `/.well-known/docplane.json`; it reports the active profile and exact credential-acquisition path. The complete copy-pasteable path from discovery through create, publish, verify, archive and cleanup is in [Agent onboarding and first publication](docs/architecture/agent-onboarding.md).

A caller's own agent framework may still classify credential issuance as a sensitive action and require human confirmation. That is a caller-side policy gate, not a DocPlane failure. Before approving it, verify the routed endpoint, requested AGENT/CONTRIBUTOR scope and token expiry.

## Authoring contract

The normal workflow is:

1. Read a page and retain its exact `revision`.
2. Create a change with one or more revision-bound operations, or use the one-call page replacement endpoint.
3. Validate the candidate state when using the explicit multi-operation workflow.
4. Publish directly.

Review comments are optional audit events. They never authorize or block publication.

A successful publication:

- revalidates every exact page revision and explicit section hash inside the publishing transaction;
- archives each prior page revision in `docs.page_versions`;
- applies the complete change atomically in PostgreSQL;
- records the contributor, operations and candidate identity;
- builds and promotes the generated MkDocs release;
- records a deployment attempt, release identity and certification state.

If the database mutation succeeds but the site build fails, DocPlane records `DEPLOYMENT_FAILED`. The authored state remains durable and can be published again with `POST /api/v1/publication/retry`; no approval or re-authoring is required.

### Machine-readable operation payloads

A cold client must not infer `CREATE_PAGE`, section-edit, move, redirect or archive payloads from implementation code. DocPlane publishes the complete unauthenticated operation contract at:

```text
GET /api/v1/operation-contracts
```

That document contains, for every supported `operation_type`:

- required revision and section bindings;
- an exact JSON Schema for `payload`;
- a complete request example for `POST /api/v1/changes/{change_id}/operations`.

The same schemas, mapping and examples are embedded in `/openapi.json`. CI fails if the runtime operation vocabulary, payload schemas and examples drift apart.

### MCP authoring contract

The bundled MCP server is a client of the same API and preserves its concurrency model:

- `read_doc` returns the exact page `revision` plus the outline and section hashes needed for bounded edits;
- updating or archiving an existing page requires the caller to pass the revision it actually read;
- omitting `title` or `nav_path` on an existing-page replacement preserves that metadata;
- `replace_doc_section`, `insert_doc_before_heading`, and `insert_doc_after_heading` use explicit heading IDs plus exact section hashes, avoiding full-document retransmission for small edits;
- `patch_doc_metadata` changes title, navigation, workspace classification, knowledge class, or criticality without replacing content;
- stale revisions and stale section hashes return actionable conflicts rather than being silently rebased by the wrapper.

Migration redaction markers are sanitised authored bytes, not references that DocPlane rehydrates at write time. The bundled MCP therefore fails closed around them:

- `read_doc` reports marker presence/count and whether full-document replacement is allowed;
- full-document replacement is refused when the current page or submitted document contains a `<REDACTED:...>` marker;
- bounded edits are allowed only on marker-free explicit sections with marker-free submitted content;
- marker-bearing sections require a separately governed redaction-remediation workflow and must never be repopulated with clear secrets.

The MCP surface remains a convenience layer. Raw HTTP and the machine-readable operation contract remain complete and authoritative.

## Fresh installation

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD, DOCPLANE_EVENT_CURSOR_SECRET and MCP_API_KEY.
# For managed mode, also set DOCPLANE_BOOTSTRAP_TOKEN.
docker compose up --build -d postgres docs-api dashboard docs-web
```

The API container applies `db/migrations/000_docplane_genesis.sql` before it starts serving. There is no alternate SQL bootstrap path.

### Managed installation

Issue the first named contributor token:

```bash
set -a; . ./.env; set +a
bash ./scripts/bootstrap-contributor.sh "Initial Administrator" HUMAN
```

### Private-fabric installation

Set:

```dotenv
DOCPLANE_ACCESS_PROFILE=private_fabric
```

Then a cold agent can discover and self-issue through the routed site URL:

```bash
BASE='http://localhost:8080'
DISCOVERY=$(curl -fsS "$BASE/.well-known/docplane.json")
TOKEN=$(curl -fsS -X POST \
  -H 'Content-Type: application/json' \
  "$BASE/api/v1/auth/self-issue" \
  -d '{"display_name":"example-agent"}' | jq -r .token)
```

The direct API port is not the self-service admission surface; use the routed docs-web hostname.

Store a suitable named token in `DOCPLANE_TOKEN` in `.env`, then start the MCP surface:

```bash
docker compose up --build -d docs-mcp
```

Default local surfaces:

- API: `http://localhost:8010`
- Dashboard: `http://localhost:8051`
- Generated documentation and routed API: `http://localhost:8080`
- MCP: `http://localhost:8049/mcp`

## Direct publication example

```bash
TOKEN='dp_...'
API='http://localhost:8080'

PAGE=$(curl -fsS -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/pages?path=reference/example.md&status=all")

RESOURCE_ID=$(printf '%s' "$PAGE" | jq -r '.pages[0].resource_id')
REVISION=$(printf '%s' "$PAGE" | jq -r '.pages[0].revision')

curl -fsS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-replace-1' \
  "$API/api/v1/pages/$RESOURCE_ID/replace" \
  -d "$(jq -n --arg rev "$REVISION" --arg content '# Example\n\nCorrected.' '{expected_revision:$rev,content:$content,purpose:"Keep the example accurate"}')"
```

## Recovery

- Page history: `GET /api/v1/pages/{resource_id}/history`
- Read an archived revision: `GET /api/v1/pages/{resource_id}/history/{revision}`
- Restore a prior revision: `POST /api/v1/pages/{resource_id}/rollback`
- Certification state: `GET /api/v1/certification/status`
- Deployment attempts: `GET /api/v1/deployments/attempts`
- Retry the current database state: `POST /api/v1/publication/retry`

## Repository structure

- `docs-api/` — sole API authority, publication transaction and release certification
- `db/migrations/` — sole database genesis and ordered schema history
- `dashboard/` — human control surface; owns no document state
- `mcp/` — MCP tools using the same contributor API
- `mkdocs/` — rendered-site configuration
- `docs/architecture/` — product architecture and deployment-security contracts
