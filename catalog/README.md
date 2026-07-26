# DocPlane generated schema catalog

The schema catalog is the machine-oriented half of DocPlane. It records deterministic,
read-only observations of databases without turning generated output into authored pages.

## Authority model

- Authored documentation remains in the Docs API page/version/certification model.
- Schema snapshots live under a separate catalog lifecycle.
- A successful collection creates an immutable snapshot identified by a SHA-256 fingerprint.
- A failed collection records failure and staleness; it never relabels an old snapshot as current.
- Database credentials and DSNs are runtime inputs and are never persisted in snapshots.

## Snapshot layout

```text
$DOCPLANE_CATALOG_DATA/
  <source>/
    latest.json
    status.json
    snapshots/
      <fingerprint>/
        manifest.json
        schema.json
        docs/
          README.md
          ...
```

`schema.json` is produced by `tbls out -t json`. `docs/` is produced by `tbls doc` and is
provided for detailed inspection; agents should prefer the structured catalog API.

## Source registry

Copy `sources.example.yml` and register each data source. The DSN is named indirectly through
`dsnEnv`; the registry never contains a password or connection string.

```yaml
sources:
  - name: docplane
    service: docplane-api
    owner: platform
    dsnEnv: DOCPLANE_CATALOG_DSN_DOCPLANE
    include:
      - docs.*
      - catalog.*
    exclude:
      - public.schema_migrations
```

The database role should be metadata-only. For PostgreSQL, grant only enough access to inspect
schemas, tables, columns, constraints, indexes, views and comments. Do not grant table-data
read access merely to make catalog collection convenient.

## Collecting

With `tbls` installed:

```bash
export DOCPLANE_CATALOG_DSN_DOCPLANE='postgres://catalog_reader:...@postgres:5432/docplane'
python -m catalog.collector \
  --registry catalog/sources.yml \
  --source docplane \
  --data-dir /var/lib/docplane/catalog
```

The collector:

1. resolves the selected source and DSN environment variable;
2. runs `tbls out -t json` and `tbls doc --rm-dist` in an isolated temporary directory;
3. rejects output containing the live DSN;
4. canonicalizes the structured schema and calculates its fingerprint;
5. installs an immutable snapshot;
6. atomically updates `latest.json` and `status.json`.

## API

Run the catalog API with:

```bash
DOCPLANE_CATALOG_DATA=/var/lib/docplane/catalog \
DOCPLANE_CATALOG_API_KEY=change-me \
uvicorn catalog.api:app --host 0.0.0.0 --port 8050
```

Initial endpoints:

- `GET /healthz`
- `GET /api/catalog/sources`
- `GET /api/catalog/sources/{source}/latest`
- `GET /api/catalog/sources/{source}/schema`
- `GET /api/catalog/search?q=...`

Except for health, endpoints require `X-API-Key`. MCP-specific tools will be layered over these
stable HTTP surfaces in a later PR.