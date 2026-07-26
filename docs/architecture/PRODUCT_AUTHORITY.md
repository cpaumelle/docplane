# DocPlane product authority

**Status:** proposed

DocPlane is the canonical, community-facing documentation-management product.
Deployments consume versioned DocPlane releases; they do not carry private forks of the
application or periodically copy application code out of an infrastructure monorepo.

## Product boundary

DocPlane owns:

- PostgreSQL-backed authored documentation and version history;
- active-work spaces, initiatives, soaks and parking lots;
- guarded mutations, change proposals, page moves, redirects and navigation;
- immutable rendered releases and certification state;
- the human authoring application and operator dashboard;
- MCP and HTTP interfaces for agents;
- generated technical catalogs, including database-schema observations;
- observer, event and usage-analytics APIs.

A deployment repository owns only its implementation choices:

- pinned DocPlane image digests;
- hostnames, routing and certificates;
- secrets and environment configuration;
- backup destinations and retention;
- monitoring integration;
- registered catalog data sources;
- deployment-specific policy and accepted debt.

Deployment-specific hostnames, credentials, corpus content, network topology and policy
exceptions must not enter this repository.

## API-first operations

DocPlane follows an endpoint-first operating model.

- Human applications, agents, CLIs and automations use versioned HTTP, MCP, event and webhook
  contracts.
- Normal discovery, reads, writes, moves, reviews, imports, exports, backup orchestration and
  operational status do not require SSH, container access, filesystem inspection or direct SQL.
- The product exposes the information needed to operate it: capabilities, health, readiness,
  migrations, certification, deployment attempts, schema catalogs, usage, audit and task state.
- Administrative endpoints are authenticated, scoped, auditable and fail closed.
- Long-running operations return durable task or change identifiers and observable progress rather
  than requiring an operator to watch a shell session.
- SDKs and the CLI wrap the same public contracts; helper scripts are convenience clients, never the
  control boundary.

SSH, direct database inspection and container shells remain break-glass diagnostic tools for a
self-hosted operator. They are not a supported application workflow, not required by agents, and not
used to compensate for missing product endpoints. When routine work repeatedly requires shell access,
that is evidence of a missing DocPlane capability that should be added to the product.

## Knowledge planes

DocPlane has three distinct knowledge planes.

### Durable authored knowledge

Authored pages are deliberate, versioned statements. They use guarded mutation or change proposals,
optimistic concurrency, review policy, verification, certification and immutable release promotion.

### Active work

Initiatives, WIP notes, blockers, soaks, handoffs and parking-lot items are searchable and auditable,
but explicitly non-authoritative until durable conclusions are promoted through a reviewed change.

### Generated technical catalog

Generated catalog records are observations of external systems. They are not ordinary pages
and are not inserted into the authored-page tables. A catalog snapshot carries:

- source identity and owner;
- collection timestamp and collector version;
- a deterministic schema fingerprint;
- structured machine-readable schema data;
- generated human-readable artifacts;
- freshness and collection status;
- a diff from the previous accepted snapshot.

A failed refresh makes a snapshot stale; it must never silently make the previous observation
look current.

## CharlieHub relationship

`docplane.charliehub.internal` is a deployment of this product. CharlieHub-specific migration
adapters and cutover evidence may live in a deployment repository or under a clearly isolated
`contrib/` boundary, but they do not define the product runtime.

The existing Hub2 documentation application is a migration source, not the future upstream.
After cutover, Hub2 should retain only the infrastructure integration required to route,
monitor and register the DocPlane deployment.

## Delivery sequence

1. Establish product authority, API-first operation and the three knowledge-plane boundaries.
2. Port the certified-deployment core as the native fresh-install model.
3. Add workspace, initiative, lifecycle, ownership and verification contracts.
4. Add usage analytics, event feeds and stable observer APIs.
5. Add the human authoring and operator applications against those APIs.
6. Add schema-catalog collection and agent tools.
7. Publish versioned images, SDKs, CLI and a clean genesis deployment.
8. Build deployment-specific migration adapters outside the product core.

Each step lands as a separately reviewable PR. No PR should combine product porting with a
production cutover.
