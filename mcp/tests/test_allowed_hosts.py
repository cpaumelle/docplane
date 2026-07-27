"""Regression cover for port-aware MCP host/origin defaults.

hub2 published MCP on 18049 while the shipped default allowlist assumed the
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

from allowed_hosts import default_allowed_hosts, default_allowed_origins  # noqa: E402


def test_default_port_allows_bare_and_qualified_loopback():
    hosts = default_allowed_hosts(8049, 8049)
    assert "localhost" in hosts and "127.0.0.1" in hosts
    assert "localhost:8049" in hosts and "127.0.0.1:8049" in hosts


def test_default_port_is_not_duplicated_when_listen_equals_public():
    hosts = default_allowed_hosts(8049, 8049)
    assert len(hosts) == len(set(hosts)), hosts


def test_non_default_published_port_is_allowed():
    """The exact hub2 failure: published 18049, listening 8049."""
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
