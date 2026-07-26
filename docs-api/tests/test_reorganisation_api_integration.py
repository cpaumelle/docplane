from __future__ import annotations

import os
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_api import router as agent_router
from app.db import get_conn
from app.event_api import router as event_router
from app.reorganisation_api import router as reorganisation_router


app = FastAPI()
app.include_router(agent_router)
app.include_router(event_router)
app.include_router(reorganisation_router)
client = TestClient(app)


def _principal() -> tuple[str, str]:
    os.environ["DOCPLANE_BOOTSTRAP_ADMIN_TOKEN"] = "test-bootstrap-token"
    os.environ["DOCPLANE_EVENT_CURSOR_SECRET"] = "test-only-event-cursor-secret"
    response = client.post(
        "/api/v1/admin/principals",
        headers={"X-DocPlane-Bootstrap-Token": "test-bootstrap-token"},
        json={
            "principal_kind": "HUMAN",
            "display_name": "reorganisation-owner",
            "scopes": [
                "docs:read", "docs:propose", "docs:review", "docs:merge",
                "events:write", "events:subscribe", "admin:workspaces",
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["principal_id"], body["token"]


def _insert_pages() -> tuple[dict, dict]:
    suffix = uuid.uuid4().hex[:8]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT workspace_id FROM docplane.workspaces WHERE workspace_key = 'operations'")
        workspace_id = cur.fetchone()[0]
        first_path = f"operations/source-{suffix}.md"
        second_path = f"operations/referrer-{suffix}.md"
        cur.execute(
            """
            INSERT INTO docs.pages
                (path, title, nav_path, content, updated_by, workspace_id,
                 publication_state, knowledge_class, metadata_review_required)
            VALUES (%s, %s, %s, %s, 'reorg-test', %s, 'PUBLISHED', 'OPERATION', FALSE)
            RETURNING resource_id::text, revision, path, nav_path
            """,
            (first_path, "Source", f"Operations/Reorganisation/{suffix}/Source", "# Source\n\nProcedure.\n", workspace_id),
        )
        first = cur.fetchone()
        cur.execute(
            """
            INSERT INTO docs.pages
                (path, title, nav_path, content, updated_by, workspace_id,
                 publication_state, knowledge_class, metadata_review_required)
            VALUES (%s, %s, %s, %s, 'reorg-test', %s, 'PUBLISHED', 'OPERATION', FALSE)
            RETURNING resource_id::text, revision, path, nav_path
            """,
            (
                second_path,
                "Referrer",
                f"Operations/Reorganisation/{suffix}/Referrer",
                f"# Referrer\n\nSee [{first_path}]({first_path}).\n",
                workspace_id,
            ),
        )
        second = cur.fetchone()
        conn.commit()
    keys = ("resource_id", "revision", "path", "nav_path")
    return dict(zip(keys, first)), dict(zip(keys, second))


def test_reorganisation_plan_analyzes_converts_and_holds_execution():
    principal_id, token = _principal()
    auth = {"Authorization": f"Bearer {token}"}
    source, _ = _insert_pages()
    target = source["path"].replace("source-", "moved-")

    created = client.post(
        "/api/v1/reorganisation/plans",
        headers={**auth, "Idempotency-Key": "plan-one"},
        json={
            "workspace_key": "operations",
            "title": "Move source page",
            "purpose": "Improve the operations information architecture",
        },
    )
    assert created.status_code == 201, created.text
    plan_id = created.json()["plan_id"]
    assert created.json()["author_principal_id"] == principal_id

    repeated = client.post(
        "/api/v1/reorganisation/plans",
        headers={**auth, "Idempotency-Key": "plan-one"},
        json={
            "workspace_key": "operations",
            "title": "Move source page",
            "purpose": "Improve the operations information architecture",
        },
    )
    assert repeated.status_code == 201
    assert repeated.json()["plan_id"] == plan_id

    operation = client.post(
        f"/api/v1/reorganisation/plans/{plan_id}/operations",
        headers={**auth, "Idempotency-Key": "move-one"},
        json={
            "operation_type": "MOVE_PAGE",
            "page_resource_id": source["resource_id"],
            "expected_revision": source["revision"],
            "payload": {"to_path": target, "create_redirect": True},
        },
    )
    assert operation.status_code == 201, operation.text

    analysed = client.post(f"/api/v1/reorganisation/plans/{plan_id}/analyze", headers=auth)
    assert analysed.status_code == 200, analysed.text
    receipt = analysed.json()["analysis_receipt"]
    assert receipt["passed"] is True
    assert receipt["candidate_state_identity"] != receipt["base_state_identity"]
    assert receipt["effects"]["search_reindex_required"] is True
    assert receipt["effects"]["mcp_reindex_required"] is True
    assert receipt["operations"][0]["inbound_reference_count"] == 1
    assert receipt["compensating_operations"][0]["payload"]["to_path"] == source["path"]

    validated = client.post(f"/api/v1/reorganisation/plans/{plan_id}/validate", headers=auth)
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "VALIDATED"
    assert validated.json()["validation_receipt"]["passed"] is True

    submitted = client.post(
        f"/api/v1/reorganisation/plans/{plan_id}/submit",
        headers={**auth, "Idempotency-Key": "submit-one"},
        json={"note": "Ready for owner review"},
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["status"] == "SUBMITTED"
    assert body["linked_change_id"]

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT operation_type, page_resource_id::text, expected_revision, payload "
            "FROM docs.change_operations WHERE change_id = %s",
            (body["linked_change_id"],),
        )
        converted = cur.fetchone()
    assert converted[0] == "MOVE_PAGE"
    assert converted[1] == source["resource_id"]
    assert converted[2] == source["revision"]
    assert converted[3]["to_path"] == target

    approved = client.post(
        f"/api/v1/reorganisation/plans/{plan_id}/reviews",
        headers=auth,
        json={
            "decision": "APPROVE",
            "reviewed_plan_version": body["version"],
            "note": "Impact is understood",
        },
    )
    assert approved.status_code == 201, approved.text
    assert approved.json()["status"] == "APPROVED"

    held = client.post(f"/api/v1/reorganisation/plans/{plan_id}/execute", headers=auth)
    assert held.status_code == 409
    assert held.json()["detail"]["code"] == "WP8_EXECUTION_NOT_AVAILABLE"

    stream = client.get("/api/v1/events?event_type=REORGANISATION_PLAN_SUBMITTED", headers=auth)
    assert stream.status_code == 200
    assert any(event["resource_id"] == plan_id for event in stream.json()["events"])


def test_collision_is_reported_and_blocks_validation():
    _, token = _principal()
    auth = {"Authorization": f"Bearer {token}"}
    source, target_page = _insert_pages()
    created = client.post(
        "/api/v1/reorganisation/plans",
        headers={**auth, "Idempotency-Key": "collision-plan"},
        json={"workspace_key": "operations", "title": "Collision", "purpose": "Prove collision analysis"},
    )
    plan_id = created.json()["plan_id"]
    client.post(
        f"/api/v1/reorganisation/plans/{plan_id}/operations",
        headers={**auth, "Idempotency-Key": "collision-move"},
        json={
            "operation_type": "MOVE_PAGE",
            "page_resource_id": source["resource_id"],
            "expected_revision": source["revision"],
            "payload": {"to_path": target_page["path"]},
        },
    )
    analysed = client.post(f"/api/v1/reorganisation/plans/{plan_id}/analyze", headers=auth)
    assert analysed.status_code == 200
    errors = analysed.json()["analysis_receipt"]["errors"]
    assert any(error["code"] == "TARGET_PATH_COLLISION" for error in errors)
    validated = client.post(f"/api/v1/reorganisation/plans/{plan_id}/validate", headers=auth)
    assert validated.status_code == 200
    assert validated.json()["status"] == "ANALYZED"
    assert validated.json()["validation_receipt"]["passed"] is False
