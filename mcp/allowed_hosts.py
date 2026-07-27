"""Port-aware defaults for MCP host/origin validation.

Deliberately dependency-free so it can be unit tested without importing the MCP
runtime (uvicorn, fastmcp). server.py is the only caller.

BACKGROUND: TransportSecuritySettings validates the Host header by EXACT string
match, apart from an explicit ``host:*`` wildcard. A client connecting to
http://127.0.0.1:18049 sends ``Host: 127.0.0.1:18049``, so an allowlist of bare
loopback names rejects every non-default port with 421 Invalid Host header.
Defaults must therefore follow the configured port.
"""
from __future__ import annotations

LOOPBACK_NAMES = ("localhost", "127.0.0.1")


def port_from_spec(value: str | None, fallback: int) -> int:
    """Extract a port from a Docker published-port spec.

    DOCPLANE_MCP_PORT is interpolated straight into the compose ``ports:``
    entry, so it is NOT always a bare port. Restricting publication to loopback
    is expressed by putting the bind address in the same variable:

        DOCPLANE_MCP_PORT=8049                 -> 8049
        DOCPLANE_MCP_PORT=127.0.0.1:18049      -> 18049   (loopback-only)
        DOCPLANE_MCP_PORT=0.0.0.0:18049        -> 18049

    Naively int()-ing the raw value crashes the server at import on every
    loopback-restricted deployment, so take the last colon-separated field and
    fall back rather than raise on anything unparseable.
    """
    if not value:
        return fallback
    candidate = value.strip().rsplit(":", 1)[-1].strip()
    return int(candidate) if candidate.isdigit() else fallback


def _ports(public_port: int, listen_port: int) -> list[int]:
    # dict.fromkeys de-duplicates while preserving order; public first because
    # that is what host-side clients actually send.
    return list(dict.fromkeys((public_port, listen_port)))


def default_allowed_hosts(listen_port: int, public_port: int) -> list[str]:
    """Loopback names, bare and qualified with every port we answer on.

    Both ports are included: the published one for clients on the host, and the
    listening one for peers on the container network. This stays an explicit
    allowlist - no wildcard, and non-loopback hosts remain rejected.
    """
    hosts = list(LOOPBACK_NAMES)
    for port in _ports(public_port, listen_port):
        hosts.extend(f"{name}:{port}" for name in LOOPBACK_NAMES)
    return hosts


def default_allowed_origins(listen_port: int, public_port: int) -> list[str]:
    return [
        f"http://{name}:{port}"
        for port in _ports(public_port, listen_port)
        for name in LOOPBACK_NAMES
    ]
