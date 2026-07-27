"""MCP server exposing the DocPlane contributor API."""
from __future__ import annotations

import hmac
import os
import sys

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route

from allowed_hosts import default_allowed_hosts, default_allowed_origins, port_from_spec
from common import DOCPLANE_API_URL, DOCPLANE_TOKEN
from tools import docs as docs_tools

HOST = os.environ.get("MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MCP_PORT", "8049"))
MCP_API_KEY = os.environ.get("MCP_API_KEY", "")
SERVER_NAME = os.environ.get("MCP_SERVER_NAME", "docplane")

# The port a client actually addresses. Inside a container the listening port
# (MCP_PORT) and the published port differ, and the Host header carries the
# PUBLISHED one, so the container has to be told. Compose passes
# MCP_PUBLIC_PORT=${DOCPLANE_MCP_PORT}, which may carry a bind address
# ("127.0.0.1:18049") because that is how loopback-only publication is
# expressed — hence port_from_spec rather than a bare int().
PUBLIC_PORT = port_from_spec(os.environ.get("MCP_PUBLIC_PORT"), PORT)


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


# An operator-supplied allowlist always wins; the default only fills a gap.
ALLOWED_HOSTS = _csv_env("MCP_ALLOWED_HOSTS") or default_allowed_hosts(PORT, PUBLIC_PORT)
ALLOWED_ORIGINS = _csv_env("MCP_ALLOWED_ORIGINS") or default_allowed_origins(PORT, PUBLIC_PORT)

mcp = FastMCP(
    SERVER_NAME,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=ALLOWED_HOSTS,
        allowed_origins=ALLOWED_ORIGINS,
    ),
)
docs_tools.register(mcp)


class BearerAuth:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/healthz":
            await self.app(scope, receive, send)
            return
        if not MCP_API_KEY:
            await self._reject(send, 500, "MCP_API_KEY is not configured")
            return
        headers = dict(scope.get("headers") or [])
        authorization = headers.get(b"authorization", b"").decode("latin-1", errors="replace")
        if not hmac.compare_digest(authorization, f"Bearer {MCP_API_KEY}"):
            await self._reject(send, 401, "unauthorized")
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status: int, message: str) -> None:
        body = message.encode()
        headers = [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())]
        if status == 401:
            headers.append((b"www-authenticate", b'Bearer realm="docplane-mcp"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


async def healthz(_request):
    return JSONResponse(
        {
            "status": "ok" if DOCPLANE_TOKEN else "degraded",
            "server": SERVER_NAME,
            "backend": DOCPLANE_API_URL,
            "contributor_token_configured": bool(DOCPLANE_TOKEN),
        }
    )


def main() -> None:
    if not MCP_API_KEY:
        print("MCP_API_KEY is not configured; MCP requests will fail", file=sys.stderr)
    if not DOCPLANE_TOKEN:
        print("DOCPLANE_TOKEN is not configured; DocPlane tools will fail", file=sys.stderr)
    app = mcp.streamable_http_app()
    app.router.routes.append(Route("/healthz", healthz))
    app.add_middleware(BearerAuth)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
