#!/usr/bin/env python3
"""docs-mcp — MCP server fronting the self-hosted docs stack.

Exposes the documentation site to MCP clients (Claude Code, Claude Desktop, …):
  search_docs, read_doc, list_docs, write_doc, archive_doc, resolve_concept

Transport: streamable HTTP at POST /mcp.
Auth: Bearer token via the MCP_API_KEY env var.
Also serves GET /healthz (unauthenticated) and POST /reindex (the webhook
docs-api fires after each deploy so the search index follows content changes).
"""
from __future__ import annotations

import hmac
import os
import sys

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route

from tools import docs as docs_mod

# --- Config ---
HOST = os.environ.get("MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MCP_PORT", "8049"))
BEARER_TOKEN = os.environ.get("MCP_API_KEY", "")
SERVER_NAME = os.environ.get("MCP_SERVER_NAME", "docs")

_allowed_hosts = [
    h.strip() for h in
    os.environ.get("MCP_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]
_allowed_origins = [
    h.strip() for h in
    os.environ.get("MCP_ALLOWED_ORIGINS", "http://127.0.0.1:8049").split(",")
    if h.strip()
]

mcp = FastMCP(
    SERVER_NAME,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
        allowed_origins=_allowed_origins,
    ),
)

docs_mod.register(mcp)


# --- Auth middleware (pure ASGI — BaseHTTPMiddleware breaks SSE streaming) ---

class BearerAuth:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path", "") == "/healthz":
            await self.app(scope, receive, send)
            return
        if not BEARER_TOKEN:
            await self._reject(send, 500, "server misconfigured: no MCP_API_KEY")
            return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1", errors="replace")
        # Constant-time compare — a plain != leaks the token via timing.
        if not hmac.compare_digest(auth, f"Bearer {BEARER_TOKEN}"):
            await self._reject(send, 401, "unauthorized")
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status: int, body: str) -> None:
        b = body.encode()
        headers = [(b"content-type", b"text/plain; charset=utf-8"),
                   (b"content-length", str(len(b)).encode())]
        if status == 401:
            headers.append((b"www-authenticate", b'Bearer realm="docs-mcp"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": b})


# --- Health endpoint ---

async def healthz(_request):
    from common import DOCS_API_URL
    return JSONResponse({
        "status": "ok",
        "server": SERVER_NAME,
        "transport": "streamable-http",
        "backends": {"docs_api": DOCS_API_URL},
    })


def main() -> None:
    if not BEARER_TOKEN:
        print("[docs-mcp] WARNING: MCP_API_KEY not set — all requests will fail",
              file=sys.stderr)
    # Build search index (tolerates a not-yet-built site), start refresher thread
    docs_mod.init()

    app = mcp.streamable_http_app()
    app.router.routes.append(Route("/healthz", healthz))

    # Webhook endpoint — docs-api POSTs here after each deploy
    docs_mod.register_routes(app)

    app.add_middleware(BearerAuth)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
