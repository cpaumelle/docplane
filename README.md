# DocPlane

DocPlane is a private-fabric documentation control plane. PostgreSQL is the authored source of truth; MkDocs output is a generated, certified release.

Every approved active identity is a **contributor**. There are no document-reader, editor, reviewer, merger or workspace-owner tiers. Workspaces classify content and active work; they do not grant authoring rights.

## Authoring contract

The normal workflow is:

1. Read a page and retain its exact `revision`.
2. Create a change with one or more revision-bound operations.
3. Validate the candidate state.
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

## Fresh installation

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD, DOCPLANE_BOOTSTRAP_TOKEN, DOCPLANE_EVENT_CURSOR_SECRET and MCP_API_KEY.
docker compose up --build -d postgres docs-api dashboard docs-web
```

The API container applies `db/migrations/000_docplane_genesis.sql` before it starts serving. There is no alternate SQL bootstrap path.

Issue the first named contributor token:

```bash
set -a; . ./.env; set +a
bash ./scripts/bootstrap-contributor.sh "Charles" HUMAN
```

Store the returned `token` in `DOCPLANE_TOKEN` in `.env`, then start the MCP surface:

```bash
docker compose up --build -d docs-mcp
```

Default local surfaces:

- API: `http://localhost:8010`
- Dashboard: `http://localhost:8051`
- Generated documentation: `http://localhost:8080`
- MCP: `http://localhost:8049/mcp`

## Direct publication example

```bash
TOKEN='dp_...'
API='http://localhost:8010'

PAGE=$(curl -fsS -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/pages?path=reference/example.md&status=all")

RESOURCE_ID=$(printf '%s' "$PAGE" | jq -r '.pages[0].resource_id')
REVISION=$(printf '%s' "$PAGE" | jq -r '.pages[0].revision')

CHANGE=$(curl -fsS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: example-change-1" \
  "$API/api/v1/changes" \
  -d '{"title":"Correct example","purpose":"Keep the runbook accurate","workspace_key":"reference"}')
CHANGE_ID=$(printf '%s' "$CHANGE" | jq -r '.change_id')

curl -fsS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: example-operation-1" \
  "$API/api/v1/changes/$CHANGE_ID/operations" \
  -d "$(jq -n --arg id "$RESOURCE_ID" --arg rev "$REVISION" --arg content '# Example\n\nCorrected.' '{operation_type:"REPLACE_DOCUMENT",page_resource_id:$id,expected_revision:$rev,payload:{content:$content}}')"

curl -fsS -X POST -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/changes/$CHANGE_ID/validate"

curl -fsS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: example-publish-1" \
  "$API/api/v1/changes/$CHANGE_ID/publish"
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
