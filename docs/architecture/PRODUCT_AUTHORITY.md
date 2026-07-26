# DocPlane product authority

**Status:** proposed

DocPlane is the canonical, community-facing documentation-management product.
Deployments consume versioned DocPlane releases; they do not carry private forks of the
application or periodically copy application code out of an infrastructure monorepo.

## Product boundary

DocPlane owns:

- PostgreSQL-backed authored documentation and version history;
- guarded mutations, page moves, redirects and navigation;
- immutable rendered releases and certification state;
- the operator dashboard;
- MCP and HTTP interfaces for agents;
- generated technical catalogs, including database-schema observations.

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

## Authored knowledge and observed knowledge

DocPlane has two distinct knowledge planes.

### Authored documentation

Authored pages are deliberate, versioned statements. They use the Docs API mutation path,
optimistic concurrency, review policy, certification and immutable release promotion.

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

1. Establish this product authority and the generated-catalog boundary.
2. Port the certified-deployment core as the native fresh-install model.
3. Port the operator dashboard against stable APIs.
4. Add schema-catalog collection and agent tools.
5. Publish versioned images and a clean genesis deployment.
6. Build deployment-specific migration adapters outside the product core.

Each step lands as a separately reviewable PR. No PR should combine product porting with a
production cutover.