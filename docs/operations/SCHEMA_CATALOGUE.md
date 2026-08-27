# Schema-catalogue reconciliation

The live DocPlane PostgreSQL database is the schema-catalogue source authority.
`scripts/schema_catalogue.py` reads structure in a read-only transaction and is
the sole writer of its generated DocPlane projection. Rendered catalogue pages
are evidence, never an alternate schema authority.

## Canonical attended execution

Schema reconciliation is currently attended only. There is no timer, cron,
source observer or execution contract. The one supported host entrypoint is:

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
