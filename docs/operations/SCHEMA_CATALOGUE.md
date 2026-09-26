# Schema-catalogue reconciliation

Each catalogued live PostgreSQL database is its own schema-catalogue source
authority (DocPlane's own database is the canary; `charliehub_domains` schemas
`ccm` and `transit` are the first product pilot, initiative e004787b).
`scripts/schema_catalogue.py` reads structure in a read-only transaction and is
the sole writer of its generated DocPlane projections: the catalogue pages and
the structured schema projection. Rendered catalogue pages are evidence, never
an alternate schema authority.

## Projection contract 2 and the structured projection

Decision D1 (e004787b, 2026-09-26): table, column, constraint and index
structure lives in a generator-owned **structured projection**, not in
TABLE/COLUMN MODEL entities. DATABASE and SCHEMA remain the MODEL identities.

- `scripts/schema_catalogue_source.py` (contract 2) reads `pg_catalog` only, with
  `search_path` pinned to `pg_catalog` inside its read-only transaction, and
  returns per schema `tables`, `views` and `enums` with FK column pairs, CHECK and
  exclusion constraints, index columns/INCLUDE/predicates, `udt`, enum and array
  identity, identity/generated columns and comments.
- `scripts/schema_catalogue_projection.py` derives, from that one structure, the
  stored projection document (structure verbatim + explicit FK `relations`) and
  the human pages: one overview per database, one page per schema, table
  sections (never table pages), Mermaid ER diagrams in brace-free word form, and
  configured viewpoints.
- `PUT /api/v1/model/artifacts/{id}/projection` stores it (`model.artifact_projections`,
  migration 018). Only the artifact's declaring generator may write; the API
  re-derives the fingerprint from `document.schemas`, refuses any secret-shaped
  value, and a byte-identical document is a zero-write no-op.
- Agents read it through `/api/v1/model/structure[...]`,
  `/api/v1/model/structure-columns` and `/api/v1/model/structure-relations/...`
  (consolidated MCP: `schema_list`, `schema_describe`, `schema_find_column`,
  `schema_relations`, `schema_provenance`).
- Presentation configuration lives in Git at
  `config/schema-catalogue/<db_key>.yml`: schema descriptions, viewpoints and the
  stated canonical-from-migrations status (`AVAILABLE`, `NOT_YET_COMPARED`,
  `NOT_AVAILABLE`). It can never add structure. Canonical availability is never
  inferred from the deployed schema (decision D3).
- Publication order is: pages + GENERATED ownership (atomic) -> CATALOGUES ->
  structured projection -> GENERATION evidence, last. Idempotency keys bind the
  *transition* (artifact + last GENERATION evidence -> new render identity), so a
  retried transition replays exactly while a structure that returns to an earlier
  shape (a migration rollback) never reuses stale receipts.

Contract 2 changes every existing fingerprint. The first contract-2 run for the
`docplane` canary performs an atomic successor handoff of its artifact; the
observer on contract 2 will report DRIFTED until that run completes, which is
correct evidence, not a failure. Deploy the observer and generator code together.

## Execution paths and authority boundary

Decision D2 (e004787b): schema generation is SCHEDULED and fingerprint-gated.
An unchanged structure, configuration and stored projection is a zero-write
no-op, so the schedule bounds staleness rather than causing churn; observation
remains the independent freshness/drift evidence and never triggers generation.
The attended entrypoint remains supported:

```bash
sudo /opt/docplane/scripts/run_schema_catalogue_reconciliation.sh
```

The canonical Unix execution identity is root. It can read the dedicated
secret, inspect the Compose runtime, and create the shared runtime lock without
granting the broad deployment environment to an unprivileged account.

The wrapper owns the complete runtime contract:

1. load the protected environment;
2. acquire the logical `schema-catalogue` exclusion domain;
3. resolve the live PostgreSQL service from Docker Compose identity;
4. construct a transient source DSN in process memory;
5. execute `scripts/schema_catalogue.py`.

Do not invoke the Python generator directly in production.

`scripts/schema_catalogue_observer.py` is the separate source-observation
entrypoint. It imports `introspect()` and `fingerprint()` directly from
`scripts/schema_catalogue_source.py`; it never imports the generator. Its sole
permitted durable write is one entity-scoped `FRESHNESS_CHECK` through OBSERVE.
A changed fingerprint is evidence only and never invokes generation,
publication, MODEL repair, CATALOGUES reconciliation or condition derivation.

The observer's protected wrapper is
`scripts/run_schema_catalogue_source_observer.sh`. It reproduces the generator
wrapper's runtime discovery and participates in the same nonblocking
`schema-catalogue` exclusion domain. Contention is a benign skipped opportunity:
it returns success and emits no observation. All other failures remain visible.

## Protected environment

The canonical secret file is
`/etc/charliehub/docplane-schema-catalogue.env`. The runtime path
`/etc/docplane/schema-catalogue.env` is a compatibility symlink to it. The
resolved file must be owned by `root:root` with mode `0600` and contain:

```text
DOCPLANE_API=https://docplane.charliehub.internal
DOCPLANE_SCHEMA_CATALOGUE_TOKEN=<schema automation bearer>
CATALOGUE_DB_KEY=docplane
CATALOGUE_DB_DISPLAY='DocPlane PostgreSQL'
CATALOGUE_SCHEMAS=docplane,docs,model,observe,work
CATALOGUE_SOURCE_DB=<configured PostgreSQL database>
CATALOGUE_SOURCE_USER=<configured PostgreSQL user>
CATALOGUE_SOURCE_PASSWORD=<configured PostgreSQL password>
CATALOGUE_SOURCE_PORT=5432
CATALOGUE_SOURCE_COMPOSE_PROJECT=docplane
CATALOGUE_SOURCE_COMPOSE_SERVICE=postgres
```

Never place `CATALOGUE_SOURCE_DSN` in the file. The PostgreSQL container address
is runtime state and changes across recreation. The broad `/opt/docplane/.env`
remains Compose deployment configuration; it is not the schema reconciler's
runtime contract.

The future observer runtime uses a distinct protected file at
`/etc/docplane/schema-catalogue-observer.env`, also owned by its execution
identity with mode `0600`. It must name its dedicated values only:

```text
DOCPLANE_API=https://docplane.charliehub.internal
DOCPLANE_SCHEMA_OBSERVER_TOKEN=<dedicated Schema observer AUTOMATION bearer>
CATALOGUE_DB_KEY=docplane
CATALOGUE_SCHEMAS=docplane,docs,model,observe,work
CATALOGUE_SOURCE_DB=<configured PostgreSQL database>
CATALOGUE_SOURCE_USER=<dedicated least-privilege observer role>
CATALOGUE_SOURCE_PASSWORD=<observer database credential>
CATALOGUE_SOURCE_PORT=5432
CATALOGUE_SOURCE_COMPOSE_PROJECT=docplane
CATALOGUE_SOURCE_COMPOSE_SERVICE=postgres
```

The observer never falls back to the generator bearer or PostgreSQL role.
Neither protected file may persist `CATALOGUE_SOURCE_DSN`; both wrappers derive
it from current Compose identity and pass it only in process memory.

## Runtime discovery and exclusion

The wrapper selects one running container by the stable Compose project and
service labels. It then requires exactly one usable container-network address.
Zero or ambiguous containers or addresses fail closed; there is no localhost,
container-name, stale-address or broad-environment fallback.

The logical exclusion domain is `schema-catalogue`. Its deployed lock is
`/run/lock/docplane-schema-catalogue.lock`, opened nonblockingly and held over
both discovery and generation. Expected contention exits with status `75` and
does not start the generator. The lock path is an implementation detail; every
legitimate execution path must use the same domain.

No credential, DSN, environment dump or source row data belongs in operational
output. A dry run still uses the canonical wrapper:

```bash
sudo /opt/docplane/scripts/run_schema_catalogue_reconciliation.sh --dry-run
```

Dry-run introspection is read-only and does not publish, but it still requires
the named schema automation credential because the generator validates its
complete runtime contract.

## Observer PostgreSQL contract

The dedicated observer database role is configuration and runbook state, not a
MODEL execution-contract field. The role must be created through a separately
authorised deployment using the migration-owning role and this exact privilege
shape (replace the angle-bracket role names safely in the attended procedure):

```sql
CREATE ROLE <schema_observer_role> LOGIN PASSWORD '<credential-authority-value>';
ALTER ROLE <schema_observer_role> SET default_transaction_read_only = on;
ALTER ROLE <schema_observer_role> SET search_path = docs;

GRANT USAGE ON SCHEMA docplane, docs, model, observe, work
  TO <schema_observer_role>;
GRANT REFERENCES ON ALL TABLES IN SCHEMA docplane, docs, model, observe, work
  TO <schema_observer_role>;

ALTER DEFAULT PRIVILEGES FOR ROLE <migration_owning_role>
  IN SCHEMA docplane, docs, model, observe, work
  GRANT REFERENCES ON TABLES TO <schema_observer_role>;
```

This is intentionally not a row-reading role and has no write privilege.
Under contract 1, `REFERENCES` was required because `information_schema.columns`
filters metadata by table privilege, and `search_path=docs` preserved the
generator's `pg_get_constraintdef()` text and therefore its fingerprint.

Contract 2 removes both dependencies: it reads `pg_catalog` only and pins its
own `search_path`. Proven 2026-09-26 on a schema-only copy of
`charliehub_domains`: a bare `LOGIN` role with `default_transaction_read_only =
on` and **no** schema or table grants produced a byte-identical projection (57
tables, 663 columns) while row reads and writes were refused. The existing
DocPlane observer role above keeps working unchanged; new source databases need
only that bare read-only login (plus `CONNECT` if the database revokes it from
`PUBLIC` — verify at the gate).

Before any production role is created, repeat the disposable integration proof
from `docs/architecture/SCHEMA_OBSERVER_PRIVILEGE_PROOF.md`: owner and observer
structures and fingerprints must be identical, all catalogued tables must have
non-empty column projections, parity must survive a newly migrated table, and
observer row reads and writes must be refused.

## Additional databases

Each additional database is one instance of the template units, keyed by its
`CATALOGUE_DB_KEY`, with its own exclusion domain
(`/run/lock/docplane-schema-catalogue-<db_key>.lock`) shared only by that
database's generator and observer:

- `docplane-schema-catalogue@<db_key>.service` / `.timer` — generation, reading
  `/etc/docplane/schema-catalogue.d/<db_key>.env`;
- `docplane-schema-catalogue-observer@<db_key>.service` / `.timer` — observation,
  reading `/etc/docplane/schema-catalogue-observer.d/<db_key>.env`.

For the `charliehub_domains` pilot the generator environment (root:root, 0600)
names only non-secret selectors plus its dedicated credentials:

```text
DOCPLANE_API=https://docplane.charliehub.internal
DOCPLANE_SCHEMA_CATALOGUE_TOKEN=<schema automation bearer>
CATALOGUE_DB_KEY=charliehub_domains
CATALOGUE_DB_DISPLAY='CharlieHub control-plane PostgreSQL'
CATALOGUE_SCHEMAS=ccm,transit
CATALOGUE_ENVIRONMENT=production
CATALOGUE_SOURCE_IDENTITY=hub2/charliehub-postgres
CATALOGUE_SOURCE_DB=charliehub_domains
CATALOGUE_SOURCE_USER=<dedicated bare read-only catalogue role>
CATALOGUE_SOURCE_PASSWORD=<that role's credential>
CATALOGUE_SOURCE_PORT=5432
CATALOGUE_SOURCE_COMPOSE_PROJECT=charliehub
CATALOGUE_SOURCE_COMPOSE_SERVICE=charliehub-postgres
```

The wrapper runs on the hub2 host and reaches the container-network address
directly; `charliehub-postgres` authenticates network connections with
`scram-sha-256`, so no new route or published port is needed. The source role
must never be the `charliehub` superuser or an application role.

## Inert units and later gates

The repository ships `config/systemd/docplane-schema-catalogue-observer.service`
and `.timer`, the scheduled-generation `docplane-schema-catalogue.service` and
`.timer` for the canary (sharing the legacy lock and environment above), and the
`@` template units for additional databases. Repository presence is not installation or activation. The timer
declares a 30-minute opportunity, is non-persistent, and has no effect until a
later authorised deployment installs the files and an even later activation
gate enables it.

The governed order remains: create the dedicated principals/runtime contract;
run one attended canary; only then declare the MODEL execution contract
(`SCHEDULED`, two-hour maximum evidence age, `schema-catalogue` exclusion);
install units disabled/non-triggering; separately enable recurring execution;
then complete the required 24-hour soak. None of those acts is performed by
the implementation PR.

For scheduled generation and each additional database the same separation
applies: deploy code and migration 018; provision the source role and
protected environment; run one attended generation canary and one attended
observer canary; update the artifact execution contract to generation trigger
`SCHEDULED`; install the units inert; enable separately; soak.
