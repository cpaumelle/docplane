from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI
from pydantic import ValidationError

from app import agent_shortcuts_api, publication, system_api
from app.agent_api import router as agent_router
from app.agent_auth import Principal
from app.agent_models import ChangeOperationCreate, PageReplaceRequest
from app.agent_shortcuts_api import router as agent_shortcuts_router
from app.generator import NavConflict, build_nav

ROOT = Path(__file__).resolve().parents[2]


def _contract_app() -> FastAPI:
    app = FastAPI()
    app.include_router(agent_router)
    app.include_router(agent_shortcuts_router)
    return app


def test_public_api_has_direct_publish_shortcuts_and_no_review_gate():
    paths = {route.path for route in _contract_app().routes}
    assert "/api/v1/changes/{change_id}/publish" in paths
    assert "/api/v1/changes/{change_id}/validate" in paths
    assert "/api/v1/pages/{resource_id}/replace" in paths
    assert "/api/v1/changes/{change_id}/abandon" in paths
    assert not any(path.endswith("/submit") for path in paths)
    assert not any(path.endswith("/approve") for path in paths)
    assert not any(path.endswith("/review") for path in paths)
    assert "/api/v1/bootstrap/principals" in paths
    assert not any("/admin/" in path for path in paths)


def test_openapi_declares_individual_bearer_authentication():
    schema = _contract_app().openapi()
    bearer = schema["components"]["securitySchemes"]["DocPlaneBearer"]
    assert bearer["type"] == "http"
    assert bearer["scheme"] == "bearer"
    protected = schema["paths"]["/api/v1/pages"]["get"]
    assert {"DocPlaneBearer": []} in protected["security"]


def test_shortcut_mutations_declare_required_idempotency_header():
    schema = _contract_app().openapi()
    for path in (
        "/api/v1/pages/{resource_id}/replace",
        "/api/v1/changes/{change_id}/abandon",
    ):
        parameters = schema["paths"][path]["post"]["parameters"]
        header = next(item for item in parameters if item["name"] == "Idempotency-Key")
        assert header["in"] == "header"
        assert header["required"] is True


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


def test_replace_shortcut_uses_canonical_change_pipeline(monkeypatch):
    resource_id = UUID("11111111-1111-1111-1111-111111111111")
    change_id = "22222222-2222-2222-2222-222222222222"
    principal = Principal(
        principal_id="33333333-3333-3333-3333-333333333333",
        principal_kind="AGENT",
        display_name="contract-test-agent",
        token_id="44444444-4444-4444-4444-444444444444",
    )
    calls: list[tuple] = []

    class Cursor:
        def execute(self, query, params):
            calls.append(("lookup", " ".join(query.split()), params))

        def fetchone(self):
            return ("reference/example.md", "reference")

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(agent_shortcuts_api, "get_conn", lambda: Connection())

    def fake_create(request, idempotency_key, principal):
        calls.append(("create", request, idempotency_key, principal))
        return {"change_id": change_id, "status": "DRAFT", "operations": []}

    def fake_add(change_uuid, request, idempotency_key, principal):
        calls.append(("operation", change_uuid, request, idempotency_key, principal))
        return {"change_id": change_id}

    def fake_validate(change_uuid, principal):
        calls.append(("validate", change_uuid, principal))
        return {"status": "VALIDATED"}

    def fake_publish(change_uuid, principal):
        calls.append(("publish", change_uuid, principal))
        return {"change_id": change_id, "status": "PUBLISHED"}

    monkeypatch.setattr(agent_shortcuts_api, "create_change", fake_create)
    monkeypatch.setattr(agent_shortcuts_api, "add_operation", fake_add)
    monkeypatch.setattr(agent_shortcuts_api, "validate_change", fake_validate)
    monkeypatch.setattr(agent_shortcuts_api, "publish_change", fake_publish)

    result = agent_shortcuts_api.replace_page(
        resource_id,
        PageReplaceRequest(
            expected_revision="revision-7",
            content="# Replacement\n",
            purpose="Correct the runbook",
        ),
        "outer-idempotency-key",
        principal,
    )

    assert result["status"] == "PUBLISHED"
    assert [call[0] for call in calls] == ["lookup", "create", "operation", "validate", "publish"]
    create_request = calls[1][1]
    assert create_request.workspace_key == "reference"
    assert create_request.purpose == "Correct the runbook"
    operation_request = calls[2][2]
    assert operation_request.operation_type == "REPLACE_DOCUMENT"
    assert operation_request.page_resource_id == resource_id
    assert operation_request.expected_revision == "revision-7"
    assert operation_request.payload == {"content": "# Replacement\n"}
    assert calls[1][2] != "outer-idempotency-key"
    assert calls[2][3] != "outer-idempotency-key"
    assert calls[1][2] != calls[2][3]


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
            return cursor

    cursor = Cursor()
    monkeypatch.setattr(system_api, "get_conn", lambda: Connection())
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


def test_move_page_repairs_backlinks_and_reports_impact(monkeypatch):
    pages = [
        {"resource_id": "11111111-1111-1111-1111-111111111111", "path": "reference/example.md", "title": "Example", "nav_path": "Reference/Example", "nav_order": 0, "content": "# Example\n", "revision": "r1", "version": 1, "status": "active"},
        {"resource_id": "22222222-2222-2222-2222-222222222222", "path": "reference/source.md", "title": "Source", "nav_path": "Reference/Source", "nav_order": 10, "content": "# Source\n\n[Example](example.md)\n", "revision": "r2", "version": 1, "status": "active"},
    ]
    monkeypatch.setattr(publication, "_load_pages", lambda conn, for_update=False: pages)
    monkeypatch.setattr(publication, "_load_redirects", lambda conn: {})
    monkeypatch.setattr(publication, "_load_sections", lambda conn: {})
    monkeypatch.setattr(publication, "state_identity", lambda pages, sections, redirects: "base")
    evaluation = publication.evaluate_change(None, {"workspace_key": "reference"}, [{
        "operation_id": "33333333-3333-3333-3333-333333333333", "sequence": 0,
        "operation_type": "MOVE_PAGE", "page_resource_id": pages[0]["resource_id"],
        "expected_revision": "r1", "expected_section_hash": None,
        "payload": {"to_path": "guides/example.md", "to_nav_path": "Guides/Example", "create_redirect": True},
    }])
    assert evaluation["passed"] is True
    source = next(page for page in evaluation["pages"] if page["path"] == "reference/source.md")
    assert "[Example](../guides/example.md)" in source["content"]
    assert source["resource_id"] in evaluation["touched"]
    assert evaluation["redirects"] == {"reference/example.md": "guides/example.md"}
    assert evaluation["operations"][0]["link_impacts"][0]["rewrites"][0]["resolved_old"] == "reference/example.md"


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
