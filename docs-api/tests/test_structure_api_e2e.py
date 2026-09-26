"""Structured schema projection: custody writes and the agent read surface.

Initiative e004787b, decision D1. The projection written here is produced by
the REAL generator code path (schema_catalogue_projection.build_document /
build_provenance over a contract-2 structure), so this is the producer ->
consumer boundary test the generated-artifact charter (§14) requires: the
generator's actual output is passed through the API's actual validation.
"""
from __future__ import annotations

import copy
import hashlib
import os
import sys
import uuid
from pathlib import Path

import pytest

if not os.environ.get("DB_HOST"):
    pytest.skip("requires a PostgreSQL database (set DB_HOST etc.)", allow_module_level=True)

os.environ.setdefault("DOCPLANE_EVENT_CURSOR_SECRET", "structure-e2e-cursor-secret-0123456789abcdef")
os.environ.setdefault("DOCPLANE_BOOTSTRAP_TOKEN", "structure-e2e-bootstrap")

from fastapi.testclient import TestClient  # noqa: E402

from app.application import app  # noqa: E402
from app.db import get_conn  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import schema_catalogue_projection as projection  # noqa: E402
import schema_catalogue_source  # noqa: E402

RUN = uuid.uuid4().hex[:8]
DB_KEY = f"structure_e2e_{RUN}"
client = TestClient(app)


def _column(name, type_="integer", udt="int4", nullable=False, **extra):
    return {
        "name": name, "type": type_, "udt": udt, "array": False, "enum": None,
        "nullable": nullable, "default": None, "identity": None, "generated": None,
        "comment": None, **extra,
    }


def _table(columns, *, pk=("id",), fks=()):
    return {
        "kind": "table", "comment": None, "columns": columns,
        "primary_key": {"name": "pk", "columns": list(pk)} if pk else None,
        "foreign_keys": list(fks), "unique": [], "checks": [], "exclusions": [],
        "indexes": [{"name": "pk", "unique": True, "primary": True, "method": "btree",
                     "columns": list(pk), "include": [], "predicate": None, "definition": "CREATE UNIQUE INDEX pk"}],
    }


def _fk(name, column, schema, table):
    return {
        "name": name, "columns": [column],
        "references": {"schema": schema, "table": table, "columns": ["id"]},
        "on_update": "NO ACTION", "on_delete": "NO ACTION", "deferrable": False,
        "initially_deferred": False, "definition": f"FOREIGN KEY ({column}) REFERENCES {schema}.{table}(id)",
    }


# A miniature of the pilot: ccm.edges -> transit.edge_nodes -> transit.sites.
STRUCTURE = {
    "ccm": {
        "comment": None, "views": {}, "enums": {},
        "tables": {
            "edges": _table([_column("id"), _column("edge_node_id", nullable=True)],
                            fks=[_fk("ccm_edges_edge_node_id_fk", "edge_node_id", "transit", "edge_nodes")]),
            "wifi_radios": _table([_column("id"), _column("edge_id")],
                                  fks=[_fk("wifi_radios_edge_id_fkey", "edge_id", "ccm", "edges")]),
        },
    },
    "transit": {
        "comment": None, "enums": {},
        "views": {"v_nodes": {"kind": "view", "comment": None, "columns": [_column("id")], "definition": " SELECT 1", "indexes": []}},
        "tables": {
            "sites": _table([_column("id"), _column("name", "text", "text")]),
            "edge_nodes": _table([_column("id"), _column("site_id")],
                                 fks=[_fk("edge_nodes_site_id_fkey", "site_id", "transit", "sites")]),
            "policy_rules": _table([_column("id"), _column("site_id", nullable=True)],
                                   fks=[_fk("policy_rules_site_id_fkey", "site_id", "transit", "sites")]),
        },
    },
}


def _mint(kind: str) -> tuple[str, dict[str, str]]:
    token = f"dp_structure_e2e_{uuid.uuid4().hex}"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO docplane.principals (display_name, principal_kind) VALUES (%s, %s) RETURNING principal_id::text",
            (f"structure-e2e-{kind.lower()}-{RUN}-{uuid.uuid4().hex[:4]}", kind),
        )
        principal_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO docplane.api_tokens (principal_id, token_hash, token_prefix, description) VALUES (%s, %s, %s, 'structure e2e')",
            (principal_id, hashlib.sha256(token.encode()).hexdigest(), token[:12]),
        )
        conn.commit()
    return principal_id, {"Authorization": f"Bearer {token}"}


GENERATOR_ID, GENERATOR = _mint("AUTOMATION")
_, OTHER_AUTOMATION = _mint("AUTOMATION")
_, AGENT = _mint("AGENT")


def _headers(base: dict[str, str]) -> dict[str, str]:
    return {**base, "Idempotency-Key": str(uuid.uuid4())}


def _declare() -> str:
    entity = client.post(
        "/api/v1/model/entities",
        json={"entity_kind": "DATABASE", "entity_key": DB_KEY, "display_name": f"Structure E2E {RUN}"},
        headers=_headers(GENERATOR),
    )
    assert entity.status_code == 201, entity.text
    artifact = client.post(
        "/api/v1/model/artifacts",
        json={
            "artifact_key": f"schema-catalogue-{DB_KEY}",
            "generator_name": "docplane-schema-catalogue",
            "generator_version": "2.0.0",
            "projection_contract_version": 2,
            "source_entity_id": entity.json()["entity_id"],
        },
        headers=_headers(GENERATOR),
    )
    assert artifact.status_code == 201, artifact.text
    return artifact.json()["artifact_id"]


ARTIFACT_ID = _declare()


def _body(structure=STRUCTURE, *, config=None):
    """Exactly what schema_catalogue.publish_projection sends."""
    config = config or {"schemas": {"ccm": {"description": None, "viewpoints": [], "canonical": {
        "status": "NOT_AVAILABLE", "reason": "hand-applied; no genesis migrations", "source": None}}}}
    static = {"environment": "test", "source_identity": "structure-e2e"}
    fingerprint = schema_catalogue_source.fingerprint(structure)
    render_config_hash = projection.config_hash(config, static)
    document = projection.build_document(DB_KEY, f"Structure E2E {RUN}", structure, fingerprint, render_config_hash, 2)
    provenance = projection.build_provenance(
        config=config, structure=structure, static_provenance=static,
        source_metadata={"database_name": "e2e", "server_version": "16.0"},
        extracted_at="2026-09-26T00:00:00+00:00",
        generator={"name": "docplane-schema-catalogue", "version": "2.0.0", "projection_contract_version": 2},
    )
    return {
        "projection_kind": "SCHEMA_STRUCTURE", "projection_contract_version": 2,
        "source_fingerprint": fingerprint, "config_hash": render_config_hash,
        "document": document, "provenance": provenance,
    }


def _events() -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM docplane.events WHERE event_type = 'MODEL_ARTIFACT_PROJECTION_PUBLISHED' AND resource_id = %s",
            (ARTIFACT_ID,),
        )
        return int(cur.fetchone()[0])


def _put(body, headers=None):
    return client.put(f"/api/v1/model/artifacts/{ARTIFACT_ID}/projection", json=body, headers=headers or _headers(GENERATOR))


@pytest.fixture(scope="module", autouse=True)
def published():
    response = _put(_body())
    assert response.status_code == 200, response.text
    assert response.json()["changed"] is True
    return response.json()


def test_generator_output_passes_real_validation_and_is_idempotent(published):
    assert published["projection"]["projection_contract_version"] == 2
    before = _events()
    again = _put(_body())
    assert again.status_code == 200
    assert again.json()["changed"] is False
    assert again.json()["projection"]["version"] == published["projection"]["version"]
    assert _events() == before


def test_exact_replay_returns_the_receipt():
    headers = _headers(GENERATOR)
    first = _put(_body(), headers)
    second = _put(_body(), headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_fingerprint_must_be_derivable_from_the_document():
    body = _body()
    tampered = copy.deepcopy(body)
    tampered["document"]["schemas"]["ccm"]["tables"]["edges"]["columns"].append(_column("smuggled"))
    response = _put(tampered)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PROJECTION_FINGERPRINT_MISMATCH"


def test_only_the_declaring_generator_may_write():
    response = _put(_body(), _headers(OTHER_AUTOMATION))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "MODEL_ARTIFACT_PROJECTION_FORBIDDEN"
    agent = _put(_body(), _headers(AGENT))
    assert agent.status_code == 403


def test_secret_shaped_values_are_refused_never_sanitised():
    poisoned = copy.deepcopy(STRUCTURE)
    poisoned["transit"]["tables"]["sites"]["columns"][1]["default"] = "'AKIAIOSFODNN7REALKEY'"
    response = _put(_body(poisoned))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "PROJECTION_SECRET_SHAPED"
    assert "AKIAIOSFODNN7REALKEY" not in response.text


def test_document_must_name_the_artifact_source_database():
    body = _body()
    body["document"]["database"]["key"] = "some_other_db"
    response = _put(body)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PROJECTION_SOURCE_MISMATCH"


def test_structure_reads_are_granular_and_uri_addressed():
    listing = client.get("/api/v1/model/structure", headers=AGENT)
    assert listing.status_code == 200
    entry = next(item for item in listing.json()["databases"] if item["db_key"] == DB_KEY)
    assert entry["schemas"]["ccm"]["canonical_status"] == "NOT_AVAILABLE"
    assert entry["schemas"]["ccm"]["basis"] == "DEPLOYED_INTROSPECTION"
    assert entry["freshness"]["state"] == "NEVER_GENERATED"

    database = client.get(f"/api/v1/model/structure/{DB_KEY}", headers=AGENT).json()
    assert database["provenance"]["migration_ledger"]["status"] == "NOT_CAPTURED"
    assert database["schemas"]["transit"]["views"] == ["v_nodes"]
    assert database["freshness"]["served_matches_generation"] is False  # no GENERATION yet

    schema = client.get(f"/api/v1/model/structure/{DB_KEY}/transit", headers=AGENT).json()
    assert schema["tables"]["sites"]["referenced_by"] == 2
    assert schema["tables"]["edge_nodes"]["references"] == ["transit.sites"]

    table = client.get(f"/api/v1/model/structure/{DB_KEY}/ccm/edges", headers=AGENT).json()
    assert table["uri"] == f"docplane://model/database/{DB_KEY}/schema/ccm/table/edges"
    assert [edge["to_uri"] for edge in table["relations"]["outbound"]] == [
        f"docplane://model/database/{DB_KEY}/schema/transit/table/edge_nodes"
    ]
    assert table["relations"]["outbound"][0]["cross_schema"] is True
    assert [edge["from"]["table"] for edge in table["relations"]["inbound"]] == ["wifi_radios"]

    column = client.get(f"/api/v1/model/structure/{DB_KEY}/transit/sites/id", headers=AGENT).json()
    assert column["primary_key"] is True
    assert sorted(edge["from"]["table"] for edge in column["referenced_by"]) == ["edge_nodes", "policy_rules"]

    view = client.get(f"/api/v1/model/structure/{DB_KEY}/transit/v_nodes", headers=AGENT).json()
    assert view["relation_kind"] == "view"


def test_missing_objects_fail_with_actionable_codes():
    assert client.get("/api/v1/model/structure/no_such_db", headers=AGENT).json()["detail"]["code"] == "STRUCTURE_DATABASE_NOT_FOUND"
    assert client.get(f"/api/v1/model/structure/{DB_KEY}/nope", headers=AGENT).json()["detail"]["code"] == "STRUCTURE_SCHEMA_NOT_FOUND"
    assert client.get(f"/api/v1/model/structure/{DB_KEY}/ccm/nope", headers=AGENT).json()["detail"]["code"] == "STRUCTURE_TABLE_NOT_FOUND"
    missing = client.get(f"/api/v1/model/structure/{DB_KEY}/ccm/edges/nope", headers=AGENT).json()["detail"]
    assert missing["code"] == "STRUCTURE_COLUMN_NOT_FOUND" and missing["columns"] == ["id", "edge_node_id"]


def test_find_column_answers_which_tables_reference_site_id():
    found = client.get("/api/v1/model/structure-columns", params={"name": "site_id", "db_key": DB_KEY}, headers=AGENT).json()
    assert [(match["schema"], match["table"]) for match in found["matches"]] == [
        ("transit", "edge_nodes"), ("transit", "policy_rules"),
    ]
    assert all(match["foreign_keys"][0]["references"]["table"] == "sites" for match in found["matches"])
    contains = client.get("/api/v1/model/structure-columns", params={"name": "edge", "match": "contains", "db_key": DB_KEY}, headers=AGENT).json()
    assert {match["column"] for match in contains["matches"]} == {"edge_node_id", "edge_id"}


def test_relations_answer_neighbourhood_and_cross_schema_path():
    path = client.get(
        f"/api/v1/model/structure-relations/{DB_KEY}/ccm/wifi_radios",
        params={"to": "transit.sites"}, headers=AGENT,
    ).json()
    assert path["path"] == ["ccm.wifi_radios", "ccm.edges", "transit.edge_nodes", "transit.sites"]
    assert [edge["constraint"] for edge in path["edges"]] == [
        "wifi_radios_edge_id_fkey", "ccm_edges_edge_node_id_fk", "edge_nodes_site_id_fkey",
    ]
    around = client.get(f"/api/v1/model/structure-relations/{DB_KEY}/transit/sites", params={"depth": 2}, headers=AGENT).json()
    hops = {item["table"]: item["hops"] for item in around["neighbourhood"]["tables"]}
    assert hops == {"transit.sites": 0, "transit.edge_nodes": 1, "transit.policy_rules": 1, "ccm.edges": 2}
    none = client.get(
        f"/api/v1/model/structure-relations/{DB_KEY}/transit/sites", params={"to": "transit.v_nodes"}, headers=AGENT,
    ).json()
    assert none["path"] is None


def test_reads_require_a_contributor_bearer():
    assert client.get(f"/api/v1/model/structure/{DB_KEY}").status_code == 401
