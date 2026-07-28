from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from pydantic import ValidationError

from app import publication, system_api
from app.agent_api import router as agent_router
from app.agent_models import ChangeOperationCreate
from app.generator import NavConflict, build_nav

ROOT = Path(__file__).resolve().parents[2]


def test_public_api_has_direct_publish_and_no_review_gate():
    app = FastAPI()
    app.include_router(agent_router)
    paths = {route.path for route in app.routes}
    assert "/api/v1/changes/{change_id}/publish" in paths
    assert "/api/v1/changes/{change_id}/validate" in paths
    assert not any(path.endswith("/submit") for path in paths)
    assert not any(path.endswith("/approve") for path in paths)
    assert not any(path.endswith("/review") for path in paths)
    assert "/api/v1/bootstrap/principals" in paths
    assert not any("/admin/" in path for path in paths)


def test_openapi_declares_individual_bearer_authentication():
    app = FastAPI()
    app.include_router(agent_router)
    schema = app.openapi()
    bearer = schema["components"]["securitySchemes"]["DocPlaneBearer"]
    assert bearer["type"] == "http"
    assert bearer["scheme"] == "bearer"
    protected = schema["paths"]["/api/v1/pages"]["get"]
    assert {"DocPlaneBearer": []} in protected["security"]


def test_bound_page_operations_require_exact_revision():
    try:
        ChangeOperationCreate(
            operation_type="REPLACE_DOCUMENT",
            page_resource_id="11111111-1111-1111-1111-111111111111",
            payload={"content": "# Corrected"},
        )
    except ValidationError as exc:
        assert "expected_revision" in str(exc)
    else:
        raise AssertionError("revision-free page mutation was accepted")


def test_operation_vocabulary_is_machine_readable():
    schema = ChangeOperationCreate.model_json_schema()
    assert schema["properties"]["operation_type"]["enum"] == [
        "ADD_REDIRECT",
        "ARCHIVE_PAGE",
        "CREATE_PAGE",
        "INSERT_AFTER_HEADING",
        "INSERT_BEFORE_HEADING",
        "MOVE_PAGE",
        "PATCH_METADATA",
        "REMOVE_REDIRECT",
        "REORDER_SECTIONS",
        "REPARENT_NAV",
        "REPLACE_DOCUMENT",
        "REPLACE_SECTION",
        "RESTORE_PAGE",
    ]


def _evaluate(monkeypatch, operation):
    page = {
        "resource_id": "11111111-1111-1111-1111-111111111111",
        "path": "reference/example.md",
        "title": "Example",
        "nav_path": "Reference/Example",
        "content": "# Current\n",
        "revision": "current-revision",
        "version": 7,
        "status": "active",
    }
    monkeypatch.setattr(publication, "_load_pages", lambda conn, for_update=False: [page])
    monkeypatch.setattr(publication, "_load_redirects", lambda conn: {})
    monkeypatch.setattr(publication, "_load_sections", lambda conn: {})
    monkeypatch.setattr(publication, "state_identity", lambda pages, sections, redirects: "base")
    monkeypatch.setattr(publication.generator, "build_nav", lambda pages, section_order: [])
    evaluation = publication.evaluate_change(
        None,
        {"workspace_key": "reference", "base_state_identity": None},
        [operation],
    )
    return evaluation, page


def test_stale_revision_is_a_change_level_failure(monkeypatch):
    evaluation, page = _evaluate(
        monkeypatch,
        {
            "operation_id": "22222222-2222-2222-2222-222222222222",
            "sequence": 0,
            "operation_type": "REPLACE_DOCUMENT",
            "page_resource_id": "11111111-1111-1111-1111-111111111111",
            "expected_revision": "stale-revision",
            "expected_section_hash": None,
            "payload": {"content": "# Lost write\n"},
        },
    )
    assert evaluation["passed"] is False
    assert evaluation["operations_accepted"] == 0
    assert evaluation["operations"][0]["accepted"] is False
    assert evaluation["errors"] == evaluation["operations"][0]["errors"]
    error = evaluation["errors"][0]
    assert error["code"] == "PAGE_REVISION_STALE"
    assert error["current"] == page["revision"]
    assert error["current_version"] == page["version"]
    assert error["current_content_sha256"] == hashlib.sha256(page["content"].encode()).hexdigest()
    assert "rebase" in error["remedy"].lower()


def test_unsupported_operation_is_a_change_level_failure(monkeypatch):
    evaluation, _ = _evaluate(
        monkeypatch,
        {
            "operation_id": "33333333-3333-3333-3333-333333333333",
            "sequence": 0,
            "operation_type": "UNKNOWN_OPERATION",
            "page_resource_id": None,
            "expected_revision": None,
            "expected_section_hash": None,
            "payload": {},
        },
    )
    assert evaluation["passed"] is False
    assert evaluation["operations_accepted"] == 0
    assert evaluation["errors"][0]["code"] == "OPERATION_UNSUPPORTED"


def test_health_labels_total_active_and_archived_counts(monkeypatch):
    class Cursor:
        def __init__(self):
            self.rows = iter([(3,), (937, 508, 429)])

        def execute(self, _query):
            return None

        def fetchone(self):
            return next(self.rows)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    # One cursor is used for both queries in production.
    cursor = Cursor()

    class SharedCursorConnection(Connection):
        def cursor(self):
            return cursor

    monkeypatch.setattr(system_api, "get_conn", lambda: SharedCursorConnection())
    monkeypatch.setattr(system_api, "certification_status", lambda: {"state": "CURRENT"})
    result = system_api.health()
    assert result["pages"] == 937
    assert result["page_counts"] == {"total": 937, "active": 508, "archived": 429}


def test_nav_validation_rejects_leaf_section_collision():
    pages = [
        {"path": "a.md", "nav_path": "Reference/Service", "content": "# A"},
        {"path": "b.md", "nav_path": "Reference/Service/Runbook", "content": "# B"},
    ]
    try:
        build_nav(pages)
    except NavConflict as exc:
        assert exc.kind == "leaf-vs-section"
    else:
        raise AssertionError("navigation collision was accepted")


def test_genesis_contains_only_contributor_publication_model():
    sql = (ROOT / "db/migrations/000_docplane_genesis.sql").read_text()
    assert "workspace_memberships" not in sql
    assert "reorganisation_reviews" not in sql
    assert "change_proposals" not in sql
    assert "SUBMITTED" not in sql
    assert "APPROVED" not in sql
    assert "scopes text[]" not in sql
    assert "docs.changes" in sql
    assert "PUBLISHED" in sql


def test_runtime_has_no_shared_key_or_legacy_direct_api():
    checked = [
        ROOT / "docker-compose.yml",
        ROOT / "docs-api/app",
        ROOT / "mcp",
    ]
    text = "\n".join(
        path.read_text(errors="ignore")
        for root in checked
        for path in ([root] if root.is_file() else root.rglob("*.py"))
    )
    assert "DOCS_API_KEY" not in text
    assert "X-API-Key" not in text
    assert "/api/agent-config" not in text
    assert "/api/docs/" not in text
