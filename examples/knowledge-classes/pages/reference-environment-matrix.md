# Environment matrix

Stable facts about the environments this installation runs. Look values
up here; the page is not meant to be read top to bottom.

## Environments

| Environment | Purpose                  | Base URL                        | Data       |
|-------------|--------------------------|---------------------------------|------------|
| local       | development compose      | `http://localhost:8080`         | disposable |
| staging     | pre-release verification | `https://docs-stg.example.test` | synthetic  |
| production  | the real corpus          | `https://docs.example.test`     | durable    |

## Ports (compose defaults)

| Service   | Port |
|-----------|------|
| docs-api  | 8010 |
| dashboard | 8051 |
| docs-web  | 8080 |
| docs-mcp  | 8049 |

## Retention

| Data                | Where              | Kept for            |
|---------------------|--------------------|---------------------|
| page versions       | `docs.page_versions` | forever (authored history) |
| deployment attempts | docs-api           | forever (audit)     |
| database backups    | operator-owned     | per your backup policy |

Update this page through the normal authoring flow whenever an
environment fact changes; it is the kind of page that rots silently if
treated as prose.
