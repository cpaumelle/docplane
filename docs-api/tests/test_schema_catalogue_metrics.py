"""The schema-catalogue generator reports projection status like every other generated projection.

Pure tests: the --metrics-file contract of the generator (written on success and on failure,
never by --dry-run) and the wrapper's per-database file selection (skipped for --dry-run and
for hosts without the textfile directory).
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

import schema_catalogue  # noqa: E402

SCRIPTS = ROOT / "scripts"


def _series(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            name, value = line.rsplit(" ", 1)
            out[name] = value
    return out


def test_metrics_written_on_success_name_the_database_artifact(monkeypatch, tmp_path):
    monkeypatch.setenv("CATALOGUE_DB_KEY", "trevarn")
    monkeypatch.setattr(schema_catalogue, "_reconcile", lambda args: 0)
    target = tmp_path / "docplane_schema_catalogue_trevarn.prom"
    assert schema_catalogue.main(["--metrics-file", str(target)]) == 0
    series = _series(target)
    assert series['docplane_generated_projection_drift{artifact="schema-catalogue-trevarn"}'] == "0"
    assert series['docplane_generated_projection_reconcile_success{artifact="schema-catalogue-trevarn"}'] == "1"
    assert 'docplane_generated_projection_last_run_unixtime{artifact="schema-catalogue-trevarn"}' in series


def test_metrics_written_as_failure_when_the_run_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("CATALOGUE_DB_KEY", "trevarn")

    def boom(args):
        raise RuntimeError("source unreachable")

    monkeypatch.setattr(schema_catalogue, "_reconcile", boom)
    target = tmp_path / "status.prom"
    with pytest.raises(RuntimeError, match="source unreachable"):
        schema_catalogue.main(["--metrics-file", str(target)])
    series = _series(target)
    assert series['docplane_generated_projection_reconcile_success{artifact="schema-catalogue-trevarn"}'] == "0"


def test_metrics_not_written_without_the_flag_and_refused_for_dry_run(monkeypatch, tmp_path):
    monkeypatch.setenv("CATALOGUE_DB_KEY", "trevarn")
    monkeypatch.setattr(schema_catalogue, "_reconcile", lambda args: 0)
    assert schema_catalogue.main([]) == 0
    assert not list(tmp_path.iterdir())
    with pytest.raises(SystemExit):
        schema_catalogue.main(["--dry-run", "--metrics-file", str(tmp_path / "x.prom")])


def _wrapper(tmp_path, *wrapper_args, metrics_env=None, make_dir=True):
    repository = tmp_path / "repo"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "run_schema_catalogue_reconciliation.sh"
    wrapper.write_text((SCRIPTS / wrapper.name).read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    for module in ("secret_source.py", "schema_catalogue_connection.py"):
        (scripts / module).write_text((SCRIPTS / module).read_text(encoding="utf-8"), encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    (scripts / "schema_catalogue.py").write_text(
        "import json, os, sys\nopen(os.environ['WRAPPER_RECEIPT'], 'w').write(json.dumps({'args': sys.argv[1:]}))\n",
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
        f"DOCPLANE_SCHEMA_CATALOGUE_TOKEN_FILE={secrets / 'docplane-token'}\n"
        "CATALOGUE_DB_KEY=trevarn\nCATALOGUE_DB_DISPLAY='Trevarn PostgreSQL'\nCATALOGUE_SCHEMAS=platform\n"
        "CATALOGUE_SOURCE_DB=trevarn\nCATALOGUE_SOURCE_USER=trevarn_schema_catalogue_reader\n"
        f"CATALOGUE_SOURCE_PASSWORD_FILE={secrets / 'db-password'}\n"
        "CATALOGUE_SOURCE_PORT=5432\nCATALOGUE_SOURCE_COMPOSE_PROJECT=trevarn-core\n"
        "CATALOGUE_SOURCE_COMPOSE_SERVICE=postgres\nCATALOGUE_ENVIRONMENT=production\n"
        "CATALOGUE_SOURCE_IDENTITY=VPS3/trevarn\nCATALOGUE_SOURCE_SSLMODE=disable\n",
        encoding="utf-8",
    )
    settings.chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/usr/bin/env bash\nif [[ $1 == ps ]]; then printf 'c\\n'; else printf 'trevarn-net=172.23.0.8\\n'; fi\n",
                      encoding="utf-8")
    docker.chmod(0o755)
    metrics_dir = tmp_path / "textfile"
    if make_dir:
        metrics_dir.mkdir()
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "DOCPLANE_SCHEMA_CATALOGUE_ENV_FILE": str(settings),
        "DOCPLANE_SCHEMA_CATALOGUE_LOCK_FILE": str(tmp_path / "lock"),
        "WRAPPER_RECEIPT": str(receipt),
        "DOCPLANE_SCHEMA_CATALOGUE_METRICS_FILE": metrics_env or str(metrics_dir / "docplane_schema_catalogue_trevarn.prom"),
    })
    result = subprocess.run(["bash", str(wrapper), *wrapper_args], env=env, capture_output=True, text=True)
    return result, json.loads(receipt.read_text()) if receipt.exists() else None, metrics_dir


def test_wrapper_passes_a_per_database_metrics_file(tmp_path):
    result, receipt, metrics_dir = _wrapper(tmp_path)
    assert result.returncode == 0, result.stderr
    assert receipt == {"args": ["--metrics-file", str(metrics_dir / "docplane_schema_catalogue_trevarn.prom")]}


def test_wrapper_never_passes_metrics_for_a_dry_run(tmp_path):
    result, receipt, _ = _wrapper(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert receipt == {"args": ["--dry-run"]}


def test_wrapper_skips_metrics_when_the_textfile_directory_is_absent(tmp_path):
    result, receipt, _ = _wrapper(tmp_path, make_dir=False)
    assert result.returncode == 0, result.stderr
    assert receipt == {"args": []}
    assert "metrics directory" in result.stderr and "not written" in result.stderr
