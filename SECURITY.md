# Security Notes

Read this before exposing anything beyond localhost.

## Secrets

Three secrets live in `.env` (never commit it — `.gitignore` already excludes it):

| Variable | Used by | Purpose |
|----------|---------|---------|
| `POSTGRES_PASSWORD` | postgres, docs-api | DB auth (postgres is not exposed on any host port) |
| `DOCS_API_KEY` | docs-api, docs-mcp, your scripts | `X-API-Key` header for every `/api/docs/*` endpoint |
| `MCP_API_KEY` | docs-mcp, your MCP clients | `Authorization: Bearer` for the MCP server, and the token docs-api uses to call `/reindex` |

Generate them with `openssl rand -hex 32`. Rotating = edit `.env`,
`docker compose up -d` (containers pick up the new values), update clients.

## `/api/agent-config`

docs-api has a bootstrap endpoint that **returns the API key** to callers so
that machines inside a trusted network can self-configure (IMDS-style). On the
upstream deployment it is anonymous because a network boundary (internal-only
reverse-proxy middleware) does the authentication.

**This package ships with `DOCS_AGENT_CONFIG_REQUIRE_KEY=true`** in
`docker-compose.yml`, which makes the endpoint require the key like everything
else. Only relax that if you have an equivalent network boundary in front of
the API and you understand the trade-off.

## Network exposure

**The recommendation is internal-only.** Run the whole stack on your LAN or
VPN (WireGuard/Tailscale) and don't publish it to the internet. The upstream
deployment this package comes from runs exactly that way — the docs site,
API, and MCP are reachable only from the private network, and that network
boundary is a load-bearing part of the security model. A docs corpus tends to
accumulate infrastructure detail (hostnames, topology, runbooks) that is
individually harmless and collectively a site map for an attacker — treat the
*site* as sensitive even though no single page is.

- Every published port in `docker-compose.yml` binds to `127.0.0.1`. Nothing
  is reachable from other hosts until *you* put a reverse proxy in front.
- Recommended: terminate TLS at your proxy (Caddy/Traefik/nginx + an internal
  CA or Let's Encrypt DNS-01) on the private network, and only forward:
  - the **docs site** (`:8080`) — add your own auth (SSO, basic auth, IP
    allow-list) if the content is not public;
  - optionally the **MCP server** (`:8049`) — it has bearer auth, but put TLS
    on it; the token travels in a header;
  - the **API** (`:8010`) only if remote machines need to write docs. It is
    key-authed, but it is also the write path — prefer keeping it
    LAN/VPN-only.
- The raw-markdown endpoint (`/raw/` on the docs site) intentionally sends
  **no** CORS headers: same-origin readers don't need them, and a wildcard
  would let arbitrary websites read your docs through a visitor's browser.

## Container hardening (already applied)

- docs-api: runs as uid 1000 (non-root), `cap_drop: ALL`,
  `no-new-privileges`, writable mounts limited to the content/site dirs.
- docs-mcp: non-root user, `cap_drop: ALL`, `no-new-privileges`, read-only
  mount of the site, DNS-rebinding protection (Host/Origin allow-lists —
  extend `MCP_ALLOWED_HOSTS`/`MCP_ALLOWED_ORIGINS` when you put a hostname in
  front of it).
- postgres: no published port; only reachable on the compose network.
- nginx: read-only mounts, `no-new-privileges`, `X-Content-Type-Options: nosniff`.

## Content is not a secret store

Pages are stored unencrypted, kept forever in `docs.page_versions`, rendered
to disk, indexed for search, and downloadable via `/raw/`. **Never put
credentials in a docs page.** Reference secret managers instead. (Archiving a
page does not purge its history — that's a feature.)

## Backups

`pg_dump` output contains your entire docs corpus and its full history.
Protect backup files accordingly.
