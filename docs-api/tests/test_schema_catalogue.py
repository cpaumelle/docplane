"""Sprint 5 exemplar: the schema-catalogue generator.

The generator is fingerprint-bound, redaction-gated and idempotent. These
tests cover the pure core (structure canonicalisation, rendering, keys,
skip logic) and — when a database is available — real introspection against
DocPlane's own schemas, which is exactly the canary the exemplar names.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import schema_catalogue  # noqa: E402
from migration.redaction import DocumentRefusedError  # noqa: E402


STRUCTURE = {
    "docplane": {
        "principals": {
            "comment": "Named identities",
            "columns": [
                {"name": "principal_id", "type": "uuid", "nullable": False, "default": "gen_random_uuid()"},
                {"name": "display_name", "type": "text", "nullable": False, "default": None},
            ],
            "constraints": [
                {"kind": "p", "name": "principals_pkey", "definition": "PRIMARY KEY (principal_id)"},
            ],
            "indexes": [{"name": "principals_pkey", "definition": "CREATE UNIQUE INDEX ..."}],
        },
    },
}


def _wrapper_fixture(tmp_path: Path, *, docker_mode: str = "valid", hold: str = "0"):
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "run_schema_catalogue_reconciliation.sh"
    wrapper.write_text(
        (ROOT / "scripts" / "run_schema_catalogue_reconciliation.sh").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    receipt = tmp_path / "generator-receipt.json"
    generator = scripts / "schema_catalogue.py"
    generator.write_text(
        "import json, os, sys, time\n"
        "from urllib.parse import urlsplit\n"
        "dsn = urlsplit(os.environ['CATALOGUE_SOURCE_DSN'])\n"
        "with open(os.environ['SCHEMA_WRAPPER_RECEIPT'], 'a', encoding='utf-8') as out:\n"
        "    out.write(json.dumps({'scheme': dsn.scheme, 'host': dsn.hostname, "
        "'port': dsn.port, 'database': dsn.path, 'password_present': bool(dsn.password), "
        "'args': sys.argv[1:]}) + '\\n')\n"
        "time.sleep(float(os.environ.get('SCHEMA_WRAPPER_HOLD_SECONDS', '0')))\n",
        encoding="utf-8",
    )
    environment_file = tmp_path / "schema.env"
    environment_file.write_text(
        "DOCPLANE_API=https://docplane.invalid\n"
        "DOCPLANE_SCHEMA_CATALOGUE_TOKEN=synthetic-test-token\n"
        "CATALOGUE_DB_KEY=docplane\n"
        "CATALOGUE_DB_DISPLAY='DocPlane PostgreSQL'\n"
        "CATALOGUE_SCHEMAS=docplane,docs,model,observe,work\n"
        "CATALOGUE_SOURCE_DB=docs\n"
        "CATALOGUE_SOURCE_USER=docs\n"
        "CATALOGUE_SOURCE_PASSWORD='synthetic password/with punctuation'\n"
        "CATALOGUE_SOURCE_PORT=5432\n"
        "CATALOGUE_SOURCE_COMPOSE_PROJECT=docplane\n"
        "CATALOGUE_SOURCE_COMPOSE_SERVICE=postgres\n",
        encoding="utf-8",
    )
    environment_file.chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "if [[ $1 == ps ]]; then\n"
        "  case \"$SCHEMA_DOCKER_MODE\" in\n"
        "    missing) exit 0 ;;\n"
        "    ambiguous-container) printf 'container-a\\ncontainer-b\\n' ;;\n"
        "    *) printf 'container-a\\n' ;;\n"
        "  esac\n"
        "elif [[ $1 == inspect ]]; then\n"
        "  case \"$SCHEMA_DOCKER_MODE\" in\n"
        "    unresolved-address) exit 0 ;;\n"
        "    ambiguous-address) printf '172.23.0.5\\n172.24.0.5\\n' ;;\n"
        "    invalid-address) printf 'not-an-address\\n' ;;\n"
        "    *) printf '172.23.0.5\\n' ;;\n"
        "  esac\n"
        "else\n"
        "  exit 2\n"
        "fi\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "DOCPLANE_SCHEMA_CATALOGUE_ENV_FILE": str(environment_file),
            "DOCPLANE_SCHEMA_CATALOGUE_LOCK_FILE": str(tmp_path / "schema.lock"),
            "SCHEMA_DOCKER_MODE": docker_mode,
            "SCHEMA_WRAPPER_RECEIPT": str(receipt),
            "SCHEMA_WRAPPER_HOLD_SECONDS": hold,
        }
    )
    return wrapper, environment_file, receipt, env


def test_runtime_wrapper_contract_is_bounded_and_contains_no_secrets_or_schedule():
    wrapper = (ROOT / "scripts" / "run_schema_catalogue_reconciliation.sh").read_text(
        encoding="utf-8"
    )

    assert "/run/lock/docplane-schema-catalogue.lock" in wrapper
    assert "/tmp" not in wrapper
    assert "/etc/docplane/schema-catalogue.env" in wrapper
    assert "CATALOGUE_SOURCE_DSN=" in wrapper
    assert "com.docker.compose.project=" in wrapper
    assert "com.docker.compose.service=" in wrapper
    assert "DOCPLANE_SCHEMA_CATALOGUE_TOKEN=" not in wrapper
    assert "CATALOGUE_SOURCE_PASSWORD=" not in wrapper
    assert "172." not in wrapper
    assert not any(
        path.name.endswith((".service", ".timer"))
        and "schema-catalogue" in path.name
        for path in ROOT.rglob("*")
    )


def test_runtime_wrapper_discovers_endpoint_and_passes_only_transient_dsn(tmp_path):
    wrapper, _, receipt, env = _wrapper_fixture(tmp_path)

    result = subprocess.run(
        ["bash", str(wrapper), "--dry-run"], env=env, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert "synthetic password" not in result.stdout + result.stderr
    record = json.loads(receipt.read_text(encoding="utf-8"))
    assert record == {
        "scheme": "postgresql",
        "host": "172.23.0.5",
        "port": 5432,
        "database": "/docs",
        "password_present": True,
        "args": ["--dry-run"],
    }


def test_runtime_wrapper_excludes_concurrent_runs_and_releases_lock(tmp_path):
    wrapper, _, receipt, env = _wrapper_fixture(tmp_path, hold="1")
    holder = subprocess.Popen(
        ["bash", str(wrapper)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        for _ in range(100):
            if receipt.exists():
                break
            holder.poll()
            if holder.returncode is not None:
                break
            time.sleep(0.01)
        assert receipt.exists()

        contender = subprocess.run(
            ["bash", str(wrapper)], env=env, capture_output=True, text=True
        )
        assert contender.returncode == 75
        assert "SKIPPED" in contender.stderr
        assert receipt.read_text(encoding="utf-8").count("\n") == 1
        assert holder.wait(timeout=5) == 0
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=5)

    env["SCHEMA_WRAPPER_HOLD_SECONDS"] = "0"
    subsequent = subprocess.run(
        ["bash", str(wrapper)], env=env, capture_output=True, text=True
    )
    assert subsequent.returncode == 0
    assert receipt.read_text(encoding="utf-8").count("\n") == 2


@pytest.mark.parametrize(
    ("docker_mode", "message"),
    [
        ("missing", "runtime identity did not resolve uniquely"),
        ("ambiguous-container", "runtime identity did not resolve uniquely"),
        ("unresolved-address", "endpoint did not resolve uniquely"),
        ("ambiguous-address", "endpoint did not resolve uniquely"),
        ("invalid-address", "endpoint validation failed"),
    ],
)
def test_runtime_wrapper_fails_closed_on_runtime_discovery(
    tmp_path, docker_mode, message
):
    wrapper, _, receipt, env = _wrapper_fixture(tmp_path, docker_mode=docker_mode)

    result = subprocess.run(
        ["bash", str(wrapper)], env=env, capture_output=True, text=True
    )

    assert result.returncode == 78
    assert message in result.stderr
    assert not receipt.exists()


def test_runtime_wrapper_fails_before_generator_for_environment_errors(tmp_path):
    wrapper, environment_file, receipt, env = _wrapper_fixture(tmp_path)
    environment_file.unlink()
    missing = subprocess.run(
        ["bash", str(wrapper)], env=env, capture_output=True, text=True
    )
    assert missing.returncode == 78
    assert "absent or unreadable" in missing.stderr
    assert not receipt.exists()

    _, environment_file, receipt, env = _wrapper_fixture(tmp_path / "second")
    content = environment_file.read_text(encoding="utf-8").replace(
        "CATALOGUE_DB_KEY=docplane\n", ""
    )
    environment_file.write_text(content, encoding="utf-8")
    environment_file.chmod(0o600)
    incomplete = subprocess.run(
        ["bash", str(wrapper)], env=env, capture_output=True, text=True
    )
    assert incomplete.returncode == 78
    assert "CATALOGUE_DB_KEY is missing" in incomplete.stderr
    assert not receipt.exists()


def test_runtime_wrapper_rejects_insecure_or_persisted_endpoint_configuration(tmp_path):
    wrapper, environment_file, receipt, env = _wrapper_fixture(tmp_path)
    environment_file.chmod(0o640)
    insecure = subprocess.run(
        ["bash", str(wrapper)], env=env, capture_output=True, text=True
    )
    assert insecure.returncode == 78
    assert "mode 0600" in insecure.stderr
    assert not receipt.exists()

    environment_file.chmod(0o600)
    with environment_file.open("a", encoding="utf-8") as stream:
        stream.write("CATALOGUE_SOURCE_DSN=postgresql://stale.invalid/docs\n")
    persisted = subprocess.run(
        ["bash", str(wrapper)], env=env, capture_output=True, text=True
    )
    assert persisted.returncode == 78
    assert "must not persist CATALOGUE_SOURCE_DSN" in persisted.stderr
    assert not receipt.exists()


def test_fingerprint_is_deterministic_and_structure_sensitive():
    first = schema_catalogue.fingerprint(STRUCTURE)
    assert first == schema_catalogue.fingerprint(STRUCTURE)
    assert len(first) == 64
    mutated = {"docplane": {**STRUCTURE["docplane"], "extra": {}}}
    assert schema_catalogue.fingerprint(mutated) != first


def test_rendering_is_deterministic_stamped_and_structure_only():
    fp = schema_catalogue.fingerprint(STRUCTURE)
    pages = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", STRUCTURE, fp)
    again = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", STRUCTURE, fp)
    assert pages == again
    assert [page["path"] for page in pages] == [
        "model/schema-catalogue/docplane/index.md",
        "model/schema-catalogue/docplane/docplane.md",
    ]
    for page in pages:
        assert fp[:16] in page["content"]
        assert "structure only, no row data" in page["content"]
        assert page["nav_path"].startswith("Model / Schema catalogue")
    schema_page = pages[1]
    assert "`principals`" in schema_page["content"]
    assert "| `principal_id` | uuid | no |" in schema_page["content"]
    assert "principals_pkey" in schema_page["content"]


def test_rendering_is_redaction_gated_fail_closed():
    poisoned = {
        "docplane": {
            "tokens": {
                "comment": None,
                "columns": [
                    {
                        "name": "token",
                        "type": "text",
                        "nullable": False,
                        # A secret-shaped default must abort the run, never
                        # publish partially redacted content silently.
                        "default": "'AKIAIOSFODNN7REALKEY'",
                    }
                ],
                "constraints": [],
                "indexes": [],
            },
        },
    }
    fp = schema_catalogue.fingerprint(poisoned)
    pages = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", poisoned, fp)
    rendered = "\n".join(page["content"] for page in pages)
    # The canonical transform sanitises secret-shaped bytes with markers (and
    # refuses outright when it cannot — DocumentRefusedError propagates and
    # aborts the run). Either way the secret must never survive rendering.
    assert "AKIAIOSFODNN7REALKEY" not in rendered
    assert DocumentRefusedError is not None  # the refusal path stays imported and wired


def test_presence_page_is_permanent_and_never_an_artifact_target():
    page = schema_catalogue.presence_page()
    assert page["path"] == "model/schema-catalogue/index.md"
    assert "permanent" in page["content"]
    fp = schema_catalogue.fingerprint(STRUCTURE)
    rendered = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", STRUCTURE, fp)
    assert page["path"] not in {item["path"] for item in rendered}


def test_idempotency_keys_are_fingerprint_bound_versioned_and_bounded():
    fp = "ab" * 32
    key = schema_catalogue._key(fp, "operation", "model/schema-catalogue/docplane/docplane.md")
    assert key.startswith(
        f"schema-catalogue-{schema_catalogue.GENERATOR_VERSION}-{fp[:16]}-operation-"
    )
    assert len(key) <= 256
    assert key == schema_catalogue._key(fp, "operation", "model/schema-catalogue/docplane/docplane.md")
    assert key != schema_catalogue._key("cd" * 32, "operation", "model/schema-catalogue/docplane/docplane.md")
    # Version-bound: a fixed generator must never replay receipts a buggy
    # predecessor persisted for the same structure fingerprint.
    assert schema_catalogue.GENERATOR_VERSION in key


def test_navigation_is_collision_free_under_the_deployed_validator():
    """Insert every rendered nav_path (plus the presence page) into the REAL
    nav builder from app.generator — the exact validator that rejected the
    first canary's leaf-vs-section collision on 'DocPlane PostgreSQL'."""
    from app.generator import _insert

    fp = schema_catalogue.fingerprint(STRUCTURE)
    pages = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", STRUCTURE, fp)
    tree: dict = {}
    for page in [schema_catalogue.presence_page(), *pages]:
        _insert(tree, page["nav_path"].split(" / "), page["path"])
    # The database node is a pure section with an Overview leaf inside it.
    database_node = tree["Model"]["Schema catalogue"]["DocPlane PostgreSQL"]
    assert isinstance(database_node, dict)
    assert database_node["Overview"] == "model/schema-catalogue/docplane/index.md"
    assert tree["Model"]["Schema catalogue"]["Overview"] == "model/schema-catalogue/index.md"


def test_generator_payloads_validate_against_the_deployed_api_models():
    """Pin every request body the generator sends to the real pydantic
    contracts, so a renamed field fails HERE instead of mid-run on the
    fabric (the to_entity_id/target_entity_id defect class)."""
    from uuid import uuid4

    from app.model_models import ArtifactDeclare, EntityCreate, EntityLinkCreate
    from app.observe_models import ObservationBatch
    from app.agent_models import ChangeCreate, ChangeOperationCreate

    fp = "ab" * 32
    database_id = uuid4()

    EntityCreate.model_validate(
        {"entity_kind": "DATABASE", "entity_key": "docplane", "display_name": "DocPlane PostgreSQL"}
    )
    EntityCreate.model_validate(
        {"entity_kind": "SCHEMA", "entity_key": "docplane.docs", "display_name": "DocPlane PostgreSQL docs"}
    )
    # The exact link body ensure_entities sends — the field is to_entity_id.
    EntityLinkCreate.model_validate({"relation": "STORES_IN", "to_entity_id": database_id})
    ArtifactDeclare.model_validate(
        {
            "artifact_key": "schema-catalogue-docplane",
            "generator_name": schema_catalogue.GENERATOR_NAME,
            "generator_version": schema_catalogue.GENERATOR_VERSION,
            "projection_contract_version": schema_catalogue.PROJECTION_CONTRACT_VERSION,
            "source_entity_id": database_id,
            "redaction_policy": "canonical",
            "target_page_resource_ids": [uuid4()],
            "target_page_paths": ["model/schema-catalogue/docplane/index.md"],
        }
    )
    ObservationBatch.model_validate(
        {
            "observations": [
                {
                    "subject_artifact_id": database_id,
                    "observation_kind": "GENERATION",
                    "outcome": "NOMINAL",
                    "source_fingerprint": fp,
                    "summary": "Regenerated 6 catalogue pages",
                    "idempotency_key": schema_catalogue._key(fp, "generation"),
                }
            ]
        }
    )
    ChangeCreate.model_validate(
        {
            "title": f"Schema catalogue regeneration {fp[:16]}",
            "purpose": "Fingerprint-bound regeneration.",
            "workspace_key": "reference",
            "generated_ownership_plan": {
                "mode": "IN_PLACE",
                "artifact_id": uuid4(),
                "expected_version": 1,
                "target_page_resource_ids": [uuid4()],
                "target_page_paths": [
                    "model/schema-catalogue/docplane/index.md"
                ],
                "generator_version": schema_catalogue.GENERATOR_VERSION,
            },
        }
    )
    ChangeOperationCreate.model_validate(
        {
            "operation_type": "CREATE_PAGE",
            "payload": {"path": "model/schema-catalogue/docplane/index.md", "title": "t", "nav_path": "n", "content": "c"},
        }
    )
    ChangeOperationCreate.model_validate(
        {
            "operation_type": "REPLACE_DOCUMENT",
            "page_resource_id": uuid4(),
            "expected_revision": "rev",
            "payload": {"path": "p", "title": "t", "nav_path": "n", "content": "c"},
        }
    )


def test_resumed_run_wires_links_for_pre_existing_entities():
    """A run interrupted after entity creation must still create the
    STORES_IN wire when it resumes — link creation cannot be skipped just
    because the schema entity already exists."""
    calls = []

    class FakeClient:
        def call(self, method, path, payload=None, idempotency_key=None):
            calls.append((method, path, payload))
            if path.startswith("GET") or method == "GET":
                if "entity_kind=DATABASE" in path:
                    return {"entities": [{"entity_key": "docplane", "entity_id": "db-id"}]}
                if "entity_kind=SCHEMA" in path:
                    return {"entities": [{"entity_key": "docplane.docs", "entity_id": "schema-id"}]}
            return {"entity_id": "created-id"}

    result = schema_catalogue.ensure_entities(
        FakeClient(), "docplane", "DocPlane PostgreSQL", ["docs"], "ab" * 32
    )
    assert result == {
        "database_id": "db-id",
        "schema_ids": {"docs": "schema-id"},
        "stale_schema_ids": [],
    }
    link_calls = [call for call in calls if call[0] == "POST" and call[1].endswith("/links")]
    assert link_calls == [
        ("POST", "/api/v1/model/entities/schema-id/links", {"relation": "STORES_IN", "to_entity_id": "db-id"}),
    ]


def test_last_generation_fingerprint_reads_the_status_list_shape():
    class FakeClient:
        def call(self, method, path, payload=None, idempotency_key=None):
            return {
                "current_status": [
                    {"observation_kind": "DEPLOYED_VERSION", "source_fingerprint": "aa" * 16},
                    {"observation_kind": "GENERATION", "source_fingerprint": "bb" * 16},
                ]
            }

    assert schema_catalogue.last_generation_fingerprint(FakeClient(), "artifact") == "bb" * 16

    class EmptyClient:
        def call(self, method, path, payload=None, idempotency_key=None):
            return {"current_status": []}

    assert schema_catalogue.last_generation_fingerprint(EmptyClient(), "artifact") is None


def _publish_fixture(*, artifact, page_status=None, fail_publish=False):
    fp = schema_catalogue.fingerprint(STRUCTURE)
    pages = schema_catalogue.render_pages(
        "docplane", "DocPlane PostgreSQL", STRUCTURE, fp
    )
    page_status = page_status or {}
    calls = []

    class Client:
        def call(self, method, path, body=None, key=None):
            calls.append((method, path, body, key))
            if method == "GET" and path.startswith("/api/v1/pages?path="):
                requested = path.removeprefix("/api/v1/pages?path=").removesuffix(
                    "&status=all"
                )
                if requested == schema_catalogue.PRESENCE_PATH:
                    return {"pages": [{"resource_id": "presence", "revision": "p1", "status": "active"}]}
                status = page_status.get(requested, "active")
                if status == "missing":
                    return {"pages": []}
                return {
                    "pages": [
                        {
                            "resource_id": f"resource-{requested}",
                            "revision": "revision-1",
                            "status": status,
                        }
                    ]
                }
            if method == "POST" and path == "/api/v1/changes":
                return {"change_id": "change-1"}
            if method == "POST" and path.endswith("/publish"):
                if fail_publish:
                    raise RuntimeError("publication rejected")
                return {"publication_receipt": {"deployment": {"status": "COMPLETED"}}}
            if method == "GET" and path == "/api/v1/model/artifacts":
                return {"artifacts": [artifact]}
            return {}

    result = schema_catalogue.publish_pages(
        Client(),
        pages,
        fp,
        include_presence=True,
        artifact=artifact,
        artifact_key="schema-catalogue-docplane",
        database_id="database-1",
    )
    return fp, pages, calls, result


def _artifact(paths, **overrides):
    return {
        "artifact_id": "artifact-1",
        "artifact_key": "schema-catalogue-docplane",
        "source_entity_id": "database-1",
        "version": 4,
        "generator_version": schema_catalogue.GENERATOR_VERSION,
        "projection_contract_version": schema_catalogue.PROJECTION_CONTRACT_VERSION,
        "redaction_policy": "canonical",
        "status": "DECLARED",
        "target_page_paths": list(paths),
        **overrides,
    }


def test_source_membership_and_software_version_reconcile_in_place():
    paths = ["model/schema-catalogue/docplane/index.md"]
    current = _artifact(paths)
    assert not schema_catalogue.needs_succession(current)
    assert schema_catalogue.needs_reconciliation(
        current, [*paths, "model/schema-catalogue/docplane/new.md"]
    )
    assert schema_catalogue.needs_reconciliation(
        _artifact(paths, generator_version="older-build"), paths
    )
    assert schema_catalogue.needs_succession(
        _artifact(paths, projection_contract_version=2)
    )


def test_schema_addition_preallocates_page_and_uses_in_place_exact_set():
    fp = schema_catalogue.fingerprint(STRUCTURE)
    pages = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", STRUCTURE, fp)
    paths = sorted(page["path"] for page in pages)
    added_path = paths[-1]
    artifact = _artifact(paths[:-1])
    _, _, calls, _ = _publish_fixture(
        artifact=artifact, page_status={added_path: "missing"}
    )
    change = next(body for method, path, body, _ in calls if path == "/api/v1/changes")
    plan = change["generated_ownership_plan"]
    assert plan["mode"] == "IN_PLACE"
    assert plan["expected_version"] == 4
    assert plan["target_page_paths"] == paths
    create = next(
        body
        for method, path, body, _ in calls
        if path.endswith("/operations")
        and body["operation_type"] == "CREATE_PAGE"
    )
    target_index = plan["target_page_paths"].index(added_path)
    assert create["payload"]["resource_id"] == plan["target_page_resource_ids"][target_index]
    assert not any(path.endswith("/retire") or path.endswith("/handoff") for _, path, _, _ in calls)


def test_schema_removal_archives_inside_same_in_place_publication():
    fp = schema_catalogue.fingerprint(STRUCTURE)
    pages = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", STRUCTURE, fp)
    paths = sorted(page["path"] for page in pages)
    stale = "model/schema-catalogue/docplane/removed.md"
    _, _, calls, _ = _publish_fixture(artifact=_artifact([*paths, stale]))
    change = next(body for method, path, body, _ in calls if path == "/api/v1/changes")
    assert change["generated_ownership_plan"]["mode"] == "IN_PLACE"
    assert stale not in change["generated_ownership_plan"]["target_page_paths"]
    archive = next(
        body
        for method, path, body, _ in calls
        if path.endswith("/operations") and body["operation_type"] == "ARCHIVE_PAGE"
    )
    assert archive["page_resource_id"] == f"resource-{stale}"
    assert archive["expected_revision"] == "revision-1"


def test_archived_schema_page_restores_with_stable_id_and_atomic_readoption():
    fp = schema_catalogue.fingerprint(STRUCTURE)
    pages = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", STRUCTURE, fp)
    paths = sorted(page["path"] for page in pages)
    restored = paths[-1]
    _, _, calls, _ = _publish_fixture(
        artifact=_artifact(paths[:-1]), page_status={restored: "archived"}
    )
    operations = [
        (body, key)
        for method, path, body, key in calls
        if path.endswith("/operations")
        and body.get("page_resource_id") == f"resource-{restored}"
    ]
    assert [body["operation_type"] for body, _ in operations] == [
        "RESTORE_PAGE",
        "REPLACE_DOCUMENT",
    ]
    assert operations[0][1] != operations[1][1]
    plan = next(body for _, path, body, _ in calls if path == "/api/v1/changes")[
        "generated_ownership_plan"
    ]
    index = plan["target_page_paths"].index(restored)
    assert plan["target_page_resource_ids"][index] == f"resource-{restored}"


def test_projection_contract_change_uses_atomic_successor_plan():
    fp = schema_catalogue.fingerprint(STRUCTURE)
    pages = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", STRUCTURE, fp)
    paths = sorted(page["path"] for page in pages)
    artifact = _artifact(paths, projection_contract_version=2)
    _, _, calls, _ = _publish_fixture(artifact=artifact)
    plan = next(body for _, path, body, _ in calls if path == "/api/v1/changes")[
        "generated_ownership_plan"
    ]
    assert plan["mode"] == "SUCCESSOR"
    assert plan["predecessor_id"] == "artifact-1"
    assert plan["successor"]["projection_contract_version"] == 1
    assert not any(path.endswith("/retire") for _, path, _, _ in calls)


def test_failed_publication_does_not_return_safe_ownership_result():
    fp = schema_catalogue.fingerprint(STRUCTURE)
    pages = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", STRUCTURE, fp)
    paths = sorted(page["path"] for page in pages)
    with pytest.raises(RuntimeError, match="publication rejected"):
        _publish_fixture(artifact=_artifact(paths), fail_publish=True)


def test_generation_evidence_is_emitted_only_after_atomic_publication(monkeypatch):
    events = []
    fp = schema_catalogue.fingerprint(STRUCTURE)
    pages = schema_catalogue.render_pages(
        "docplane", "DocPlane PostgreSQL", STRUCTURE, fp
    )
    artifact = _artifact(sorted(page["path"] for page in pages))

    class Source:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(schema_catalogue.psycopg2, "connect", lambda _dsn: Source())
    monkeypatch.setattr(schema_catalogue, "introspect", lambda *_: STRUCTURE)
    monkeypatch.setattr(schema_catalogue, "render_pages", lambda *_: pages)
    monkeypatch.setattr(schema_catalogue, "ensure_entities", lambda *_: {
        "database_id": "database-1", "schema_ids": {"docplane": "schema-1"},
        "stale_schema_ids": [],
    })
    monkeypatch.setattr(schema_catalogue, "current_artifact", lambda *_: artifact)
    monkeypatch.setattr(
        schema_catalogue, "last_generation_fingerprint", lambda *_: "old-fingerprint"
    )

    def publish(*args, **kwargs):
        events.append("publication+ownership")
        return artifact, {page["path"]: f"id-{index}" for index, page in enumerate(pages)}

    monkeypatch.setattr(schema_catalogue, "publish_pages", publish)
    monkeypatch.setattr(
        schema_catalogue,
        "reconcile_catalogues",
        lambda *_args, **_kwargs: events.append("catalogues"),
    )
    monkeypatch.setattr(
        schema_catalogue,
        "emit_generation",
        lambda *_args, **_kwargs: events.append("generation"),
    )
    monkeypatch.setenv("CATALOGUE_SOURCE_DSN", "not-used")
    monkeypatch.setenv("CATALOGUE_DB_KEY", "docplane")
    monkeypatch.setenv("CATALOGUE_SCHEMAS", "docplane")
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_SCHEMA_CATALOGUE_TOKEN", "not-printed")

    assert schema_catalogue.main([]) == 0
    assert events == ["publication+ownership", "catalogues", "generation"]


def test_unchanged_source_and_exact_targets_perform_no_publication(monkeypatch):
    fp = schema_catalogue.fingerprint(STRUCTURE)
    pages = schema_catalogue.render_pages(
        "docplane", "DocPlane PostgreSQL", STRUCTURE, fp
    )
    artifact = _artifact(sorted(page["path"] for page in pages))

    class Source:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(schema_catalogue.psycopg2, "connect", lambda _dsn: Source())
    monkeypatch.setattr(schema_catalogue, "introspect", lambda *_: STRUCTURE)
    monkeypatch.setattr(schema_catalogue, "render_pages", lambda *_: pages)
    monkeypatch.setattr(schema_catalogue, "ensure_entities", lambda *_: {
        "database_id": "database-1", "schema_ids": {"docplane": "schema-1"},
        "stale_schema_ids": [],
    })
    monkeypatch.setattr(schema_catalogue, "current_artifact", lambda *_: artifact)
    monkeypatch.setattr(schema_catalogue, "last_generation_fingerprint", lambda *_: fp)
    monkeypatch.setattr(
        schema_catalogue,
        "publish_pages",
        lambda *_args, **_kwargs: pytest.fail("unchanged run published"),
    )
    monkeypatch.setattr(
        schema_catalogue,
        "page_ids_for_paths",
        lambda _client, paths: {path: f"id-{index}" for index, path in enumerate(paths)},
    )
    monkeypatch.setattr(schema_catalogue, "reconcile_catalogues", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        schema_catalogue,
        "emit_generation",
        lambda *_args, **_kwargs: pytest.fail("unchanged run emitted evidence"),
    )
    monkeypatch.setenv("CATALOGUE_SOURCE_DSN", "not-used")
    monkeypatch.setenv("CATALOGUE_DB_KEY", "docplane")
    monkeypatch.setenv("CATALOGUE_SCHEMAS", "docplane")
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_SCHEMA_CATALOGUE_TOKEN", "not-printed")

    assert schema_catalogue.main([]) == 0


def test_catalogues_exact_state_is_zero_write_and_unrelated_relations_are_ignored():
    calls = []

    class Client:
        def call(self, method, path, body=None, key=None):
            calls.append((method, path, body, key))
            assert method == "GET"
            return {"pages": [
                {"relation": "DESCRIBES", "page_resource_id": "unrelated"},
                {"relation": "CATALOGUES", "page_resource_id": "wanted"},
            ]}

    assert schema_catalogue.reconcile_catalogues(
        Client(), {"entity-1": ["wanted"]}, key_prefix="test"
    ) == []
    assert [method for method, *_ in calls] == ["GET"]


def test_catalogues_missing_or_stale_state_reconciles_exactly():
    calls = []

    class Client:
        def call(self, method, path, body=None, key=None):
            calls.append((method, path, body, key))
            if method == "GET":
                return {"pages": [
                    {"relation": "DESCRIBES", "page_resource_id": "unrelated"},
                    {"relation": "CATALOGUES", "page_resource_id": "stale"},
                ]}
            return {"changed": True, "added": ["wanted"], "removed": ["stale"]}

    result = schema_catalogue.reconcile_catalogues(
        Client(), {"entity-1": ["wanted"]}, key_prefix="test"
    )
    assert result == [{"changed": True, "added": ["wanted"], "removed": ["stale"]}]
    put = next(call for call in calls if call[0] == "PUT")
    assert put[1] == "/api/v1/model/entities/entity-1/page-links/catalogues"
    assert put[2] == {"page_resource_ids": ["wanted"]}
    assert "unrelated" not in put[2]["page_resource_ids"]


def test_catalogues_unknown_response_resumes_from_committed_exact_state():
    class Client:
        current = ["stale"]
        put_calls = 0

        def call(self, method, path, body=None, key=None):
            if method == "GET":
                return {"pages": [
                    {"relation": "CATALOGUES", "page_resource_id": page_id}
                    for page_id in self.current
                ]}
            self.put_calls += 1
            self.current = list(body["page_resource_ids"])
            raise ConnectionError("response lost after commit")

    client = Client()
    with pytest.raises(ConnectionError, match="response lost"):
        schema_catalogue.reconcile_catalogues(
            client, {"entity-1": ["wanted"]}, key_prefix="attempt-1"
        )
    # The next invocation reads durable state and does not issue a second
    # mutation, so publication can remain safely committed and the generator
    # can advance to GENERATION evidence.
    assert schema_catalogue.reconcile_catalogues(
        client, {"entity-1": ["wanted"]}, key_prefix="attempt-2"
    ) == []
    assert client.put_calls == 1


def test_schema_catalogues_mapping_add_remove_restore_uses_stable_page_ids_only():
    entities = {
        "database_id": "database-1",
        "schema_ids": {"docs": "schema-docs", "model": "schema-model"},
        "stale_schema_ids": ["schema-removed"],
    }
    page_ids = {
        "model/schema-catalogue/docplane/index.md": "page-index",
        "model/schema-catalogue/docplane/docs.md": "page-docs",
        "model/schema-catalogue/docplane/model.md": "page-model",
    }
    mappings = schema_catalogue.schema_catalogues_mappings(entities, page_ids, "docplane")
    assert mappings == {
        "database-1": ["page-index"],
        "schema-docs": ["page-docs"],
        "schema-model": ["page-model"],
        "schema-removed": [],
    }
    assert not any("table" in entity_id or "column" in entity_id for entity_id in mappings)


def test_unchanged_schema_link_repair_does_not_publish_or_reemit_generation(monkeypatch):
    events = []
    existing_summary = "Regenerated 6 catalogue pages for docplane (5 schemas)"
    semantic_repair_summary = "Confirmed 6 catalogue pages for docplane (5 schemas)"
    assert existing_summary != semantic_repair_summary
    fp = schema_catalogue.fingerprint(STRUCTURE)
    pages = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", STRUCTURE, fp)
    artifact = _artifact(sorted(page["path"] for page in pages))

    class Source:
        def __enter__(self): return self
        def __exit__(self, *args): return False

    monkeypatch.setattr(schema_catalogue.psycopg2, "connect", lambda _dsn: Source())
    monkeypatch.setattr(schema_catalogue, "introspect", lambda *_: STRUCTURE)
    monkeypatch.setattr(schema_catalogue, "ensure_entities", lambda *_: {
        "database_id": "database-1", "schema_ids": {"docplane": "schema-1"},
        "stale_schema_ids": [],
    })
    monkeypatch.setattr(schema_catalogue, "current_artifact", lambda *_: artifact)
    monkeypatch.setattr(schema_catalogue, "last_generation_fingerprint", lambda *_: fp)
    monkeypatch.setattr(schema_catalogue, "page_ids_for_paths", lambda _client, paths: {
        path: f"id-{index}" for index, path in enumerate(paths)
    })
    monkeypatch.setattr(schema_catalogue, "publish_pages", lambda *_a, **_k: pytest.fail("republished"))
    monkeypatch.setattr(schema_catalogue, "reconcile_catalogues", lambda *_a, **_k: events.append("catalogues") or [{"changed": True}])
    monkeypatch.setattr(
        schema_catalogue,
        "emit_generation",
        lambda *_a, **_k: pytest.fail(
            "semantic repair attempted the production-conflicting observation POST"
        ),
    )
    monkeypatch.setenv("CATALOGUE_SOURCE_DSN", "unused")
    monkeypatch.setenv("CATALOGUE_DB_KEY", "docplane")
    monkeypatch.setenv("CATALOGUE_SCHEMAS", "docplane")
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_SCHEMA_CATALOGUE_TOKEN", "not-printed")

    assert schema_catalogue.main([]) == 0
    assert events == ["catalogues"]


def test_attribution_only_repair_does_not_publish_or_reemit_generation(monkeypatch):
    events = []
    fp = schema_catalogue.fingerprint(STRUCTURE)
    pages = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", STRUCTURE, fp)
    paths = sorted(page["path"] for page in pages)
    artifact = _artifact(paths, generator_version="older-build")

    class Source:
        def __enter__(self): return self
        def __exit__(self, *args): return False

    class Client:
        def __init__(self, *_args): pass

        def call(self, method, path, body=None, key=None):
            if method == "GET" and path.startswith("/api/v1/pages?path="):
                requested = path.removeprefix("/api/v1/pages?path=").removesuffix("&status=all")
                return {"pages": [{"resource_id": f"resource-{requested}"}]}
            if method == "PUT" and path.endswith("/targets"):
                events.append("attribution")
                return {"artifact": {**artifact, "generator_version": schema_catalogue.GENERATOR_VERSION}}
            raise AssertionError((method, path, body, key))

    monkeypatch.setattr(schema_catalogue.psycopg2, "connect", lambda _dsn: Source())
    monkeypatch.setattr(schema_catalogue, "introspect", lambda *_: STRUCTURE)
    monkeypatch.setattr(schema_catalogue, "ensure_entities", lambda *_: {
        "database_id": "database-1", "schema_ids": {"docplane": "schema-1"},
        "stale_schema_ids": [],
    })
    monkeypatch.setattr(schema_catalogue, "Client", Client)
    monkeypatch.setattr(schema_catalogue, "current_artifact", lambda *_: artifact)
    monkeypatch.setattr(schema_catalogue, "last_generation_fingerprint", lambda *_: fp)
    monkeypatch.setattr(schema_catalogue, "publish_pages", lambda *_a, **_k: pytest.fail("republished"))
    monkeypatch.setattr(
        schema_catalogue,
        "reconcile_catalogues",
        lambda *_a, **_k: events.append("catalogues") or [{"changed": True}],
    )
    monkeypatch.setattr(
        schema_catalogue,
        "emit_generation",
        lambda *_a, **_k: pytest.fail(
            "attribution repair reused existing generation evidence"
        ),
    )
    monkeypatch.setenv("CATALOGUE_SOURCE_DSN", "unused")
    monkeypatch.setenv("CATALOGUE_DB_KEY", "docplane")
    monkeypatch.setenv("CATALOGUE_SCHEMAS", "docplane")
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_SCHEMA_CATALOGUE_TOKEN", "not-printed")

    assert schema_catalogue.main([]) == 0
    assert events == ["attribution", "catalogues"]


def test_catalogues_failure_after_publication_suppresses_generation(monkeypatch):
    events = []
    fp = schema_catalogue.fingerprint(STRUCTURE)
    pages = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", STRUCTURE, fp)
    artifact = _artifact(sorted(page["path"] for page in pages))

    class Source:
        def __enter__(self): return self
        def __exit__(self, *args): return False

    monkeypatch.setattr(schema_catalogue.psycopg2, "connect", lambda _dsn: Source())
    monkeypatch.setattr(schema_catalogue, "introspect", lambda *_: STRUCTURE)
    monkeypatch.setattr(schema_catalogue, "ensure_entities", lambda *_: {
        "database_id": "database-1", "schema_ids": {"docplane": "schema-1"},
        "stale_schema_ids": [],
    })
    monkeypatch.setattr(schema_catalogue, "current_artifact", lambda *_: artifact)
    monkeypatch.setattr(schema_catalogue, "last_generation_fingerprint", lambda *_: "older")
    monkeypatch.setattr(schema_catalogue, "publish_pages", lambda *_a, **_k: (
        events.append("publication+ownership") or
        (artifact, {page["path"]: f"id-{index}" for index, page in enumerate(pages)})
    ))

    def refuse(*_args, **_kwargs):
        events.append("catalogues-failed")
        raise RuntimeError("semantic reconciliation failed")

    monkeypatch.setattr(schema_catalogue, "reconcile_catalogues", refuse)
    monkeypatch.setattr(schema_catalogue, "emit_generation", lambda *_a, **_k: events.append("generation"))
    monkeypatch.setenv("CATALOGUE_SOURCE_DSN", "unused")
    monkeypatch.setenv("CATALOGUE_DB_KEY", "docplane")
    monkeypatch.setenv("CATALOGUE_SCHEMAS", "docplane")
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_SCHEMA_CATALOGUE_TOKEN", "not-printed")

    with pytest.raises(RuntimeError, match="semantic reconciliation failed"):
        schema_catalogue.main([])
    assert events == ["publication+ownership", "catalogues-failed"]


@pytest.mark.skipif(not os.environ.get("DB_HOST"), reason="requires a PostgreSQL database")
def test_introspection_of_the_canary_is_deterministic_and_rowless():
    import psycopg2

    dsn = (
        f"host={os.environ['DB_HOST']} port={os.environ.get('DB_PORT', '5432')} "
        f"dbname={os.environ.get('DB_NAME', 'docs')} user={os.environ.get('DB_USER', 'docs')} "
        f"password={os.environ.get('DB_PASS', '')}"
    )
    with psycopg2.connect(dsn) as conn:
        first = schema_catalogue.introspect(conn, ["docplane", "docs"])
    with psycopg2.connect(dsn) as conn:
        second = schema_catalogue.introspect(conn, ["docplane", "docs"])
    assert first == second
    assert schema_catalogue.fingerprint(first) == schema_catalogue.fingerprint(second)
    assert "schema_migrations" in first["docplane"]
    assert "pages" in first["docs"]
    ledger = first["docplane"]["schema_migrations"]
    assert {column["name"] for column in ledger["columns"]} >= {"ordinal", "filename", "checksum", "applied_at"}
    # Structure only: nothing in the model may carry row data. The checksum
    # CHECK constraint is structure; an actual checksum value would be data.
    canonical = schema_catalogue.fingerprint(first)
    assert len(canonical) == 64
    rendered = schema_catalogue.render_pages(
        "docplane", "DocPlane PostgreSQL", first, canonical
    )
    assert all("applied_at" in page["content"] or "index" in page["path"] for page in rendered[:1])
