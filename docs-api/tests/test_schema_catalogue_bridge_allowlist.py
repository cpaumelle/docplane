"""The plaintext-bridge allowlist shared by the Python connection builder and both host wrappers.

sslmode=disable is accepted only for an allowlisted (environment, source identity) pair whose
container the wrapper has verified on the bound Docker network: VM1124 development and VPS3
production. Pure tests; the wrappers run against a fake `docker`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import schema_catalogue_connection as connection  # noqa: E402

SCRIPTS = ROOT / "scripts"


@pytest.mark.parametrize("environment,identity,network,ok", [
    ("development", "VM1124/trevarn", "trevarn-net", True),
    ("production", "VPS3/trevarn", "trevarn-net", True),
    ("production", "VM1124/trevarn", "trevarn-net", False),   # development identity labelled production
    ("development", "VPS3/trevarn", "trevarn-net", False),
    ("production", "VPS3/trevarn", "bridge", False),          # right identity, wrong network
    ("staging", "VM1424/trevarn", "trevarn-net", False),      # not allowlisted until its own review
    ("", "", "", False),
])
def test_plaintext_bridge_allowlist(environment, identity, network, ok):
    assert connection.plaintext_bridge_allowed(environment, identity, network) is ok


def test_connection_parameters_accept_the_production_vps3_bridge_only_on_its_network(monkeypatch, tmp_path):
    secret = tmp_path / "db-password"
    secret.write_text("pw", encoding="utf-8")
    secret.chmod(0o400)
    monkeypatch.setenv("CATALOGUE_SOURCE_HOST", "172.23.0.9")
    monkeypatch.setenv("CATALOGUE_SOURCE_DB", "trevarn")
    monkeypatch.setenv("CATALOGUE_SOURCE_USER", "trevarn_schema_catalogue_reader")
    monkeypatch.setenv("CATALOGUE_SOURCE_PORT", "5432")
    monkeypatch.setenv("CATALOGUE_SOURCE_PASSWORD_FILE", str(secret))
    monkeypatch.setenv("CATALOGUE_SOURCE_SSLMODE", "disable")
    monkeypatch.setenv("CATALOGUE_ENVIRONMENT", "production")
    monkeypatch.setenv("CATALOGUE_SOURCE_IDENTITY", "VPS3/trevarn")
    monkeypatch.setenv("CATALOGUE_SOURCE_DOCKER_NETWORK_VERIFIED", "trevarn-net")
    assert connection.source_connection_parameters()["sslmode"] == "disable"
    monkeypatch.setenv("CATALOGUE_SOURCE_DOCKER_NETWORK_VERIFIED", "bridge")
    with pytest.raises(RuntimeError, match="allowlisted Trevarn Docker bridge"):
        connection.source_connection_parameters()


def _wrapper_run(tmp_path, wrapper_name, env_file_var, token_var, *, environment, identity, network):
    repository = tmp_path / "repo"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / wrapper_name
    wrapper.write_text((SCRIPTS / wrapper_name).read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    for module in ("secret_source.py", "schema_catalogue_connection.py"):
        (scripts / module).write_text((SCRIPTS / module).read_text(encoding="utf-8"), encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    stub = "schema_catalogue.py" if "reconciliation" in wrapper_name else "schema_catalogue_observer.py"
    (scripts / stub).write_text(
        "import json, os, sys\n"
        "sys.path.insert(0, os.path.dirname(__file__))\n"
        "from schema_catalogue_connection import source_connection_parameters\n"
        "params = source_connection_parameters()\n"
        "open(os.environ['WRAPPER_RECEIPT'], 'w').write(json.dumps({'host': params['host'], "
        "'sslmode': params.get('sslmode'), 'verified': os.environ.get('CATALOGUE_SOURCE_DOCKER_NETWORK_VERIFIED')}))\n",
        encoding="utf-8",
    )
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    for name in ("db-password", "docplane-token"):
        (secrets / name).write_text("synthetic", encoding="utf-8")
        (secrets / name).chmod(0o400)
    settings = tmp_path / "settings.env"
    settings.write_text(
        "DOCPLANE_API=https://docplane.invalid\n"
        f"{token_var}_FILE={secrets / 'docplane-token'}\n"
        "CATALOGUE_DB_KEY=trevarn\nCATALOGUE_DB_DISPLAY='Trevarn PostgreSQL'\nCATALOGUE_SCHEMAS=platform\n"
        "CATALOGUE_SOURCE_DB=trevarn\nCATALOGUE_SOURCE_USER=trevarn_schema_catalogue_reader\n"
        f"CATALOGUE_SOURCE_PASSWORD_FILE={secrets / 'db-password'}\n"
        "CATALOGUE_SOURCE_PORT=5432\nCATALOGUE_SOURCE_COMPOSE_PROJECT=trevarn-core\n"
        "CATALOGUE_SOURCE_COMPOSE_SERVICE=postgres\n"
        f"CATALOGUE_ENVIRONMENT={environment}\nCATALOGUE_SOURCE_IDENTITY={identity}\n"
        "CATALOGUE_SOURCE_SSLMODE=disable\n",
        encoding="utf-8",
    )
    settings.chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ $1 == ps ]]; then printf 'postgres-container\\n'; "
        f"else printf '{network}=172.23.0.8\\n'; fi\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        env_file_var: str(settings),
        "DOCPLANE_SCHEMA_CATALOGUE_LOCK_FILE": str(tmp_path / "lock"),
        "WRAPPER_RECEIPT": str(receipt),
        "DOCPLANE_SCHEMA_CATALOGUE_METRICS_FILE": str(tmp_path / "missing-dir" / "x.prom"),
    })
    result = subprocess.run(["bash", str(wrapper), "--dry-run"], env=env, capture_output=True, text=True)
    return result, (json.loads(receipt.read_text()) if receipt.exists() else None)


WRAPPERS = [
    ("run_schema_catalogue_reconciliation.sh", "DOCPLANE_SCHEMA_CATALOGUE_ENV_FILE", "DOCPLANE_SCHEMA_CATALOGUE_TOKEN"),
    ("run_schema_catalogue_source_observer.sh", "DOCPLANE_SCHEMA_OBSERVER_ENV_FILE", "DOCPLANE_SCHEMA_OBSERVER_TOKEN"),
]


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=["generator", "observer"])
@pytest.mark.parametrize("environment,identity,network", [
    ("development", "VM1124/trevarn", "trevarn-net"),
    ("production", "VPS3/trevarn", "trevarn-net"),
])
def test_wrappers_allow_each_allowlisted_bridge_and_export_the_verified_network(tmp_path, wrapper, environment, identity, network):
    result, receipt = _wrapper_run(tmp_path, *wrapper, environment=environment, identity=identity, network=network)
    assert result.returncode == 0, result.stderr
    assert receipt == {"host": "172.23.0.8", "sslmode": "disable", "verified": network}


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=["generator", "observer"])
@pytest.mark.parametrize("environment,identity,network", [
    ("production", "VPS3/trevarn", "bridge"),            # production container off its bridge
    ("production", "VM1124/trevarn", "trevarn-net"),     # development identity labelled production
    ("staging", "VM1424/trevarn", "trevarn-net"),        # staging is not allowlisted yet
])
def test_wrappers_refuse_plaintext_outside_the_allowlist(tmp_path, wrapper, environment, identity, network):
    result, receipt = _wrapper_run(tmp_path, *wrapper, environment=environment, identity=identity, network=network)
    assert result.returncode != 0
    assert "allowlisted Trevarn Docker bridge" in result.stderr
    assert receipt is None
