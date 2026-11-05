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

## DocPlane principal scopes

Automation principals remain full contributors unless the bootstrap request
includes explicit `artifact_scopes`. A scoped principal is marked
`SCOPED_AUTOMATION` and cannot use other write routes. Each scope binds an exact
artifact key to `GENERATE` or one observation kind. A GENERATE grant also binds
the source DATABASE entity key and a relative page-path prefix; change plans and
every page operation are checked against that prefix. For MODEL identity it
permits only that DATABASE entity, its SCHEMA children, `STORES_IN` links from
those schemas to the database, and exact `CATALOGUES` links to pages under the
same prefix. Other entity kinds, entity updates, links, and page-link relations
remain denied. An OBSERVE grant binds to
one artifact and either `FRESHNESS_CHECK` or `GENERATION`. Artifact, source,
change, page, and observation checks are enforced by docs-api on each write.

For a catalogue, use separate generator and observer principals. The generator
receives only `GENERATE` for its artifact and the corresponding source database
and catalogue path. The observer receives only `OBSERVE/FRESHNESS_CHECK` for
that artifact. The generator may receive `OBSERVE/GENERATION` only when it must
record its own completed generation. Scopes are established when the named
automation principal is created through the existing bootstrap authority; do
not use an unscoped shared token for these jobs.

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
Neither protected file may persist `CATALOGUE_SOURCE_DSN`; the wrappers
rediscover the Compose address, then the Python entrypoint resolves the password
and passes keyword connection parameters directly to psycopg2. No credential-
bearing DSN is assembled, persisted, or logged.

For the VM1124 Trevarn attended one-shot, settings contain secret paths only:

```text
# /etc/docplane/schema-catalogue.d/trevarn.env (root:root 0600)
DOCPLANE_API=https://docplane.charliehub.internal
DOCPLANE_SCHEMA_CATALOGUE_TOKEN_FILE=/run/charliehub/secrets/trevarn-schema-catalogue-generator/docplane-token
CATALOGUE_DB_KEY=trevarn
CATALOGUE_DB_DISPLAY='Trevarn PostgreSQL'
CATALOGUE_SCHEMAS=platform,ingest,occupancy,analytics,parking,contact,public
CATALOGUE_SOURCE_DB=trevarn
CATALOGUE_SOURCE_USER=trevarn_schema_catalogue_reader
CATALOGUE_SOURCE_PASSWORD_FILE=/run/charliehub/secrets/trevarn-schema-catalogue-generator/db-password
CATALOGUE_SOURCE_PORT=5432
CATALOGUE_SOURCE_COMPOSE_PROJECT=trevarn-core
CATALOGUE_SOURCE_COMPOSE_SERVICE=postgres
CATALOGUE_ENVIRONMENT=development
CATALOGUE_SOURCE_IDENTITY=VM1124/trevarn
CATALOGUE_SOURCE_SSLMODE=disable

# /etc/docplane/schema-catalogue-observer.d/trevarn.env (root:root 0600)
DOCPLANE_API=https://docplane.charliehub.internal
DOCPLANE_SCHEMA_OBSERVER_TOKEN_FILE=/run/charliehub/secrets/trevarn-schema-catalogue-observer/docplane-token
CATALOGUE_DB_KEY=trevarn
CATALOGUE_SCHEMAS=platform,ingest,occupancy,analytics,parking,contact,public
CATALOGUE_SOURCE_DB=trevarn
CATALOGUE_SOURCE_USER=trevarn_schema_catalogue_observer
CATALOGUE_SOURCE_PASSWORD_FILE=/run/charliehub/secrets/trevarn-schema-catalogue-observer/db-password
CATALOGUE_SOURCE_PORT=5432
CATALOGUE_SOURCE_COMPOSE_PROJECT=trevarn-core
CATALOGUE_SOURCE_COMPOSE_SERVICE=postgres
CATALOGUE_ENVIRONMENT=development
CATALOGUE_SOURCE_IDENTITY=VM1124/trevarn
CATALOGUE_SOURCE_SSLMODE=disable
```

Each secret file is delivered separately under its consumer directory with
root ownership and mode `0400`; the settings directories are root-owned mode
`0700`. The wrappers verify the configured Compose container has exactly one
usable address and, before allowing `sslmode=disable`, confirm it is attached
to `trevarn-net` with the VM1124 development source labels above. The verified
network name is process-local state and is not configured in the settings file.
PostgreSQL
remains unpublished. Existing callers that supply a DSN directly retain the
legacy `CATALOGUE_SOURCE_DSN[_FILE]` compatibility path; the host wrappers
reject DSNs in their settings files.

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

## Adding a schema to an existing database

Not every service that owns tables is a new database. A service may live in a
schema of a database the catalogue already sources. LAN Watcher is the worked
example: it connects to the existing `charliehub_domains` cluster
(`DB_NAME=charliehub_domains`, role `lan_watcher`, host `charliehub-postgres`)
and creates its tables in the shared `public` schema, not a database or schema
of its own. Onboarding it is therefore adding the `public` schema to the
existing `charliehub_domains` catalogue, not registering an additional database.

Because the catalogue introspects whole schemas only, adding `public` catalogues
**every** object in it — LAN Watcher's `devices`, `ip_assignments`, the
`lan_hosts` view, `lan_heartbeats`, `network_actions_log`, `device_icons`,
`network_devices`, `infrastructure_assets` and `lan_hosts_legacy`, but also the
CCM-LAN `ccm_lan_*` tables, the Domain Manager `domains` table and hand-applied
control-plane tables (`authelia_realms`, `nodes`, `deploy_log`,
`governance_audit`, and others). There is no table filter and viewpoints only
group tables for readers; they never restrict introspection. The schema
description must therefore state that `public` has no single writer, and its
`canonical.status` is `NOT_AVAILABLE` because no single migration source
reproduces a multi-service schema.

Two changes are required, in separate governed gates:

1. **Presentation (this PR, config only).** Add a `public:` entry to
   `config/schema-catalogue/charliehub_domains.yml` with its description, the
   `NOT_AVAILABLE` canonical status and a viewpoint grouping LAN Watcher's own
   tables under `network/fabric-v2/lan-watcher.md` as the authority. This adds no
   structure and cannot introspect anything on its own.
2. **Introspection (separate, not in this PR).** Add `public` to
   `CATALOGUE_SCHEMAS` in the protected generator environment
   `/etc/charliehub/docplane-schema-catalogue.env`
   (`CATALOGUE_SCHEMAS=ccm,transit,public`) and the matching observer
   environment. This is a change to a deployed protected file and is gated by
   the same governed rollout as the pilot (attended canary, then soak). The
   existing `charliehub_domains` source role already reaches the database; the
   catalogue reads `public` under projection contract 2 with **no** table grants
   and needs only `USAGE ON SCHEMA public` if `public` usage has been revoked
   from `PUBLIC` (verify at the gate — by default `PUBLIC` holds `USAGE` on
   `public`, so no new grant is typically required). Never grant the source role
   any table privilege, and never reuse an application role.

Once `public` is introspected, the generated pages are
`model/schema-catalogue/charliehub_domains/public.md` (plus the refreshed
`model/schema-catalogue/charliehub_domains/index.md` overview).

## Trevarn: production catalogue on vps3

Trevarn's deployed production database is onboarded as **one more instance of the
existing template units**, alongside `charliehub_domains` and `docplane`, with the
same generator/observer lifecycle, scoped principals and governed gates described
above. Nothing in this section is new mechanism.

| db_key | Environment | Source identity | Runs on | Source database |
|---|---|---|---|---|
| `trevarn` | `production` | `VPS3/trevarn` | vps3 host, root, `docplane-schema-catalogue@trevarn` and `docplane-schema-catalogue-observer@trevarn` from a read-only `/opt/docplane` checkout at the reviewed tag | live `trevarn-core/postgres` container `trevarn-postgres`, database `trevarn`, PostgreSQL 16.13, roles `trevarn_schema_catalogue_reader` / `_observer` (trevarn-core `ops/metadata-reader`, CONNECT-only, `pg_catalog` reads) |

`CATALOGUE_SCHEMAS=platform,ingest,occupancy,analytics,parking,contact,public`
(`cbre_compat` is deliberately excluded, as in the presentation config). Candidate
and staging coverage is a **separate** catalogue and a separate decision; see
*Trevarn: staging catalogue* below. Main's unapplied migrations are never
presented as production structure under `trevarn`.

### Plaintext Docker bridges (allowlist)

Both Trevarn PostgreSQL containers run without server TLS and publish no port, so
the host-local bridge hop uses `sslmode=disable`. That is accepted only when the
wrapper has verified the single discovered container is attached to the network
bound to the `(CATALOGUE_ENVIRONMENT, CATALOGUE_SOURCE_IDENTITY)` pair:

| Environment | Source identity | Required network |
|---|---|---|
| `development` | `VM1124/trevarn` | `trevarn-net` |
| `production` | `VPS3/trevarn` | `trevarn-net` |

The table lives twice on purpose and must stay in step: `plaintext_bridge_network()`
in both wrappers and `_PLAINTEXT_BRIDGES` in `scripts/schema_catalogue_connection.py`.
Adding a pair is a code change with its own review, never a settings-file value.
This replaces the single hard-coded VM1124 rule; the VM1124 development one-shot
is unchanged.

### Production settings on vps3

```text
# /etc/docplane/schema-catalogue.d/trevarn.env (root:root 0600)
DOCPLANE_API=https://docplane.charliehub.internal
DOCPLANE_SCHEMA_CATALOGUE_TOKEN_FILE=/etc/trevarn/secrets/trevarn-schema-catalogue-generator/docplane-token
CATALOGUE_DB_KEY=trevarn
CATALOGUE_DB_DISPLAY='Trevarn PostgreSQL'
CATALOGUE_SCHEMAS=platform,ingest,occupancy,analytics,parking,contact,public
CATALOGUE_SOURCE_DB=trevarn
CATALOGUE_SOURCE_USER=trevarn_schema_catalogue_reader
CATALOGUE_SOURCE_PASSWORD_FILE=/etc/trevarn/secrets/trevarn-schema-catalogue-generator/db-password
CATALOGUE_SOURCE_PORT=5432
CATALOGUE_SOURCE_COMPOSE_PROJECT=trevarn-core
CATALOGUE_SOURCE_COMPOSE_SERVICE=postgres
CATALOGUE_ENVIRONMENT=production
CATALOGUE_SOURCE_IDENTITY=VPS3/trevarn
CATALOGUE_SOURCE_SSLMODE=disable
# optional, see "Migration-ledger identity": CATALOGUE_LEDGER_TABLE=public.schema_migrations

# /etc/docplane/schema-catalogue-observer.d/trevarn.env (root:root 0600)
DOCPLANE_API=https://docplane.charliehub.internal
DOCPLANE_SCHEMA_OBSERVER_TOKEN_FILE=/etc/trevarn/secrets/trevarn-schema-catalogue-observer/docplane-token
CATALOGUE_DB_KEY=trevarn
CATALOGUE_SCHEMAS=platform,ingest,occupancy,analytics,parking,contact,public
CATALOGUE_SOURCE_DB=trevarn
CATALOGUE_SOURCE_USER=trevarn_schema_catalogue_observer
CATALOGUE_SOURCE_PASSWORD_FILE=/etc/trevarn/secrets/trevarn-schema-catalogue-observer/db-password
CATALOGUE_SOURCE_PORT=5432
CATALOGUE_SOURCE_COMPOSE_PROJECT=trevarn-core
CATALOGUE_SOURCE_COMPOSE_SERVICE=postgres
CATALOGUE_ENVIRONMENT=production
CATALOGUE_SOURCE_IDENTITY=VPS3/trevarn
CATALOGUE_SOURCE_SSLMODE=disable
```

Secret files follow vps3's existing recovery-escrow pattern
(`/etc/trevarn/secrets/<consumer>/…`, root `0400`, persistent across reboot as for
`vps3-offsite-s3`), because a **scheduled** production collector must survive a reboot
without an attended restore; the writer, escrow records and verification are in
charliehub-hub2 `ops/secrets-v3/docs/trevarn-schema-catalogue.md` (profile `vps3`).
Settings files contain paths only. This does not change how hub2's own two catalogue
settings files deliver their bearers; their plaintext-environment delivery (and the
deprecation warning each run logs) is a separate follow-up, not widened here.

### Refresh after a production deployment, with the scheduled safety net

trevarn-core's maintained deployment tool (`deploy/vps3/vps3_deploy.py`, which the
release packet and `release.py` drive and pin) requests
`systemctl start --no-block docplane-schema-catalogue@trevarn.service` after a
successful, verified deploy that applied at least one migration, and records the
request (`catalogue_refresh`) in its deploy-ledger `end` record. **Activation
boundary:** the request is made only when `docplane-schema-catalogue@trevarn.timer`
reports `enabled`, which is the owner's scheduled-operation activation act; a unit that
is merely installed is never started, and the ledger records `not activated`. The
request is otherwise best-effort: a missing unit, a disabled hook or a failing start
never changes the deploy result. The per-database timer (30 min) and observer timer
remain the safety net; an unchanged structure is a zero-write no-op.

### Coverage and known unsupported areas

Contract 2 captures tables (incl. partitioned), views and materialized views
(definition, columns), enums, columns (type, udt, array, nullability, default,
identity, generated, comment), primary keys, foreign keys with exact column pairs,
unique/check/exclusion constraints, indexes (columns, INCLUDE, predicate), and schema
and relation comments. **Not captured** by contract 2 and therefore not claimed for
Trevarn: function and procedure signatures (`platform.twin_asof()`, `local_zone()`,
`retwin_device()`, trigger functions), triggers, row-level-security policies,
grants/ACLs, sequences and extensions. Those remain KNOW statements on
`services/trevarn/database.md` until a separately reviewed contract extension.

### Activation gates (unchanged order, none performed by code review)

1. Production roles exist with the reviewed shape (`provision-schema-catalogue-reader.sql`, `provision-schema-catalogue-observer.sql`).
2. Two scoped DocPlane principals exist for artifact `schema-catalogue-trevarn` (generator: `GENERATE` with source entity `trevarn` and page prefix `model/schema-catalogue/trevarn/`, plus `OBSERVE/GENERATION`; observer: `OBSERVE/FRESHNESS_CHECK` only).
3. Secrets enrolled, written and escrow-verified; `/opt/docplane` checkout and units installed **disabled** on vps3.
4. Attended canary: `--dry-run`, one generation, one observation; owner accepts `schema_provenance trevarn` (FRESH, MATCH, CURRENT) and the generated pages.
5. Execution contract set to `SCHEDULED`; timers enabled; 24-hour soak; then KNOW pages receive their Data-model links to `model/schema-catalogue/trevarn/`.

### Migration-ledger identity (optional, additive to contract 2)

Contract 2 records `provenance.migration_ledger` as `NOT_CAPTURED` because the source
contract reads structure only. For Trevarn the deployed **migration set** is release
evidence: the Development Process Charter binds a release manifest to its migration
set, and a maximum migration number does not prove which files were applied. A
catalogue may therefore opt in with `CATALOGUE_LEDGER_TABLE=<schema>.<table>`
(Trevarn: `public.schema_migrations`, columns `filename`, `sha256`, `applied_at`,
written only by `db/migrate.sh`; no row of it is tenant data). When set, the generator
reads the ledger in its own read-only transaction and records:

```json
"migration_ledger": {
  "status": "CAPTURED",
  "table": "public.schema_migrations",
  "rows": 126,
  "head_filename": "332_glanik_checkins_scope.sql",
  "head_sha256_prefix": "<24 hex>",
  "head_applied_at": "2026-11-04T21:57:10+00:00",
  "ledger_set_digest_prefix": "<24-hex prefix of sha256 over the sorted set of filename<TAB>sha256 lines>",
  "digest_prefix_hex": 24,
  "identity_note": "truncated comparison identifiers, not integrity hashes"
}
```

The two digests are **truncated comparison identifiers** (24 hex, 96 bits): the projection
store refuses any 40+ character hex value as secret-shaped (`PROJECTION_SECRET_SHAPED`,
found by the development end-to-end run). They are for comparing captures with each
other and with a release manifest's migration list; they do **not** substitute for the
manifest's full integrity hashes, which stay in the release record.

`ledger_set_digest_prefix` identifies the **set**: two databases with the same head but a gap
differ. Compare it, and `rows`, against the manifest's migration list and between the
production and staging catalogues.

Rules:

- **One guarded capture, coordinated with the migration runner.** Trevarn's `db/migrate.sh`
  (the single supported runner; the vps3 deployment tool, the 285/286 gate, CI and the test
  fixture all call it) commits a migration's DDL and then records its ledger row, so between
  those commits the database holds new structure with the old ledger and re-reading the
  ledger cannot tell. The runner therefore holds `pg_advisory_lock(hashtext('public.schema_migrations')::bigint)`
  in one psql session across the DDL and its ledger row (trevarn-core #556), and the capture
  takes the **shared** form of the same key for its whole read: while a migration holds the
  lock the capture polls for `CATALOGUE_LEDGER_LOCK_WAIT_SECONDS` (default 60) and then
  **refuses** (`MigrationInProgress`); while a capture holds it a migration waits a few
  seconds (`lock_timeout` 120 s on the runner side). Inside the lock the capture still reads
  structure, source metadata and the ledger in one `REPEATABLE READ, READ ONLY` snapshot
  with the ledger compared before and after; a difference means a writer bypassed the runner,
  and the generator retries then refuses (`SourceChangedDuringCapture`). Catalogues without
  a ledger setting keep the original two short reads. Proven on a real PostgreSQL by
  `test_schema_catalogue_capture_integration.py`: a runner paused after DDL commit and before
  the ledger insert makes the capture wait and then return the matching pair, or refuse.
- **A missing ledger never certifies the pair.** An `UNAVAILABLE` or `EMPTY` ledger still
  publishes structure, but `provenance.migration_ledger.capture.consistent` is `false` with
  the reason; `true` requires three identical `CAPTURED` reads under the lock.
- **Ledger and structure stay distinct.** The ledger is never part of the structural
  fingerprint. The independent observer reads no rows and **verifies structure only**:
  its FRESHNESS_CHECK verdict says nothing about ledger freshness; a ledger-only change is
  visible through the render identity and the republished overview, not through the observer. The ledger identity (status, table, rows, head filename, digest;
  never `applied_at`) is folded into the **render identity**, so a ledger-only change
  (a data migration) republishes the overview page and the projection document and
  rewrites **no schema page**; a catalogue without the setting keeps its exact
  previous render identity (no churn, verified by test).
- **Exception to structure-only introspection, narrowly scoped.** The source role
  needs `SELECT` on that one table and nothing else (trevarn-core
  `ops/metadata-reader/grant-schema-catalogue-ledger-read.sql`, which refuses if the
  table ever has columns other than those three). The observer role receives no
  grant. This is the only row read the generator performs, and it is opt-in per catalogue.
- A refused or missing table is recorded as `UNAVAILABLE` with the error class only;
  the run continues and the structure remains authoritative. The redaction guard is
  unchanged: no exemption is needed for prefix-length digests.

### Projection-status metrics for schema catalogues

Until now only the work-catalogue, meter-list and invariant-register generators wrote
`docplane_generated_projection_{drift,reconcile_success,last_run_unixtime}` for
node_exporter, so `DocPlaneGeneratedProjectionDrift`, `…ReconcileFailed` and
`…MetricsStale` could not see any schema catalogue; its only failure signal was the
systemd journal and the observer's FRESHNESS_CHECK evidence inside DocPlane.

The generator now takes `--metrics-file` and the reconciliation wrapper passes
`/var/lib/node_exporter/textfile_collector/docplane_schema_catalogue_<db_key>.prom`
(override `DOCPLANE_SCHEMA_CATALOGUE_METRICS_FILE`) through the shared writer, labelled
`artifact="schema-catalogue-<db_key>"`:

- written on every non-dry run: `reconcile_success` 1 after any successful path
  (published, repaired or UNCHANGED) and 0 when the run raises; `drift` is 0 after a
  successful run by construction (the published projection equals the live source);
- never written by `--dry-run`; skipped with a journal line on a host without the
  textfile directory; the unit files gain `-/var/lib/node_exporter/textfile_collector`
  in `ReadWritePaths`.

The three label-agnostic alerts then cover every schema catalogue, including
`charliehub_domains` and `docplane` on hub2, without a rule change; absence guards
stay per artifact. charliehub-hub2 holds `DocPlaneTrevarnSchemaCatalogueMetricsMissing`
as a **staged** rule file outside the deployed rules directory until the Trevarn
activation gate; promoting it is step 8 of the first-activation plan below. This is a reporting
improvement independent of Trevarn; it changes no publication behaviour.

## Trevarn: staging catalogue on VM1424 (design, inert)

**Status: design and inert configuration only.** `config/schema-catalogue/trevarn_staging.yml`
introspects nothing; no principal, role, route, command, unit or allowlist entry for
staging exists until the activation decisions below are taken. The production catalogue
does not depend on any of this.

**Why 1424, not a synthetic 1124 database.** The Development Process Charter makes
VM1424 the environment that qualifies the accepted release candidate through the
production-style deployment path, including the migration mechanism. Its database is
therefore the structure a release would deploy. The disposable Compose database that
trevarn-core `scripts/schema-candidate-db.sh` builds on VM1124 from `db/baseline` remains
a **generator test fixture** (it is what the end-to-end tests run against), not a
catalogue source: a source commit alone does not prove which migrations were applied.

**Provenance binding.** `CATALOGUE_SOURCE_IDENTITY=VM1424/<stack>@<release-manifest-id>`
names the actual stack and the exact release manifest, and `CATALOGUE_LEDGER_TABLE`
captures the applied set (`rows`, head, `ledger_digest`) to compare with the manifest's
migration list and with the production catalogue. The staging landing page/handoff
already names the current candidate; the catalogue page carries the same identity.

**Smallest supported design, reusing the established pattern.**

1. The generator and observer run **on VM1424** as root one-shots through the unchanged
   wrappers, discovering the stack's PostgreSQL by its Compose labels and connecting to
   its container-network address host-locally. The stack's loopback-only database and
   API bindings are untouched; nothing is published.
2. **No standing timer.** A candidate's structure changes only when a candidate is
   deployed, so the staging `deploy` helper requests one generation after a successful
   deploy (the same best-effort hook shape as production); the observer one-shot runs on
   demand for acceptance evidence.
3. **Credentials boundary.** Staging-only `trevarn_schema_catalogue_reader` /
   `_observer` roles on the staging database (same provisioning SQL, plus the ledger
   grant for the reader), and two DocPlane scoped principals bound to artifact
   `schema-catalogue-trevarn_staging` only (generator: `GENERATE` with source entity
   `trevarn_staging` and page prefix `model/schema-catalogue/trevarn_staging/`, plus
   `OBSERVE/GENERATION`; observer: `OBSERVE/FRESHNESS_CHECK`). Neither can touch the
   production artifact; staging gains no write authority anywhere else.

**Access dependencies to verify before activation (not assumed).** The documented
VM1424 posture is: no default route, a host route only for replies to the internal
ingress source, `stagingadmin` without Docker membership and with fixed sudo commands,
Docker and the VM disabled for automatic start.

| Need | First check (read-only, by the staging operator) | If absent, the exact proposal for review |
|---|---|---|
| HTTPS egress to DocPlane (`docplane.charliehub.internal` = `10.254.0.1`, TCP 443, CA-verified) | `ip -4 route get 10.254.0.1` on VM1424 and `curl --cacert <internal CA> -sS -o /dev/null -w '%{http_code}' https://docplane.charliehub.internal/openapi.json` | one host route `10.254.0.1/32 via 10.44.1.1`, installed and verified by the staging helper (same mechanism as the existing ingress-reply route), validated by the curl above returning 200 and by `--dry-run` through the wrapper; rollback `ip route del 10.254.0.1/32`. Destination is DocPlane only; no default route, no other host. |
| Operator invocation without Docker membership | `sudo -l` for `stagingadmin` | one fixed command `/usr/local/sbin/staging-schema-catalogue {dry-run|generate|observe|status}` that execs the two DocPlane wrappers with fixed settings paths `/etc/docplane/schema-catalogue.d/trevarn_staging.env` and `…-observer.d/trevarn_staging.env`, no other arguments, exit codes passed through; rollback is removing the sudoers line and the script. |
| Plaintext bridge allowlist entry | the stack's Compose project, service and network names | add `("staging", "VM1424/<stack>@<manifest>", "<stack network>")` to both wrappers and `_PLAINTEXT_BRIDGES` in the activation PR, with the manifest id matched as a pattern; until then the wrappers refuse `sslmode=disable` for staging (tested). |
| Escrow for four staging credentials | `ops/secrets-v3/registry/secrets.yaml` | four records mirroring the vps3 pair under a `vm1424` profile of the attended writer. |

Generated destination pages: `model/schema-catalogue/trevarn_staging/index.md` plus one
page per schema. Agents compare `schema_provenance trevarn` and
`schema_provenance trevarn_staging` (fingerprint, ledger rows/head/digest) to see what a
release would change; they never read `trevarn_staging` as production structure.

## Trevarn production: first-activation plan (revision 4, operator's execution copy)

**Execution blocked pending Charles's scoped approval. Bound to WORK update `a5b985d9` on initiative docplane-knowledge-reality-loop, item trevarn-production-catalogue. Nothing is merged, deployed or activated under this review.**

Revision 4 carries three corrections from the review of revision 3: gate 0 stops on a changed runner; gate 11 reconciles against the gate-2 baseline, the gate-6 release receipt and the gate-7b capture; the in-flight rollback treats an interrupted publication as incomplete until the receipts say otherwise.

Heads: docplane #228 (this PR; scripts, units and configuration as at `dcf9033f`, verify / fresh-instance success), trevarn-core #556 `bce89675` (build run 41933094023 success, nine jobs), charliehub-hub2 #1383 `f3e315a3` (18 of 18 workflows success, including secrets-v3-safety and both doc-citation-gate runs). This section is a docs-only commit on top of `dcf9033f`; no script, unit or configuration changes.
Reviewed runner identity (content, independent of merge strategy): `db/migrate.sh` at `bce89675` = sha256 `4ddf48e59778b81c322fdf964705f46182c80a07cef283884dd98f9068d06b61`; `deploy/vps3/vps3_deploy.py` at `bce89675` = sha256 `a968c6472b20da6f9080ae5cf9be625846588912dbd86de302b9129b50da61bc`.

**Prerequisite change, now on #1383 at `f3e315a3`:** the vps3 writer profile omits `CATALOGUE_LEDGER_TABLE` by default and writes it for the generator only when `SC_ENV_LEDGER_TABLE=public.schema_migrations` is given at `install`. Tests cover both forms; the procedure text matches. The structure-only canary (gate 5) is therefore the default outcome of gate 3, and the ledger switch (gate 7b) is an explicit, reversible act.

**Ordering constraints.** (a) The lock-aware runner must be the effective runner on vps3 before ledger-enabled capture: the collector's shared lock cannot coordinate with an older runner. (b) The structure-only canary precedes ledger capture, and after any release that may change schema a fresh structure-only capture precedes the ledger switch, so the ledger step is provably a ledger-only change.

| Gate | Who / where | Action and exact check | Evidence (WORK) | Rollback |
|---|---|---|---|---|
| 0 Merge | reviewers | Merge the three PRs after required checks. Then compute `git show main:db/migrate.sh \| sha256sum` in trevarn-core. If it equals the reviewed bytes `4ddf48e5…`, that value stands. If it differs, **stop**: do not accept the new value. Review the diff between the reviewed bytes and merged `main`, record the review and its approval in WORK, and only the approved value becomes the reviewed runner identity used at gates 6 and 7b. Merging deploys nothing. | merge SHAs, runner checksum | revert |
| 1 DocPlane on hub2 | hub2 operator | Protected pull of `/opt/docplane` to the merged tag. The two existing catalogues: next runs log `UNCHANGED`; provenance stays FRESH / MATCH / CURRENT; the two new `docplane_schema_catalogue_*.prom` files appear with success 1. | journal, provenance JSON, `.prom` | pull back |
| 2 Production roles + receipt | DB owner, vps3 | Provision reader then observer; run both boundary tests. **No ledger grant.** In the same authorised session collect the production receipt for the KNOW reconciliation: `SELECT max(filename), count(*) FROM public.schema_migrations` and the last deploy-ledger `end` records' `migrations_applied` (no row data, no credentials). This receipt is the **pre-release baseline** for gate 11. | psql transcripts, receipt | drop roles |
| 3 Principals and credentials | bootstrap authority; secrets owner | Scoped principals for `schema-catalogue-trevarn` (generator `GENERATE` on source `trevarn`, prefix `model/schema-catalogue/trevarn/`, plus `OBSERVE/GENERATION`; observer `OBSERVE/FRESHNESS_CHECK`). `\password` for both roles. Enrol four escrow records. On vps3: `sudo SC_ENV_PROFILE=vps3 bash sc-env.sh install generator`, then `observer`, **without** `SC_ENV_LEDGER_TABLE`. Verify: four `escrow-verify` → `match: true`; `status` shows root 0400 files and 0600 settings; `grep -c '^CATALOGUE_LEDGER_TABLE=' /etc/docplane/schema-catalogue.d/trevarn.env` → 0. Keep the four verify receipts (ids, timestamps, `match: true`) in WORK; they survive any later `remove`. | principal ids, four receipts, grep result | `sc-env.sh remove` both; revoke principals |
| 4 Install inert | vps3 operator | Read-only `/opt/docplane` at the merged tag; template units; daemon-reload; no enable, no start; `is-enabled` on the timer → `disabled`. A migrating deploy from here records `catalogue_refresh: not activated`. | unit list, `is-enabled` | remove units |
| 5 Structure-only canary | vps3 operator + owner | Confirm again that the generator settings carry no `CATALOGUE_LEDGER_TABLE` and that `--dry-run` prints no `ledger` line. One generation → `PUBLISHED`, `migration_ledger.status = NOT_CAPTURED`; one observation → `NOMINAL`; provenance FRESH / MATCH / CURRENT; second generation → `UNCHANGED`. Record fingerprint F1. | wrapper output, provenance | retire the artifact through the owning authority |
| 6 Lock-aware runner effective on vps3 | release owner | Promote a trevarn-core release whose target commit carries the reviewed runner. Record in the release receipt `git show <target>:db/migrate.sh \| sha256sum` and require it to equal the reviewed checksum. After the deploy's checkout, verify the **effective** runner by content on vps3: `sha256sum /opt/trevarn-core/db/migrate.sh` equals that value (the deploy tool copies this file into the container and executes it), and the deploy-ledger `end` record's `after_commit` equals the release target. If that release migrated, the record shows `catalogue_refresh.reason = "not activated"`. Follow-up (not blocking): extend `release_packet.py` to pin `db/migrate.sh` by content as it already pins `vps3_deploy.py`. | release receipt, checksums, ledger record | ordinary release rollback; the lock is harmless without a collector |
| 7a Fresh structure-only capture after the release | vps3 operator | One generation: `PUBLISHED` with new fingerprint F2 if the release changed schema, else `UNCHANGED` (F2 = F1). Record F2 and the DocPlane revisions of all seven schema pages. | wrapper output, page revisions | none needed |
| 7b Ledger-enabled capture | DB owner, vps3 operator, owner | Only after 6 and 7a. Apply the column-level ledger grant and its boundary test. `sc-env.sh remove generator` then `install generator` with `SC_ENV_LEDGER_TABLE=public.schema_migrations` (re-enter the two generator values; re-verify both receipts). One generation: fingerprint **equals F2**; overview republished; the seven schema-page revisions **unchanged**; projection version +1; `migration_ledger.status = CAPTURED`, `capture.consistent = true`, guard names the advisory lock. Record rows, head filename and set-digest prefix beside the release receipt's migration list, labelled truncated comparison identifiers. | provenance JSON, page revisions before/after | `remove`/`install generator` without the variable (structure-only resumes); optionally revoke the grant |
| 8 Owner acceptance | owner | Accept 5, 6, 7a, 7b in WORK. | WORK update | — |
| 9 Activate | owner, vps3 operator, hub2 operator | Execution contract → `SCHEDULED` (2 h max evidence age). `systemctl enable --now` both Trevarn timers; this one act also arms the deployment tool's refresh request. Promote the staged alert into `rules/` (hub2 PR), rules-only deploy, reload. | `is-enabled` → `enabled`, rule receipt | see rollback section |
| 10 Soak 24 h, scheduled operation only | monitoring | Both timers fire every 30 min; provenance stays FRESH; `reconcile_success{artifact="schema-catalogue-trevarn"} == 1` on hub2; no `DocPlaneGeneratedProjection*` alert for the artifact; at least one observer cycle after each generation. Deployment-triggered refresh is **not** exercised by the soak: it is covered by the unit tests of `_request_catalogue_refresh` (activated and not-activated) and the dev end-to-end; the next independently authorised production migration is observed when it occurs and its `catalogue_refresh` record and the resulting regeneration (`capture.consistent = true`) are added to WORK then. | Prometheus, journal | see rollback section |
| 11 KNOW links | docs owner | Data-model blocks on the Trevarn Database and Architecture pages point at `model/schema-catalogue/trevarn/`. Reconcile the pages' deployment claims against three records: the gate-2 receipt as the pre-release baseline; the successful gate-6 release receipt (release target, `after_commit`, migrations applied); and the gate-7b capture (rows, head filename, set-digest prefix). The pages state deployment facts in the deploy ledger's terms and must agree with all three; any disagreement is recorded in WORK before the pages change. | page revisions, the three records | revert pages |

### Rollback, including active work

- **Stop future triggers:** `systemctl disable --now docplane-schema-catalogue@trevarn.timer docplane-schema-catalogue-observer@trevarn.timer`. The deployment tool then records `not activated` again.
- **In-flight run:** do not kill a generator mid-publication. Wait for `docplane-schema-catalogue@trevarn.service` to leave `activating`/`active` (bounded by `TimeoutStartSec=5min` and the per-database lock); if it must be stopped, `systemctl stop` sends SIGTERM. An interrupted run can leave publication incomplete: the publication steps (pages+ownership, CATALOGUES, projection, GENERATION) commit separately, so a stop between them leaves the earlier steps committed and the later ones absent. After any interruption, inspect the receipts before anything else: the unit journal, `schema_provenance trevarn` (generation state, projection version) and the artifact's GENERATION evidence; record in WORK which steps committed. Then reconcile through the supported retry path only: the next run of the same unit for the same transition, whose transition-bound idempotency keys complete the missing steps without duplicating the committed ones. Idempotency aids recovery; it does not make the interrupted run atomic. Never hand-edit pages or evidence to finish a run. The shared advisory lock is session-level and is released with the connection.
- **Execution contract:** set it back to `MANUAL` through the owning authority so DocPlane does not report scheduled evidence as overdue; record the change. Leaving it `SCHEDULED` is also honest (freshness turns stale after 2 h) but noisier.
- **Published MODEL evidence:** pages, projection and GENERATION evidence stay as they are, correctly dated, with `schema_provenance` reporting staleness. If the catalogue must be withdrawn, retire the artifact through `POST /api/v1/model/artifacts/{id}/retire` (generated pages become GENERATED tombstones); never hand-edit generated pages.
- **Credentials:** `sc-env.sh remove` deletes the four files and two settings files only; the four `escrow-verify` receipts stay in WORK (ids, timestamps, `match: true`), never values; role and principal revocation go through their owners.
- **Alert:** move the rule back to `rules-staged/` and redeploy; the three label-agnostic alerts simply stop seeing the artifact once its metric file ages out.

### Separately tracked
VM1424 staging connectivity checks; the production receipt is now collected inside gate 2 so the KNOW correction at gate 11 needs no second visit.
