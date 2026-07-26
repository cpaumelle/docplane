from __future__ import annotations

import os
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_api import router
from app.db import get_conn


app = FastAPI()
app.include_router(router)
client = TestClient(app)


def _issue_agent() -> tuple[str, str]:
    os.environ["DOCPLANE_BOOTSTRAP_ADMIN_TOKEN"] = "test-bootstrap-token"
    response = client.post(
        "/api/v1/admin/principals",
        headers={"X-DocPlane-Bootstrap-Token": "test-bootstrap-token"},
        json={
            "principal_kind": "AGENT",
            "display_name": "integration-agent",
            "scopes": ["docs:read", "docs:propose"],
            "metadata": {"suite": "agent-api"},
        },
    )
    assert response.status_code == 201, response.text
    issued = response.json()
    return issued["principal_id"], issued["token"]


def _insert_page() -> dict:
    suffix = uuid.uuid4().hex[:10]
    path = f"services/agent-api-{suffix}.md"
    content = """# Agent API Test

**Lifecycle:** REFERENCE
<!-- lifecycle: REFERENCE -->

A bounded-read integration fixture.

## Overview {#overview}

Current behavior.

## Rollback {#rollback}

Restore the previous release.
"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO docs.pages (path, title, nav_path, content, updated_by)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING resource_id::text, revision
            """,
            (path, "Agent API Test", "Services/Agent API Test", content, "integration-test"),
        )
        resource_id, revision = cur.fetchone()
        conn.commit()
    return {
        "resource_id": resource_id,
        "revision": revision,
        "path": path,
        "content": content,
    }


def test_endpoint_first_agent_journey_is_identity_safe_and_proposal_only():
    capability = client.get("/.well-known/docplane.json")
    assert capability.status_code == 200
    assert capability.json()["authentication"]["named_principals"] is True
    assert capability.json()["capabilities"]["proposal_merge"] is False
    assert "token" not in capability.text.lower()

    principal_id, token = _issue_agent()
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/v1/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["principal_id"] == principal_id

    page = _insert_page()
    outline = client.get(
        f"/api/v1/pages/{page['resource_id']}?view=outline",
        headers=headers,
    )
    assert outline.status_code == 200, outline.text
    headings = outline.json()["outline"]
    overview = next(item for item in headings if item["heading_id"] == "overview")
    assert overview["explicit_id"] is True

    section = client.get(
        f"/api/v1/pages/{page['resource_id']}?view=section&heading_id=overview",
        headers=headers,
    )
    assert section.status_code == 200
    assert section.json()["section"]["content_hash"] == overview["content_hash"]

    search = client.get("/api/v1/search?q=Agent%20API%20Test", headers=headers)
    assert search.status_code == 200
    assert any(item["resource_id"] == page["resource_id"] for item in search.json()["results"])

    resolution = client.post(
        "/api/v1/resolve-canonical",
        headers=headers,
        json={"query": "Agent API Test", "intended_title": "Agent API Test"},
    )
    assert resolution.status_code == 200
    assert resolution.json()["preferred"]["resource_id"] == page["resource_id"]
    assert resolution.json()["create_allowed"] is False

    change_payload = {
        "title": "Correct Agent API overview",
        "purpose": "Prove precise revision-bound proposals",
        "workspace_key": "default",
    }
    change_headers = {**headers, "Idempotency-Key": "change-integration-1"}
    created = client.post("/api/v1/changes", headers=change_headers, json=change_payload)
    assert created.status_code == 201, created.text
    repeated = client.post("/api/v1/changes", headers=change_headers, json=change_payload)
    assert repeated.status_code == 201
    assert repeated.json()["change_id"] == created.json()["change_id"]
    change_id = created.json()["change_id"]

    operation = client.post(
        f"/api/v1/changes/{change_id}/operations",
        headers={**headers, "Idempotency-Key": "operation-integration-1"},
        json={
            "operation_type": "REPLACE_SECTION",
            "page_resource_id": page["resource_id"],
            "expected_revision": page["revision"],
            "expected_section_hash": overview["content_hash"],
            "payload": {
                "heading_id": "overview",
                "content": "## Overview {#overview}\n\nCorrected behavior.\n",
            },
        },
    )
    assert operation.status_code == 201, operation.text
    assert len(operation.json()["operations"]) == 1

    validated = client.post(f"/api/v1/changes/{change_id}/validate", headers=headers)
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "VALIDATED"
    assert validated.json()["validation_summary"]["passed"] is True
    assert validated.json()["preview_receipt"]["publication"]["merge_supported"] is False

    submitted = client.post(
        f"/api/v1/changes/{change_id}/submit",
        headers=headers,
        json={"note": "Ready for human review"},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "SUBMITTED"

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT content, revision FROM docs.pages WHERE resource_id = %s",
            (page["resource_id"],),
        )
        content, revision = cur.fetchone()
    assert content == page["content"]
    assert revision == page["revision"]
