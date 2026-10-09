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
    for module in ("secret_source.py", "schema_catalogue_connection.py"):
        (scripts / module).write_text((SCRIPTS / module).read_text(encoding="utf-8"), encoding="utf-8")
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


def test_observer_host_wrapper_uses_its_own_secret_files_and_source_parameters(tmp_path):
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "run_schema_catalogue_source_observer.sh"
    wrapper.write_text((SCRIPTS / wrapper.name).read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    for module in ("secret_source.py", "schema_catalogue_connection.py"):
        (scripts / module).write_text((SCRIPTS / module).read_text(encoding="utf-8"), encoding="utf-8")
    receipt = tmp_path / "observer-receipt.json"
    (scripts / "schema_catalogue_observer.py").write_text(
        "import json, os, sys\n"
        "sys.path.insert(0, os.path.dirname(__file__))\n"
        "from secret_source import read_secret\n"
        "from schema_catalogue_connection import source_connection_parameters\n"
        "params = source_connection_parameters()\n"
        "token = read_secret('DOCPLANE_SCHEMA_OBSERVER_TOKEN')\n"
        "with open(os.environ['OBSERVER_WRAPPER_RECEIPT'], 'w') as out:\n"
        " out.write(json.dumps({'host': params['host'], 'password_present': bool(params['password']), "
        "'token_present': bool(token), 'has_dsn': 'dsn' in params}))\n",
        encoding="utf-8",
    )
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir(mode=0o700)
    files = {}
    for name, value in (("db-password", "observer-db-secret"), ("docplane-token", "observer-api-secret")):
        path = secret_dir / name
        path.write_text(value, encoding="utf-8")
        path.chmod(0o400)
        files[name] = path
    settings = tmp_path / "observer.env"
    settings.write_text(
        "DOCPLANE_API=https://docplane.invalid\n"
        f"DOCPLANE_SCHEMA_OBSERVER_TOKEN_FILE={files['docplane-token']}\n"
        "CATALOGUE_DB_KEY=trevarn\nCATALOGUE_SCHEMAS=platform,ingest\n"
        "CATALOGUE_SOURCE_DB=trevarn\nCATALOGUE_SOURCE_USER=trevarn_schema_catalogue_observer\n"
        f"CATALOGUE_SOURCE_PASSWORD_FILE={files['db-password']}\n"
        "CATALOGUE_SOURCE_PORT=5432\nCATALOGUE_SOURCE_COMPOSE_PROJECT=trevarn-core\n"
        "CATALOGUE_SOURCE_COMPOSE_SERVICE=postgres\nCATALOGUE_ENVIRONMENT=development\n"
        "CATALOGUE_SOURCE_IDENTITY=VM1124/trevarn\nCATALOGUE_SOURCE_DOCKER_NETWORK=trevarn-net\n"
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
        "else printf 'trevarn-net=172.23.0.8\\n'; fi\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "DOCPLANE_SCHEMA_OBSERVER_ENV_FILE": str(settings),
        "DOCPLANE_SCHEMA_CATALOGUE_LOCK_FILE": str(tmp_path / "observer.lock"),
        "OBSERVER_WRAPPER_RECEIPT": str(receipt),
    })
    result = subprocess.run(
        ["bash", str(wrapper), "--probe-id", "33333333-3333-4333-8333-333333333333"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "observer-db-secret" not in result.stdout + result.stderr
    assert "observer-api-secret" not in result.stdout + result.stderr
    assert json.loads(receipt.read_text()) == {
        "host": "172.23.0.8", "password_present": True, "token_present": True, "has_dsn": False,
    }


@pytest.mark.parametrize("consumer", [schema_catalogue, observer], ids=["generator", "observer"])
def test_connection_parameters_read_password_file_and_confine_disable_sslmode(
    monkeypatch, tmp_path, consumer
):
    secret_file = tmp_path / "db-password"
    secret_file.write_text("password with punctuation", encoding="utf-8")
    secret_file.chmod(0o400)
    monkeypatch.setenv("CATALOGUE_SOURCE_HOST", "172.23.0.9")
    monkeypatch.setenv("CATALOGUE_SOURCE_DB", "trevarn")
    monkeypatch.setenv("CATALOGUE_SOURCE_USER", "trevarn_schema_catalogue_reader")
    monkeypatch.setenv("CATALOGUE_SOURCE_PORT", "5432")
    monkeypatch.setenv("CATALOGUE_SOURCE_PASSWORD_FILE", str(secret_file))
    monkeypatch.setenv("CATALOGUE_SOURCE_SSLMODE", "disable")
    monkeypatch.setenv("CATALOGUE_ENVIRONMENT", "development")
    monkeypatch.setenv("CATALOGUE_SOURCE_IDENTITY", "VM1124/trevarn")
    monkeypatch.setenv("CATALOGUE_SOURCE_DOCKER_NETWORK", "trevarn-net")
    params = consumer.source_connection_parameters()
    assert params == {
        "host": "172.23.0.9", "dbname": "trevarn",
        "user": "trevarn_schema_catalogue_reader", "password": "password with punctuation",
        "port": "5432", "sslmode": "disable",
    }
    assert "dsn" not in params

    monkeypatch.setenv("CATALOGUE_ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="restricted to the VM1124 Trevarn Docker bridge"):
        consumer.source_connection_parameters()

    monkeypatch.delenv("CATALOGUE_SOURCE_SSLMODE")
    monkeypatch.setenv("PGSSLMODE", "disable")
    with pytest.raises(RuntimeError, match="restricted to the VM1124 Trevarn Docker bridge"):
        consumer.source_connection_parameters()


@pytest.mark.parametrize("consumer", [schema_catalogue, observer], ids=["generator", "observer"])
def test_connection_parameters_fail_closed_on_missing_or_invalid_password_file(
    monkeypatch, tmp_path, consumer
):
    from secret_source import SecretSourceError

    monkeypatch.setenv("CATALOGUE_SOURCE_HOST", "172.23.0.9")
    monkeypatch.setenv("CATALOGUE_SOURCE_DB", "trevarn")
    monkeypatch.setenv("CATALOGUE_SOURCE_USER", "reader")
    monkeypatch.setenv("CATALOGUE_SOURCE_PORT", "5432")
    monkeypatch.setenv("CATALOGUE_SOURCE_PASSWORD", "legacy-password")
    monkeypatch.setenv("CATALOGUE_SOURCE_PASSWORD_FILE", str(tmp_path / "missing-password"))
    with pytest.raises(SecretSourceError) as missing:
        consumer.source_connection_parameters()
    assert "legacy-password" not in str(missing.value)

    invalid = tmp_path / "invalid-password"
    invalid.write_bytes(b"\xff")
    monkeypatch.setenv("CATALOGUE_SOURCE_PASSWORD_FILE", str(invalid))
    with pytest.raises(SecretSourceError, match="not valid UTF-8"):
        consumer.source_connection_parameters()

    readable = tmp_path / "unreadable-password"
    readable.write_text("unreadable-sentinel", encoding="utf-8")
    monkeypatch.setenv("CATALOGUE_SOURCE_PASSWORD_FILE", str(readable))
    real_read_bytes = Path.read_bytes

    def deny_password_read(path):
        if path == readable:
            raise PermissionError("permission denied")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", deny_password_read)
    with pytest.raises(SecretSourceError, match="not readable") as unreadable:
        consumer.source_connection_parameters()
    assert "unreadable-sentinel" not in str(unreadable.value)


@pytest.mark.parametrize(
    ("consumer", "required", "other"),
    [
        (schema_catalogue, "DOCPLANE_SCHEMA_CATALOGUE_TOKEN", "DOCPLANE_SCHEMA_OBSERVER_TOKEN"),
        (observer, "DOCPLANE_SCHEMA_OBSERVER_TOKEN", "DOCPLANE_SCHEMA_CATALOGUE_TOKEN"),
    ],
    ids=["generator-token-is-not-observer-token", "observer-token-is-not-generator-token"],
)
def test_consumers_do_not_fall_back_to_the_other_consumers_token(
    monkeypatch, tmp_path, consumer, required, other
):
    from secret_source import SecretSourceError

    wrong_token_file = tmp_path / "other-token"
    wrong_token_file.write_text("wrong-consumer-token", encoding="utf-8")
    monkeypatch.setenv(f"{other}_FILE", str(wrong_token_file))
    monkeypatch.delenv(required, raising=False)
    monkeypatch.delenv(f"{required}_FILE", raising=False)
    with pytest.raises(SecretSourceError):
        consumer._required_secret(required)


@pytest.mark.skipif(not os.environ.get("DB_HOST"), reason="requires disposable PostgreSQL")
@pytest.mark.parametrize("consumer", [schema_catalogue, observer], ids=["generator", "observer"])
def test_disposable_bare_reader_preserves_contract2_without_tenant_data_access(consumer):
    """Both one-shot consumers introspect pg_catalog without schema/table grants."""
    import psycopg2
    from psycopg2 import sql

    owner_dsn = (
        f"host={os.environ['DB_HOST']} port={os.environ.get('DB_PORT', '5432')} "
        f"dbname={os.environ.get('DB_NAME', 'docs')} user={os.environ.get('DB_USER', 'docs')} "
        f"password={os.environ.get('DB_PASS', '')}"
    )
    role = f"schema_catalogue_test_{uuid4().hex[:10]}"
    password = uuid4().hex
    schemas = ["docplane", "docs", "model", "observe", "work"]
    probe_table = f"observer_privilege_probe_{uuid4().hex[:10]}"
    owner = psycopg2.connect(owner_dsn)
    owner.autocommit = True
    try:
        with owner.cursor() as cur:
            cur.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(role)), (password,))
            cur.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(sql.Identifier(role)))
            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(os.environ.get("DB_NAME", "docs")), sql.Identifier(role)
            ))
            # Created after default privileges: parity must remain durable.
            cur.execute(sql.SQL("CREATE TABLE docs.{} (id integer PRIMARY KEY, note text)").format(sql.Identifier(probe_table)))

        reader_dsn = (
            f"host={os.environ['DB_HOST']} port={os.environ.get('DB_PORT', '5432')} "
            f"dbname={os.environ.get('DB_NAME', 'docs')} user={role} password={password}"
        )
        with psycopg2.connect(owner_dsn) as owner_read, psycopg2.connect(reader_dsn) as reader_read:
            expected = schema_catalogue_source.introspect(owner_read, schemas)
            actual = consumer.introspect(reader_read, schemas)
        assert actual == expected
        assert schema_catalogue_source.fingerprint(actual) == schema_catalogue_source.fingerprint(expected)
        assert len(actual["docs"]["tables"][probe_table]["columns"]) == 2

        with psycopg2.connect(reader_dsn) as restricted:
            with restricted.cursor() as cur:
                with pytest.raises(psycopg2.Error):
                    cur.execute("SELECT * FROM docs.pages LIMIT 1")
            restricted.rollback()
            with restricted.cursor() as cur:
                with pytest.raises(psycopg2.Error):
                    cur.execute("CREATE TABLE docs.catalogue_write_must_fail (id integer)")
    finally:
        with owner.cursor() as cur:
            cur.execute(sql.SQL("DROP TABLE IF EXISTS docs.{}").format(sql.Identifier(probe_table)))
            cur.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM {}").format(
                sql.Identifier(os.environ.get("DB_NAME", "docs")), sql.Identifier(role)
            ))
            cur.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        owner.close()


@pytest.mark.parametrize(
    ("consumer", "name"),
    [
        (schema_catalogue, "DOCPLANE_SCHEMA_CATALOGUE_TOKEN"),
        (schema_catalogue, "CATALOGUE_SOURCE_PASSWORD"),
        (observer, "DOCPLANE_SCHEMA_OBSERVER_TOKEN"),
        (observer, "CATALOGUE_SOURCE_PASSWORD"),
    ],
)
def test_secrets_v3_file_source_wins_for_each_runtime_secret(
    monkeypatch, tmp_path, consumer, name
):
    secret_dir = tmp_path / "runtime-secrets"
    secret_dir.mkdir()
    secret_dir.chmod(0o700)
    secret_file = secret_dir / "runtime-secret"
    secret_file.write_text("file-value", encoding="utf-8")
    secret_file.chmod(0o400)
    assert secret_dir.stat().st_mode & 0o777 == 0o700
    assert secret_file.stat().st_mode & 0o777 == 0o400
    monkeypatch.setenv(f"{name}_FILE", str(secret_file))
    monkeypatch.setenv(name, "legacy-value")

    assert consumer._required_secret(name) == "file-value"


@pytest.mark.parametrize(
    ("consumer", "name"),
    [
        (schema_catalogue, "DOCPLANE_SCHEMA_CATALOGUE_TOKEN"),
        (schema_catalogue, "CATALOGUE_SOURCE_PASSWORD"),
        (observer, "DOCPLANE_SCHEMA_OBSERVER_TOKEN"),
        (observer, "CATALOGUE_SOURCE_PASSWORD"),
    ],
)
def test_secrets_v3_missing_file_fails_closed_for_each_runtime_secret(
    monkeypatch, tmp_path, consumer, name
):
    from secret_source import SecretSourceError

    monkeypatch.setenv(f"{name}_FILE", str(tmp_path / "missing-secret"))
    monkeypatch.setenv(name, "legacy-value")

    with pytest.raises(SecretSourceError) as error:
        consumer._required_secret(name)
    assert "legacy-value" not in str(error.value)


@pytest.mark.parametrize(
    ("consumer", "name"),
    [
        (schema_catalogue, "DOCPLANE_SCHEMA_CATALOGUE_TOKEN"),
        (schema_catalogue, "CATALOGUE_SOURCE_PASSWORD"),
        (observer, "DOCPLANE_SCHEMA_OBSERVER_TOKEN"),
        (observer, "CATALOGUE_SOURCE_PASSWORD"),
    ],
)
def test_secrets_v3_unreadable_file_fails_closed_for_each_runtime_secret(
    monkeypatch, tmp_path, consumer, name
):
    from secret_source import SecretSourceError

    secret_file = tmp_path / "unreadable-secret"
    monkeypatch.setenv(f"{name}_FILE", str(secret_file))
    monkeypatch.setenv(name, "sensitive-legacy-value")

    def deny_read(path):
        if path == secret_file:
            raise PermissionError("permission denied")
        raise AssertionError("unexpected file read")

    monkeypatch.setattr(Path, "read_bytes", deny_read)
    with pytest.raises(SecretSourceError) as error:
        consumer._required_secret(name)
    assert "sensitive-legacy-value" not in str(error.value)
    assert "sensitive-legacy-value" not in repr(error.value)
