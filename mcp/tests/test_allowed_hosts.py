"""Regression cover for port-aware MCP host/origin defaults.

A deployment published MCP on 18049 while the shipped default allowlist assumed the
upstream 8049, so every normal client got 421 Invalid Host header and had to
send a hand-forged ``Host: 127.0.0.1``. These tests pin the fix: defaults must
follow the configured port, without weakening host validation.

The end-to-end proof that a real client succeeds on a non-default published
port lives in scripts/integration-fresh-install.sh, which runs the whole stack
on 18149. These unit tests cover the derivation itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from allowed_hosts import (  # noqa: E402
    default_allowed_hosts,
    default_allowed_origins,
    port_from_spec,
)


def test_default_port_allows_bare_and_qualified_loopback():
    hosts = default_allowed_hosts(8049, 8049)
    assert "localhost" in hosts and "127.0.0.1" in hosts
    assert "localhost:8049" in hosts and "127.0.0.1:8049" in hosts


def test_default_port_is_not_duplicated_when_listen_equals_public():
    hosts = default_allowed_hosts(8049, 8049)
    assert len(hosts) == len(set(hosts)), hosts


def test_non_default_published_port_is_allowed():
    """The exact production failure: published 18049, listening 8049."""
    hosts = default_allowed_hosts(8049, 18049)
    assert "127.0.0.1:18049" in hosts, "published port missing - this is the 421 defect"
    assert "localhost:18049" in hosts
    # The container-network port must still work for in-cluster peers.
    assert "127.0.0.1:8049" in hosts


def test_allowlist_stays_explicit_and_loopback_only():
    hosts = default_allowed_hosts(8049, 18049)
    assert "*" not in hosts
    assert not any(h.endswith(":*") for h in hosts)
    for host in hosts:
        assert host.split(":")[0] in ("localhost", "127.0.0.1"), host


def test_origins_follow_the_published_port():
    origins = default_allowed_origins(8049, 18049)
    assert "http://127.0.0.1:18049" in origins
    assert "http://localhost:18049" in origins
    assert all(o.startswith("http://") for o in origins)


def test_published_port_is_offered_before_listen_port():
    """Host-side clients are the common case; keep their entries first."""
    hosts = default_allowed_hosts(8049, 18049)
    assert hosts.index("127.0.0.1:18049") < hosts.index("127.0.0.1:8049")


# --- port_from_spec -------------------------------------------------------
# DOCPLANE_MCP_PORT is interpolated into the compose `ports:` entry, so it may
# carry a bind address. a fabric host publishes loopback-only as "127.0.0.1:18049";
# int()-ing that raw value crash-looped docs-mcp at import on the very first
# deployment of the port-aware default.

def test_bare_port_spec():
    assert port_from_spec("8049", 1) == 8049


def test_loopback_restricted_spec_is_the_production_shape():
    assert port_from_spec("127.0.0.1:18049", 1) == 18049


def test_wildcard_bind_spec():
    assert port_from_spec("0.0.0.0:18049", 1) == 18049


def test_unset_or_unparseable_falls_back_rather_than_raising():
    for value in (None, "", "   ", "not-a-port", "127.0.0.1:", "a:b:c"):
        assert port_from_spec(value, 8049) == 8049, value
