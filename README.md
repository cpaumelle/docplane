# Self-Hosted Docs Stack

A database-backed documentation control plane you can run on your own infra.
This is the generalized, content-free version of the stack that runs
`docs.charliehub.net` — shared so you can stand up the same service with your
own PostgreSQL, Python and web server.

**What you get:**

- **docs-api** — a FastAPI service that stores every documentation page in
  PostgreSQL (single source of truth), with optimistic locking, full version
  history, rollback, soft-delete/restore, page moves with automatic redirects,
  nav-tree management, lint rules, drift detection and Prometheus metrics.
- **MkDocs + nginx** — the API renders pages to disk, builds a
  [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) site in a
  staging dir and rsyncs it atomically into the directory nginx serves (no
  404-window during rebuilds). Raw markdown is also served under `/raw/` so
  agents can fetch page sources directly.
- **docs-mcp** — an MCP (Model Context Protocol) server that fronts the docs
  site so AI agents (Claude Code, Claude Desktop, anything MCP-capable) can
  search, read, write and archive documentation pages as first-class tools.
- **claude-md** — the pattern (and a ready-to-use sync script) for composing
  each machine's `CLAUDE.md` agent-context file from pages stored in the docs
  database, so agent context is versioned, auditable and centrally managed.

**What is deliberately NOT here:** any documentation content, secrets, keys,
hostnames or site-specific configuration. You bring your own.

---

## Architecture

```
Agents / scripts / humans / MCP clients
        │  PUT /api/docs/pages/{path}          ┌───────────────┐
        ▼                                      │   docs-mcp    │
   ┌──────────┐   source of truth              │  (MCP server) │
   │ docs-api │ ◄──────────────► PostgreSQL    └──────┬────────┘
   │ (FastAPI)│                  (docs schema)        │ search index +
   └────┬─────┘                                       │ raw markdown (ro)
        │ deploy (auto after every write, coalesced)  │
        ▼                                             │
   mkdocs/docs/*.md  +  mkdocs.nav.yml (generated nav)│
        ▼                                             │
   mkdocs build → staging → rsync (atomic per file)   │
        ▼                                             │
   mkdocs/site/  ◄────────────────────────────────────┘
        ▼
   nginx (docs-web) → your reverse proxy → readers
```

Key invariants (the whole design hangs off these):

1. **PostgreSQL is the only source of truth.** The markdown files on disk are
   build artifacts, regenerated on every deploy. Never edit them directly —
   the API detects the drift and overwrites it.
2. **All writes go through the API.** That's what gives you version history,
   optimistic locking (`If-Match`), validation and audit labels for free.
3. **The generated nav lives in `mkdocs.nav.yml`** (git-ignored, rewritten
   every deploy), which `INHERIT`s the hand-maintained `mkdocs/mkdocs.yml`.
   You edit the theme/plugins; the API owns the nav and the redirect map.

## Prerequisites

- Docker with Compose v2
- ~1 GB disk for images + your content
- `curl`, `jq`, `openssl` on the host for the helper scripts

## Quickstart

```bash
# 1. Configure secrets (two API keys + a DB password)
cp .env.example .env
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
sed -i "s/^DOCS_API_KEY=.*/DOCS_API_KEY=$(openssl rand -hex 32)/" .env
sed -i "s/^MCP_API_KEY=.*/MCP_API_KEY=$(openssl rand -hex 32)/" .env

# 2. Create the working dirs the containers mount (docs-api runs as uid 1000
#    and writes rendered markdown + the built site here; adjust the uid in
#    docker-compose.yml if your host user differs)
mkdir -p mkdocs/docs mkdocs/site
sudo chown -R 1000:1000 mkdocs

# 3. Build and start
docker compose up -d --build

# 4. Seed a Home page + example pages, trigger the first deploy
./scripts/seed-example-docs.sh

# 5. Browse
open http://localhost:8080          # the docs site
open http://localhost:8010/docs     # docs-api OpenAPI UI
```

> **Recommended deployment: internal-only.** This stack is designed to run on
> your LAN or VPN (WireGuard/Tailscale), not as a public internet site — that's
> how the upstream deployment runs it. All ports bind to `127.0.0.1`; to serve
> beyond localhost, put a reverse proxy (Caddy, Traefik, nginx) in front with
> TLS **and keep it reachable only from your private network**. If you truly
> need it public, read [SECURITY.md](SECURITY.md) first and put SSO or another
> auth layer in front of the site — the docs pages themselves have no auth.

## Writing pages

Everything is a `PUT` of `{title, nav_path, content}` to a path:

```bash
source .env

# Create
curl -sf -X PUT http://localhost:8010/api/docs/pages/guides/backups.md \
  -H "X-API-Key: $DOCS_API_KEY" -H "Content-Type: application/json" \
  -d '{"title":"Backups","nav_path":"Guides/Backups","content":"# Backups\n\nHow we back things up."}'

# Update (optimistic lock: read the revision, send it back as If-Match;
# or use If-Match: * to explicitly take last-writer-wins)
REV=$(curl -sf -H "X-API-Key: $DOCS_API_KEY" \
  http://localhost:8010/api/docs/pages/guides/backups.md | jq -r .revision)
curl -sf -X PUT http://localhost:8010/api/docs/pages/guides/backups.md \
  -H "X-API-Key: $DOCS_API_KEY" -H "If-Match: \"$REV\"" \
  -H "Content-Type: application/json" \
  -d '{"title":"Backups","nav_path":"Guides/Backups","content":"# Backups\n\nUpdated."}'
```

- `path` is the page's URL identity (`guides/backups.md` →
  `/guides/backups/`); lowercase `[a-z0-9/_-]` plus `.md`.
- `nav_path` is where it sits in the sidebar (`Guides/Backups`). Path and nav
  are decoupled on purpose; the API warns (and in clear cases blocks) writes
  that look like the section was mechanically derived from the URL.
- Every write auto-triggers a coalesced background deploy (~seconds).

Useful endpoints (all under `http://localhost:8010`, all `X-API-Key`-authed):

| Endpoint | Purpose |
|----------|---------|
| `GET /api/docs/pages` | List pages (`?status=active\|archived\|all`) |
| `GET /api/docs/pages/{path}` | Read one page (+ `ETag` revision) |
| `PUT /api/docs/pages/{path}` | Create / update |
| `POST /api/docs/pages/{path}/archive` | Soft-delete (recoverable) |
| `POST /api/docs/pages/{path}/restore` | Restore an archived page |
| `GET /api/docs/pages/{path}/history` | Version history |
| `POST /api/docs/pages/{path}/rollback/{revision}` | Roll back |
| `POST /api/docs/pages/move` | Relocate a page's URL, with auto-redirect |
| `GET /api/docs/nav` | The rendered nav tree + conflict report |
| `POST /api/docs/nav/reparent` | Bulk-move a sidebar subtree |
| `PUT /api/docs/nav/sections` | Order the top-level sections |
| `POST /api/docs/deploy` | Force a rebuild (`?dry_run=true` to preview) |
| `GET /api/docs/drift` | DB-vs-disk drift report |
| `GET /api/docs/lint` | Content lint findings |
| `GET /api/docs/search?q=…` | Full-text search |
| `GET /metrics` | Prometheus metrics (drift, nav divergence) |

Explore the rest at `http://localhost:8010/docs` (OpenAPI UI), and read
**[API_GUIDE.md](API_GUIDE.md)** for the full write-path semantics: `If-Match`
modes, the nav-section guard, move/redirect mechanics, the document lifecycle
model, and rendering gotchas. The seed script publishes that guide into your
own site as `guides/docs-api.md`, so your docs are self-documenting.

## Connecting an AI agent (MCP)

`docs-mcp` exposes the docs as MCP tools over streamable HTTP at
`http://localhost:8049/mcp`, bearer-token authed:

- `search_docs(query)` — ranked keyword search (Lunr index built from the site)
- `read_doc(path_or_slug)` — full markdown source of a page
- `list_docs()` / `write_doc(...)` / `archive_doc(path)` — full write path
- `resolve_concept(term)` — your project's jargon → canonical concept + doc
  links (edit `mcp/aliases.yml`, hot-reloaded)

Claude Code:

```bash
source .env
claude mcp add docs --transport http http://localhost:8049/mcp \
  --header "Authorization: Bearer $MCP_API_KEY"
```

After every deploy, docs-api fires a webhook at `docs-mcp:/reindex` so the
search index follows content changes within seconds.

## The CLAUDE.md pattern

If you run coding agents on several machines, keeping each machine's
`CLAUDE.md` (or `AGENTS.md`) current is exactly the kind of state that rots.
The pattern we use: store the shared context and each machine's node-specific
context as ordinary pages in the docs database, and let a small timer-driven
script on each machine compose and install the file read-only. See
[claude-md/README.md](claude-md/README.md) — the sync script and systemd units
are included.

## Operating it

- **Backups:** the database is everything. `docker compose exec postgres
  pg_dump -U docs docs > backup.sql` on a cron; the site rebuilds from it.
- **Drift:** `GET /api/docs/drift` tells you if anyone edited rendered files
  behind the API's back (they get overwritten on next deploy anyway).
- **Monitoring:** scrape `docs-api:/metrics` with Prometheus if you have one.
- **Upgrades:** bump pins in `docs-api/requirements.txt` (keep
  `mkdocs-material` in lockstep with anything else that pins it), rebuild.

## Customizing

- **Theme / plugins:** edit `mkdocs/mkdocs.yml` (hand-maintained; the nav and
  `redirect_maps` block are API-owned — leave those alone).
- **Section order:** `PUT /api/docs/nav/sections` with your ordered list, or
  edit the `DEFAULT_SECTION_ORDER` bootstrap in `docs-api/app/generator.py`.
- **Lint rules:** `docs-api/app/lint_rules.py` ships with *our* rules as
  worked examples (stale CIDRs, retired container names…). Replace them with
  checks that encode **your** infra's past incidents — that's the point of
  the file.
- **Jargon resolver:** copy `mcp/aliases.example.yml` to `mcp/aliases.yml`
  and add your team's vocabulary.

## Repository layout

```
docker-compose.yml     postgres + docs-api + docs-web (nginx) + docs-mcp
.env.example           the three secrets + public URLs
db/init.sql            schema (docs.pages, docs.page_versions, docs.sections)
docs-api/              the FastAPI control plane (copied from upstream, unmodified)
mkdocs/                mkdocs.yml template, build hooks, nginx config, theme overrides
mcp/                   the MCP server (docs tools only)
claude-md/             CLAUDE.md distribution pattern + sync script + systemd units
scripts/               seed-example-docs.sh, sync-from-upstream.sh
```

## Using this as a standalone repo

This directory is self-contained. To lift it out:

```bash
cp -r docs-stack-selfhost ~/my-docs-stack && cd ~/my-docs-stack && git init
# or, preserving history from the parent repo:
git subtree split -P docs-stack-selfhost -b docs-stack && git push <newremote> docs-stack:main
```

If you received this directory from the upstream repo, `scripts/sync-from-upstream.sh`
refreshes the `docs-api/` copy from the canonical sources.
