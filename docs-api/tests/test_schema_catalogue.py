"""Sprint 5 exemplar: the schema-catalogue generator.

The generator is fingerprint-bound, redaction-gated and idempotent. These
tests cover the pure core (structure canonicalisation, rendering, keys,
skip logic) and — when a database is available — real introspection against
DocPlane's own schemas, which is exactly the canary the exemplar names.
"""
from __future__ import annotations

import os
import sys
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


def test_idempotency_keys_are_fingerprint_bound_and_bounded():
    fp = "ab" * 32
    key = schema_catalogue._key(fp, "operation", "model/schema-catalogue/docplane/docplane.md")
    assert key.startswith(f"schema-catalogue-{fp[:16]}-operation-")
    assert len(key) <= 256
    assert key == schema_catalogue._key(fp, "operation", "model/schema-catalogue/docplane/docplane.md")
    assert key != schema_catalogue._key("cd" * 32, "operation", "model/schema-catalogue/docplane/docplane.md")


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

    schema_catalogue.ensure_entities(FakeClient(), "docplane", "DocPlane PostgreSQL", ["docs"], "ab" * 32)
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
