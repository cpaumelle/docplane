from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_api import router as agent_router
from app.db import get_conn
from app.event_api import router as event_router
from app.trust_api import router as trust_router


app = FastAPI()
app.include_router(agent_router)
app.include_router(event_router)
app.include_router(trust_router)
client = TestClient(app)


def _issue_owner_reviewer() -> tuple[str, str]:
    os.environ["DOCPLANE_BOOTSTRAP_ADMIN_TOKEN"] = "test-bootstrap-token"
    os.environ["DOCPLANE_EVENT_CURSOR_SECRET"] = "test-only-event-cursor-secret"
    response = client.post(
        "/api/v1/admin/principals",
        headers={"X-DocPlane-Bootstrap-Token": "test-bootstrap-token"},
        json={
            "principal_kind": "HUMAN",
            "display_name": f"trust-owner-{uuid.uuid4().hex[:8]}",
            "scopes": [
                "docs:read",
                "docs:write-direct",
                "docs:review",
                "events:subscribe",
                "admin:workspaces",
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["principal_id"], body["token"]


def _insert_unclassified_page() -> dict:
    suffix = uuid.uuid4().hex[:10]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT workspace_id FROM docplane.workspaces WHERE workspace_key = 'migration-import'"
        )
        migration_workspace = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO docs.pages
                (path, title, nav_path, content, updated_by, workspace_id,
                 publication_state, knowledge_class, verification_state,
                 metadata_review_required)
            VALUES (%s, %s, %s, %s, 'trust-test', %s,
                    'PUBLISHED', NULL, 'UNVERIFIED', TRUE)
            RETURNING resource_id::text, revision, metadata_version, path
            """,
            (
                f"migration/trust-{suffix}.md",
                "Trust Test",
                f"Migration/Trust {suffix}",
                "# Trust Test\n\nImported content awaiting accountable classification.\n",
                migration_workspace,
            ),
        )
        resource_id, revision, metadata_version, path = cur.fetchone()
        cur.execute(
            "SELECT workspace_id::text FROM docplane.workspaces WHERE workspace_key = 'operations'"
        )
        operations_workspace = cur.fetchone()[0]
        conn.commit()
    return {
        "resource_id": resource_id,
        "revision": revision,
        "metadata_version": metadata_version,
        "path": path,
        "operations_workspace": operations_workspace,
    }


def test_classification_verification_invalidation_and_expiry_are_audited():
    principal_id, token = _issue_owner_reviewer()
    auth = {"Authorization": f"Bearer {token}"}
    page = _insert_unclassified_page()
    first_expiry = datetime.now(timezone.utc) + timedelta(days=7)

    classification_payload = {
        "expected_metadata_version": page["metadata_version"],
        "workspace_id": page["operations_workspace"],
        "publication_state": "PUBLISHED",
        "knowledge_class": "OPERATION",
        "criticality": "IMPORTANT",
        "owner_principal_id": principal_id,
        "review_due_at": first_expiry.isoformat(),
        "reason": "Classify imported procedure into the accountable operations workspace",
    }
    classified = client.post(
        f"/api/v1/pages/{page['resource_id']}/classification",
        headers={**auth, "Idempotency-Key": "classify-page-once"},
        json=classification_payload,
    )
    assert classified.status_code == 200, classified.text
    classified_body = classified.json()
    assert classified_body["workspace_key"] == "operations"
    assert classified_body["knowledge_class"] == "OPERATION"
    assert classified_body["owner_principal_id"] == principal_id
    assert classified_body["metadata_review_required"] is False
    assert classified_body["metadata_version"] == page["metadata_version"] + 1
    assert classified_body["metadata_history"][0]["change_reason"].startswith("Classify imported")

    repeated = client.post(
        f"/api/v1/pages/{page['resource_id']}/classification",
        headers={**auth, "Idempotency-Key": "classify-page-once"},
        json=classification_payload,
    )
    assert repeated.status_code == 200
    assert repeated.json()["metadata_version"] == classified_body["metadata_version"]

    verified = client.post(
        f"/api/v1/pages/{page['resource_id']}/verify",
        headers={**auth, "Idempotency-Key": "verify-revision-one"},
        json={
            "expected_revision": page["revision"],
            "expires_at": first_expiry.isoformat(),
            "notes": "Procedure checked against the current implementation",
        },
    )
    assert verified.status_code == 201, verified.text
    verified_body = verified.json()
    assert verified_body["verification_state"] == "VERIFIED"
    assert verified_body["verification_receipt"]["page_revision"] == page["revision"]

    new_revision = str(uuid.uuid4())
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE docs.pages
               SET content = content || '\nChanged after verification.\n',
                   revision = %s,
                   version = version + 1,
                   updated_at = now(),
                   updated_by = 'trust-test-content-change'
             WHERE resource_id = %s
            """,
            (new_revision, page["resource_id"]),
        )
        conn.commit()

    invalidated = client.get(f"/api/v1/pages/{page['resource_id']}/trust", headers=auth)
    assert invalidated.status_code == 200, invalidated.text
    invalidated_body = invalidated.json()
    assert invalidated_body["revision"] == new_revision
    assert invalidated_body["verification_state"] == "OUTDATED"
    assert invalidated_body["review_due_at"] is not None
    assert invalidated_body["verifications"][0]["verification_state"] == "OUTDATED"

    second_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    reverified = client.post(
        f"/api/v1/pages/{page['resource_id']}/verify",
        headers={**auth, "Idempotency-Key": "verify-revision-two"},
        json={
            "expected_revision": new_revision,
            "expires_at": second_expiry.isoformat(),
            "notes": "Updated revision checked",
        },
    )
    assert reverified.status_code == 201, reverified.text
    assert reverified.json()["verification_state"] == "VERIFIED"

    expired = client.post(
        "/api/v1/maintenance/verifications/expire",
        headers={**auth, "Idempotency-Key": "expire-verifications-once"},
        json={"cutoff": (second_expiry + timedelta(minutes=1)).isoformat()},
    )
    assert expired.status_code == 200, expired.text
    assert expired.json()["pages_expired"] >= 1

    queue = client.get("/api/v1/maintenance/pages?queue=expired", headers=auth)
    assert queue.status_code == 200, queue.text
    assert any(item["resource_id"] == page["resource_id"] for item in queue.json()["queues"]["expired"])

    stream = client.get("/api/v1/events?limit=200", headers=auth)
    assert stream.status_code == 200, stream.text
    event_types = {event["event_type"] for event in stream.json()["events"]}
    assert {
        "PAGE_CLASSIFIED",
        "PAGE_VERIFIED",
        "VERIFICATION_EXPIRY_RUN",
    } <= event_types


def test_verification_refuses_unclassified_or_stale_revision():
    _, token = _issue_owner_reviewer()
    auth = {"Authorization": f"Bearer {token}"}
    page = _insert_unclassified_page()

    unclassified = client.post(
        f"/api/v1/pages/{page['resource_id']}/verify",
        headers={**auth, "Idempotency-Key": "verify-unclassified"},
        json={"expected_revision": page["revision"]},
    )
    assert unclassified.status_code == 409
    assert unclassified.json()["detail"]["code"] in {
        "PAGE_NOT_DURABLE_KNOWLEDGE",
        "PAGE_METADATA_REVIEW_REQUIRED",
        "PAGE_OWNER_REQUIRED",
    }

    stale_classification = client.post(
        f"/api/v1/pages/{page['resource_id']}/classification",
        headers={**auth, "Idempotency-Key": "stale-classification"},
        json={
            "expected_metadata_version": page["metadata_version"] + 1,
            "workspace_id": page["operations_workspace"],
            "publication_state": "PUBLISHED",
            "knowledge_class": "OPERATION",
            "criticality": "NORMAL",
            "reason": "This request intentionally carries a stale metadata version",
        },
    )
    assert stale_classification.status_code == 409
    assert stale_classification.json()["detail"]["code"] == "PAGE_METADATA_VERSION_STALE"
