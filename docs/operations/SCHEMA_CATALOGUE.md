# Schema-catalogue reconciliation

The live DocPlane PostgreSQL database is the schema-catalogue source authority.
`scripts/schema_catalogue.py` reads structure in a read-only transaction and is
the sole writer of its generated DocPlane projection. Rendered catalogue pages
are evidence, never an alternate schema authority.

## Execution paths and authority boundary

Schema generation remains attended and manual. Its supported host entrypoint is:

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
`REFERENCES` is required because PostgreSQL's `information_schema.columns`
filters metadata by table privilege; the migration owner's default privilege
keeps that visibility complete for later tables. `search_path=docs` preserves
the generator's existing `pg_get_constraintdef()` and default-expression text,
and therefore the established fingerprint. Do not change the generator search
path as part of observer deployment: that is a separately governed projection-
contract migration.

Before any production role is created, repeat the disposable integration proof
from `docs/architecture/SCHEMA_OBSERVER_PRIVILEGE_PROOF.md`: owner and observer
structures and fingerprints must be identical, all catalogued tables must have
non-empty column projections, parity must survive a newly migrated table, and
observer row reads and writes must be refused.

## Inert units and later gates

The repository ships `config/systemd/docplane-schema-catalogue-observer.service`
and `.timer`. Repository presence is not installation or activation. The timer
declares a 30-minute opportunity, is non-persistent, and has no effect until a
later authorised deployment installs the files and an even later activation
gate enables it.

The governed order remains: create the dedicated principals/runtime contract;
run one attended canary; only then declare the MODEL execution contract
(`SCHEDULED`, two-hour maximum evidence age, `schema-catalogue` exclusion);
install units disabled/non-triggering; separately enable recurring execution;
then complete the required 24-hour soak. None of those acts is performed by
the implementation PR.
