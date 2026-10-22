"""Docker allocation readback: no race-prone free-port probing or runner cleanup."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/lib/integration-endpoint.sh"


@pytest.mark.parametrize("mapping,rc,expected", [
    ("127.0.0.1:32781", 0, "http://127.0.0.1:32781"),
    ("127.0.0.1:18110", 0, "http://127.0.0.1:18110"),
    ("127.0.0.1:32781", 17, None),  # healthy-looking output from failed Docker
    ("0.0.0.0:32781", 0, None),
    ("[::]:32781", 0, None),
    ("127.0.0.1:0", 0, None),
    ("127.0.0.1:65536", 0, None),
    ("127.0.0.1:32781\n127.0.0.1:32782", 0, None),
    ("", 0, None),
])
def test_allocated_endpoint_is_verified(mapping, rc, expected):
    result = subprocess.run(["bash", "-c", '''
source "$HELPER"
compose() { printf '%s\n' "$MAPPING"; return "$STUB_RC"; }
integration_endpoint docs-api 8010
'''], env={**os.environ, "HELPER": str(HELPER), "MAPPING": mapping, "STUB_RC": str(rc)},
        capture_output=True, text=True)
    if expected:
        assert result.returncode == 0
        assert result.stdout.strip() == expected
    else:
        assert result.returncode != 0
        assert result.stdout == ""


def test_fresh_install_uses_atomic_docker_ports_and_refreshes_after_recreate():
    source = (ROOT / "scripts/integration-fresh-install.sh").read_text()
    for kind in ("API", "DASH", "SITE", "MCP"):
        assert f"{kind}_PORT=${{{kind}_PORT:-0}}" in source
    assert "free_port" not in source
    assert source.count("API=$(integration_endpoint docs-api 8010)") == 3
    assert source.count("FRONT=$(integration_endpoint docs-web 80)") == 3
    assert 'MCP="$(integration_endpoint docs-mcp 8049)/mcp"' in source
