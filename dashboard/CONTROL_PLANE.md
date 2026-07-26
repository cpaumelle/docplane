# DocPlane dashboard control-plane contract

The dashboard is the first-class human control surface for the DocPlane product.

It is not a reporting add-on and it is not a second source of truth. The authoritative control plane
remains the versioned DocPlane API. The dashboard renders those contracts, carries the signed-in human
principal, creates idempotent requests and displays durable receipts.

## Product domains

The dashboard exposes these top-level workspaces:

1. **Corpus** — structure, navigation, redirects, classification and review signals.
2. **Reorganisation** — governed page moves, navigation reparenting, section ordering and redirect plans.
3. **Changes** — multi-operation proposals, previews, findings, review and merge receipts.
4. **Certification** — working-state identity, policy, deployment attempts, releases, seals and CURRENT state.
5. **Active work** — initiatives, blockers, soaks, parking, activities and promotion.
6. **Trust and maintenance** — ownership, revision-bound verification, expiry and maintenance queues.
7. **Usage** — dashboard-owned human, agent and automation traffic analytics.
8. **Schema catalog** — generated tbls snapshots, freshness, drift and relationships.
9. **Operations** — health, migrations, backups, workers, webhooks and release storage.

## Reorganisation as a first-class workflow

Reorganisation is not a direct move button. It is a durable plan:

```text
PLAN
  -> ANALYZE_IMPACT
  -> VALIDATE
  -> SUBMIT
  -> APPROVE
  -> EXECUTE
  -> CERTIFY
  -> STABILIZE
  -> CLOSE_OR_COMPENSATE
```

A plan may contain:

- canonical page moves;
- navigation reparenting;
- batched moves;
- top-level section ordering;
- redirect additions or removals;
- inbound-link rewrites;
- archive or restore operations needed by the target information architecture.

The impact model must include:

- source and destination identities;
- destination/path/nav collisions;
- inbound references;
- redirects before and after;
- navigation-tree diff;
- search and MCP impact;
- policy and validation findings;
- exact candidate-state identity;
- deployment-attempt and release identities;
- post-promotion stabilization evidence;
- a compensating-operation plan.

Execution is permitted only through WP8-guarded APIs. The dashboard never invokes filesystem moves,
container commands, raw SQL or MkDocs edits.

## API boundary

The dashboard backend is a narrow browser-facing facade over allowlisted DocPlane endpoints. It forwards
the signed-in human principal and idempotency keys. It does not hold a broad service credential that can
silently perform mutations on behalf of every operator.

The current facade reserves these reorganisation routes:

- `GET /api/v1/reorganisation/tree`
- `GET /api/v1/reorganisation/plans`
- `POST /api/v1/reorganisation/plans`
- `POST /api/v1/reorganisation/plans/{id}/operations`
- `POST /api/v1/reorganisation/plans/{id}/analyze`
- `POST /api/v1/reorganisation/plans/{id}/validate`
- `POST /api/v1/reorganisation/plans/{id}/submit`
- `POST /api/v1/reorganisation/plans/{id}/execute`
- `POST /api/v1/reorganisation/plans/{id}/compensate`

The facade does not make these operations real by itself. The authoritative APIs, state machine and WP8
merge/publication path must land separately and advertise the capabilities through
`/.well-known/docplane.json`.

## Usage boundary

Usage statistics belong to the dashboard domain because they are operator intelligence, not knowledge or
certification authority.

The dashboard owns:

- page-view, API-read, MCP-read, search, AI-citation and feedback instrumentation;
- human/agent/automation separation;
- retention policy;
- aggregation and trend computation;
- high/low traffic panels;
- search-gap and no-result analysis;
- maintenance recommendations derived from usage combined with ownership, age and verification.

Traffic never archives or rewrites content automatically. It creates review evidence.

The core product event stream remains available for durable domain events and integration cursors. The
dashboard may consume that stream, but usage-specific schemas and collectors remain dashboard-owned.

## Authentication

The initial UI accepts a short-lived bearer token in memory for development. It is not persisted by the
browser. Production deployments should replace this with OIDC or another server-side session flow that
maps the human to a named DocPlane principal and preserves workspace roles and audit identity.

## Invariants

- No direct dashboard database connection.
- No normal SSH or container shell requirement.
- No filesystem mutation.
- No hidden mutation credential.
- Every mutation is idempotent and attributable.
- Every reorganisation operation is previewed and validated.
- Every executed plan produces WP8 deployment and certification receipts.
- Recovery uses compensating plans or certified release recovery, never silent manual repair.
