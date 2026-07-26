from __future__ import annotations

import os
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_api import router as agent_router
from app.event_api import router as event_router


app = FastAPI()
app.include_router(agent_router)
app.include_router(event_router)
client = TestClient(app)


def _issue_event_agent() -> tuple[str, str]:
    os.environ["DOCPLANE_BOOTSTRAP_ADMIN_TOKEN"] = "test-bootstrap-token"
    os.environ["DOCPLANE_EVENT_CURSOR_SECRET"] = "test-only-event-cursor-secret"
    response = client.post(
        "/api/v1/admin/principals",
        headers={"X-DocPlane-Bootstrap-Token": "test-bootstrap-token"},
        json={
            "principal_kind": "AGENT",
            "display_name": f"event-agent-{uuid.uuid4().hex[:8]}",
            "scopes": ["events:write", "events:subscribe"],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["principal_id"], body["token"]


def test_domain_events_are_idempotent_cursor_driven_and_principal_scoped():
    principal_id, token = _issue_event_agent()
    auth = {"Authorization": f"Bearer {token}"}
    resource_id = str(uuid.uuid4())
    batch_payload = {
        "producer_id": "integration-suite",
        "events": [
            {
                "event_type": "CHANGE_SUBMITTED",
                "channel": "API",
                "idempotency_key": "change-submitted-1",
                "workspace_key": "operations",
                "resource_type": "CHANGE",
                "resource_id": resource_id,
                "metadata": {"status": "SUBMITTED"},
            },
            {
                "event_type": "INITIATIVE_UPDATED",
                "channel": "API",
                "idempotency_key": "initiative-updated-1",
                "workspace_key": "work",
                "resource_type": "INITIATIVE",
                "resource_id": str(uuid.uuid4()),
                "metadata": {"from_state": "ACTIVE", "to_state": "SOAKING"},
            },
        ],
    }
    first = client.post("/api/v1/events/batch", headers=auth, json=batch_payload)
    assert first.status_code == 202, first.text
    assert first.json()["accepted"] == 2
    assert first.json()["duplicate"] == 0

    repeated = client.post("/api/v1/events/batch", headers=auth, json=batch_payload)
    assert repeated.status_code == 202
    assert repeated.json()["accepted"] == 0
    assert repeated.json()["duplicate"] == 2
    assert repeated.json()["event_ids"] == first.json()["event_ids"]

    stream = client.get("/api/v1/events?limit=100", headers=auth)
    assert stream.status_code == 200, stream.text
    assert stream.json()["usage_analytics"] == "deferred to dashboard slice"
    types = {event["event_type"] for event in stream.json()["events"]}
    assert {"CHANGE_SUBMITTED", "INITIATIVE_UPDATED"} <= types
    assert all(event["principal_id"] == principal_id for event in stream.json()["events"])

    cursor = stream.json()["next_cursor"]
    empty_delta = client.get("/api/v1/events", headers=auth, params={"after": cursor})
    assert empty_delta.status_code == 200
    assert empty_delta.json()["events"] == []

    other_principal, other_token = _issue_event_agent()
    assert other_principal != principal_id
    wrong_scope_cursor = client.get(
        "/api/v1/events",
        headers={"Authorization": f"Bearer {other_token}"},
        params={"after": cursor},
    )
    assert wrong_scope_cursor.status_code == 400
    assert wrong_scope_cursor.json()["detail"]["code"] == "EVENT_CURSOR_INVALID"


def test_event_metadata_rejects_credentials_and_prompt_payloads():
    _, token = _issue_event_agent()
    response = client.post(
        "/api/v1/events/batch",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "producer_id": "unsafe-suite",
            "events": [
                {
                    "event_type": "CHANGE_SUBMITTED",
                    "channel": "API",
                    "idempotency_key": "unsafe-1",
                    "metadata": {"token": "must-not-be-stored"},
                }
            ],
        },
    )
    assert response.status_code == 422
