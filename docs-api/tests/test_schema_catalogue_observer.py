"""Source-only, negative-control, runtime and activation contracts for Schema observation."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import schema_catalogue  # noqa: E402
import schema_catalogue_observer as observer  # noqa: E402
import schema_catalogue_source  # noqa: E402

ENTITY = {"entity_id": "11111111-1111-4111-8111-111111111111", "entity_kind": "DATABASE", "entity_key": "docplane"}
STRUCTURE = {"docs": {"pages": {"comment": None, "columns": [], "constraints": [], "indexes": []}}}


class FakeClient:
    def __init__(self, *, entities=None):
        self.entities = [ENTITY] if entities is None else entities
        self.calls = []

    def call(self, method, path, payload=None, idempotency_key=None):
        self.calls.append((method, path, payload, idempotency_key))
        if method == "GET":
            return {"entities": self.entities}
        assert method == "POST" and path == "/api/v1/observations"
        return {"recorded": [{"observation_id": "22222222-2222-4222-8222-222222222222"}]}


class Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_observer_and_generator_share_exact_source_implementation():
    assert observer.introspect is schema_catalogue_source.introspect is schema_catalogue.introspect
    assert observer.fingerprint is schema_catalogue_source.fingerprint is schema_catalogue.fingerprint


def test_observer_import_graph_has_no_generator_or_mutation_surface():
    source = (SCRIPTS / "schema_catalogue_observer.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert "schema_catalogue_source" in imported_modules
    assert "schema_catalogue" not in imports
    for banned in ("render_pages", "publish_pages", "emit_generation", "ensure_entities", "reconcile_catalogues"):
        assert banned not in source


def test_success_emits_only_entity_scoped_freshness(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(observer, "introspect", lambda connection, schemas: STRUCTURE)
    result, succeeded = observer.observe_source(
        client, dsn="not-recorded", db_key="docplane", schemas=["docs"],
        probe_id="33333333-3333-4333-8333-333333333333", connector=lambda _: Connection(),
    )
    assert succeeded is True
    assert result["source_fingerprint"] == schema_catalogue_source.fingerprint(STRUCTURE)
    assert [call[0:2] for call in client.calls] == [
        ("GET", "/api/v1/model/entities?entity_kind=DATABASE&limit=1000"),
        ("POST", "/api/v1/observations"),
    ]
    _, _, batch, key = client.calls[-1]
    assert key == "schema-catalogue:source-probe:33333333-3333-4333-8333-333333333333:batch"
    assert len(batch["observations"]) == 1
    evidence = batch["observations"][0]
    assert evidence == {
        "subject_entity_id": ENTITY["entity_id"],
        "observation_kind": "FRESHNESS_CHECK",
        "outcome": "NOMINAL",
        "summary": "Observed authoritative Schema source for schema-catalogue",
        "payload": {"probe": "schema-catalogue-source"},
        "idempotency_key": "schema-catalogue:source-probe:33333333-3333-4333-8333-333333333333:observation",
        "source_fingerprint": schema_catalogue_source.fingerprint(STRUCTURE),
    }


def test_failed_introspection_is_bounded_and_cannot_masquerade_as_drift():
    client = FakeClient()

    def fail(_):
        raise RuntimeError("password=must-never-escape")

    result, succeeded = observer.observe_source(
        client, dsn="also-secret", db_key="docplane", schemas=["docs"],
        probe_id="44444444-4444-4444-8444-444444444444", connector=fail,
    )
    assert succeeded is False and result["source_fingerprint"] is None
    evidence = client.calls[-1][2]["observations"][0]
    assert evidence["outcome"] == "FAILED"
    assert "source_fingerprint" not in evidence
    assert evidence["payload"] == {
        "probe": "schema-catalogue-source", "stage": "INTROSPECT_SOURCE", "error_class": "RuntimeError"
    }
    assert "password" not in json.dumps(evidence)


def test_unresolved_identity_emits_no_observation():
    client = FakeClient(entities=[])
    with pytest.raises(RuntimeError, match="exactly one"):
        observer.observe_source(
            client, dsn="unused", db_key="docplane", schemas=["docs"],
            probe_id="55555555-5555-4555-8555-555555555555", connector=lambda _: Connection(),
        )
    assert len(client.calls) == 1 and client.calls[0][0] == "GET"


def test_wrapper_shares_lock_and_contention_is_benign_without_probe(tmp_path):
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "run_schema_catalogue_source_observer.sh"
    wrapper.write_text((SCRIPTS / wrapper.name).read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    marker = tmp_path / "observer-called"
    (scripts / "schema_catalogue_observer.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('called')\n", encoding="utf-8"
    )
    env_file = tmp_path / "observer.env"
    env_file.write_text(
        "DOCPLANE_API=https://docplane.invalid\nDOCPLANE_SCHEMA_OBSERVER_TOKEN=fake\n"
        "CATALOGUE_DB_KEY=docplane\nCATALOGUE_SCHEMAS=docs\nCATALOGUE_SOURCE_DB=docs\n"
        "CATALOGUE_SOURCE_USER=observer\nCATALOGUE_SOURCE_PASSWORD=fake\nCATALOGUE_SOURCE_PORT=5432\n"
        "CATALOGUE_SOURCE_COMPOSE_PROJECT=docplane\nCATALOGUE_SOURCE_COMPOSE_SERVICE=postgres\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    lock = tmp_path / "schema.lock"
    command = (
        f"exec 8>{lock}; flock -n 8; "
        f"DOCPLANE_SCHEMA_OBSERVER_ENV_FILE={env_file} DOCPLANE_SCHEMA_CATALOGUE_LOCK_FILE={lock} "
        f"bash {wrapper}"
    )
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
    assert result.returncode == 0
    assert "SKIPPED schema-catalogue exclusion domain" in result.stderr
    assert not marker.exists()


def test_runtime_and_units_are_inert_least_privilege_contracts():
    wrapper = (SCRIPTS / "run_schema_catalogue_source_observer.sh").read_text(encoding="utf-8")
    service = (ROOT / "config/systemd/docplane-schema-catalogue-observer.service").read_text(encoding="utf-8")
    timer = (ROOT / "config/systemd/docplane-schema-catalogue-observer.timer").read_text(encoding="utf-8")
    runbook = (ROOT / "docs/operations/SCHEMA_CATALOGUE.md").read_text(encoding="utf-8")
    assert "/run/lock/docplane-schema-catalogue.lock" in wrapper
    assert "DOCPLANE_SCHEMA_OBSERVER_TOKEN" in wrapper
    assert "DOCPLANE_SCHEMA_CATALOGUE_TOKEN" not in wrapper
    assert "schema_catalogue_observer.py" in wrapper and "schema_catalogue.py\"" not in wrapper
    assert "WantedBy=timers.target" in timer and "OnUnitInactiveSec=30min" in timer
    assert "Persistent=false" in timer and "EnvironmentFile=" not in service
    assert "ALTER ROLE" in runbook and "default_transaction_read_only = on" in runbook
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE" in runbook and "GRANT REFERENCES" in runbook
    assert "GRANT SELECT" not in runbook
    assert "search_path = docs" in runbook


@pytest.mark.skipif(not os.environ.get("DB_HOST"), reason="requires disposable PostgreSQL")
def test_disposable_least_privilege_role_preserves_projection_without_row_access():
    """Execute the #174 privilege/search-path contract on CI's throwaway DB."""
    import psycopg2
    from psycopg2 import sql

    owner_dsn = (
        f"host={os.environ['DB_HOST']} port={os.environ.get('DB_PORT', '5432')} "
        f"dbname={os.environ.get('DB_NAME', 'docs')} user={os.environ.get('DB_USER', 'docs')} "
        f"password={os.environ.get('DB_PASS', '')}"
    )
    role = f"schema_observer_test_{uuid4().hex[:10]}"
    password = uuid4().hex
    schemas = ["docplane", "docs", "model", "observe", "work"]
    probe_table = f"observer_privilege_probe_{uuid4().hex[:10]}"
    owner = psycopg2.connect(owner_dsn)
    owner.autocommit = True
    try:
        with owner.cursor() as cur:
            cur.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(role)), (password,))
            cur.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(sql.Identifier(role)))
            cur.execute(sql.SQL("ALTER ROLE {} SET search_path = docs").format(sql.Identifier(role)))
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.SQL(", ").join(map(sql.Identifier, schemas)), sql.Identifier(role)
            ))
            for schema in schemas:
                cur.execute(sql.SQL("GRANT REFERENCES ON ALL TABLES IN SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                ))
                cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT REFERENCES ON TABLES TO {}").format(
                    sql.Identifier(os.environ.get("DB_USER", "docs")), sql.Identifier(schema), sql.Identifier(role)
                ))
            # Created after default privileges: parity must remain durable.
            cur.execute(sql.SQL("CREATE TABLE docs.{} (id integer PRIMARY KEY, note text)").format(sql.Identifier(probe_table)))

        observer_dsn = (
            f"host={os.environ['DB_HOST']} port={os.environ.get('DB_PORT', '5432')} "
            f"dbname={os.environ.get('DB_NAME', 'docs')} user={role} password={password}"
        )
        with psycopg2.connect(owner_dsn) as owner_read, psycopg2.connect(observer_dsn) as observer_read:
            expected = schema_catalogue_source.introspect(owner_read, schemas)
            actual = schema_catalogue_source.introspect(observer_read, schemas)
        assert actual == expected
        assert schema_catalogue_source.fingerprint(actual) == schema_catalogue_source.fingerprint(expected)
        assert len(actual["docs"][probe_table]["columns"]) == 2

        with psycopg2.connect(observer_dsn) as restricted:
            with restricted.cursor() as cur:
                with pytest.raises(psycopg2.Error):
                    cur.execute("SELECT * FROM docs.pages LIMIT 1")
            restricted.rollback()
            with restricted.cursor() as cur:
                with pytest.raises(psycopg2.Error):
                    cur.execute("CREATE TABLE docs.observer_write_must_fail (id integer)")
    finally:
        with owner.cursor() as cur:
            cur.execute(sql.SQL("DROP TABLE IF EXISTS docs.{}").format(sql.Identifier(probe_table)))
            for schema in schemas:
                cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} REVOKE REFERENCES ON TABLES FROM {}").format(
                    sql.Identifier(os.environ.get("DB_USER", "docs")), sql.Identifier(schema), sql.Identifier(role)
                ))
                cur.execute(sql.SQL("REVOKE REFERENCES ON ALL TABLES IN SCHEMA {} FROM {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                ))
            cur.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        owner.close()
