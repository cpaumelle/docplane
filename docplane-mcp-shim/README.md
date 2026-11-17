# docplane-mcp-shim

A single-file, dependency-free MCP server (stdio, JSON-RPC 2.0) that gives an
MCP client **read access** to a DocPlane deployment: documentation pages and
WORK initiatives. Python 3 standard library only.

It is meant for clients that should be able to read DocPlane but must not get a
broader agent tool surface, for example a desktop assistant or a hosted agent
connected through an MCP tunnel.

## Security boundary

**The read-only property is structural, not a property of the credential.**

* The shim obtains its DocPlane token by self-service issuance
  (`POST /api/v1/auth/self-issue`). That works only on deployments whose access
  profile enables it, and only through the deployment's trusted front (see
  `docs/architecture/authentication-profiles.md`). The issued token is a
  short-lived **CONTRIBUTOR** token: DocPlane would accept writes made with it.
* The shim never makes those writes because it implements only fixed `GET`
  routes. No caller-supplied path, URL, HTTP method or header is ever forwarded,
  and every path segment is built from a validated identifier.
* Reviewing the shim's safety therefore means reviewing **its tool list and
  route construction**, not its token. Adding any tool is a security change.
* The token is requested lazily, held only in memory, never written to disk,
  and re-requested once on a 401.
* Each process start creates a new DocPlane principal. Principal records
  outlive their tokens, so long-running deployments accumulate inert principals
  and should plan their own cleanup.

Deployments that cannot accept a write-capable token in the shim process should
not use self-issuance. Use a narrower credential mechanism instead.

## Tools

| Tool | DocPlane route(s) | Purpose |
|---|---|---|
| `search` | `GET /api/v1/search` | Full-text search; returns canonical paths and revisions |
| `list` | `GET /api/v1/pages` | Browse pages; `path_prefix` is filtered client-side and reports counts |
| `read` | `GET /api/v1/pages/{id}` | Full Markdown; oversized pages degrade to an outline, then top headings, never silently truncated |
| `outline` | `GET /api/v1/pages/{id}?view=outline` | Heading structure without the body |
| `section` | `GET /api/v1/pages/{id}/sections/{heading}` | One heading-bounded section |
| `work_brief` | `GET /api/v1/initiatives/{id}/brief` | WORK brief (`work-brief-v1`) for one initiative |
| `work_history` | `GET /api/v1/initiatives/{id}?activity_bodies=redacted` | Redacted activity history with links and dependencies, paginated |

Page paths resolve through `GET /api/v1/resolve`. An initiative is named by a
lowercase canonical UUID or an exact `initiative_key`, resolved with
`GET /api/v1/initiatives?key=…`; a near match is refused.

### WORK reads

The two WORK tools read **all** initiatives that the token can read; there is no
initiative allowlist. They add no write.

* `work_brief` returns DocPlane's brief, but removes `context_token`. That token
  binds a WORK update to the brief it was based on, and this server has no update
  tool. An oversized brief drops `recent_activity` bodies and says so.
* `work_history` refuses any response that lacks DocPlane's
  `activity_bodies: "redacted"` and `activity_metadata: "allowlisted-v1"` stamps,
  so it never returns raw activity bodies. Activities are ordered oldest first.
  `pagination` reports `total`, `returned`, `next_offset`, `has_more` and
  `budget_truncated`. To stay inside the response budget the page shrinks; a
  body is never cut. DocPlane returns the whole history on each call, so paging
  is client-side.
* Recorded WORK evidence is a claim, not live verification.

## Configuration

| Variable | Required | Meaning |
|---|---|---|
| `DOCPLANE_BASE_URL` | yes | The deployment's trusted front, e.g. `https://docplane.example.org` |
| `DOCPLANE_CA_BUNDLE` | if private CA | PEM bundle for TLS; otherwise system trust is used |
| `DOCPLANE_AUDIT_LOG` | no | Local append-only audit log (default `./audit/docplane-mcp.log`) |
| `DOCPLANE_MAX_RESPONSE_BYTES` | no | Per-response budget, default 40960 |
| `DOCPLANE_HTTP_TIMEOUT` | no | Seconds, default 60 |

Run it as a stdio child of the MCP client, with a clean environment that
contains only these variables. stdout carries protocol frames only; diagnostics
go to stderr and the audit log. The audit log records timestamp, tool,
arguments, HTTP status, bytes, principal and revision; it never records tokens.

## Tests

```
python -m pytest docplane-mcp-shim/tests
```

## Provenance

This is a cleaned, generic import of an adapter first maintained privately for
one deployment. Deployment-specific files and history are kept by that
deployment and are not part of this repository.
