from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from pydantic import ValidationError

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
