from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_api import router as agent_router
from app.event_api import router as event_router
from app.work_api import router as work_router


app = FastAPI()
app.include_router(agent_router)
app.include_router(event_router)
app.include_router(work_router)
client = TestClient(app)


def _issue_work_agent() -> tuple[str, str]:
    os.environ["DOCPLANE_BOOTSTRAP_ADMIN_TOKEN"] = "test-bootstrap-token"
    os.environ["DOCPLANE_EVENT_CURSOR_SECRET"] = "test-only-event-cursor-secret"
    suffix = uuid.uuid4().hex[:8]
    response = client.post(
        "/api/v1/admin/principals",
        headers={"X-DocPlane-Bootstrap-Token": "test-bootstrap-token"},
        json={
            "principal_kind": "AGENT",
            "display_name": f"work-agent-{suffix}",
            "scopes": [
                "admin:workspaces",
                "work:read",
                "work:update",
                "events:subscribe",
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["principal_id"], body["token"]


def test_workspaces_initiatives_soaks_parking_and_queues_are_first_class():
    principal_id, token = _issue_work_agent()
    auth = {"Authorization": f"Bearer {token}"}
    suffix = uuid.uuid4().hex[:10]

    workspace_payload = {
        "workspace_key": f"programme-{suffix}",
        "name": "Endpoint-first programme",
        "workspace_kind": "WORK",
        "visibility": "INTERNAL",
        "mutation_policy": "DIRECT_LOW_RISK",
        "retention_policy": {"completed_days": 365},
    }
    workspace_headers = {**auth, "Idempotency-Key": f"workspace-{suffix}"}
    workspace = client.post(
        "/api/v1/workspaces", headers=workspace_headers, json=workspace_payload
    )
    assert workspace.status_code == 201, workspace.text
    workspace_id = workspace.json()["workspace_id"]
    repeated_workspace = client.post(
        "/api/v1/workspaces", headers=workspace_headers, json=workspace_payload
    )
    assert repeated_workspace.status_code == 201
    assert repeated_workspace.json()["workspace_id"] == workspace_id

    listed = client.get("/api/v1/workspaces", headers=auth)
    assert listed.status_code == 200
    assert any(item["workspace_id"] == workspace_id for item in listed.json()["workspaces"])

    initiative_payload = {
        "initiative_key": f"agent-interface-{suffix}",
        "workspace_id": workspace_id,
        "title": "Endpoint-first agent interface",
        "objective": "Replace routine SSH and direct SQL with supported product endpoints",
        "scope": "Discovery, precise changes, events, usage and work tracking",
        "work_state": "ACTIVE",
        "priority": "HIGH",
        "review_due_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    }
    initiative_headers = {**auth, "Idempotency-Key": f"initiative-{suffix}"}
    created = client.post(
        "/api/v1/initiatives", headers=initiative_headers, json=initiative_payload
    )
    assert created.status_code == 201, created.text
    initiative = created.json()
    initiative_id = initiative["initiative_id"]
    assert initiative["work_state"] == "ACTIVE"
    assert initiative["version"] == 1

    repeated = client.post(
        "/api/v1/initiatives", headers=initiative_headers, json=initiative_payload
    )
    assert repeated.status_code == 201
    assert repeated.json()["initiative_id"] == initiative_id

    blocked = client.post(
        f"/api/v1/initiatives/{initiative_id}/transition",
        headers={**auth, "Idempotency-Key": f"block-{suffix}"},
        json={
            "expected_version": 1,
            "work_state": "BLOCKED",
            "blocker_summary": "WP8 proposal merge guard is not implemented yet",
            "note": "Hold publication while discovery and proposal APIs remain read/propose only.",
        },
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["work_state"] == "BLOCKED"
    assert blocked.json()["version"] == 2

    stale = client.post(
        f"/api/v1/initiatives/{initiative_id}/transition",
        headers={**auth, "Idempotency-Key": f"stale-{suffix}"},
        json={"expected_version": 1, "work_state": "ACTIVE"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "INITIATIVE_VERSION_STALE"

    active = client.post(
        f"/api/v1/initiatives/{initiative_id}/transition",
        headers={**auth, "Idempotency-Key": f"unblock-{suffix}"},
        json={
            "expected_version": 2,
            "work_state": "ACTIVE",
            "note": "The prerequisite API contracts are now available.",
        },
    )
    assert active.status_code == 200, active.text
    assert active.json()["version"] == 3

    now = datetime.now(timezone.utc)
    soaking = client.post(
        f"/api/v1/initiatives/{initiative_id}/transition",
        headers={**auth, "Idempotency-Key": f"soak-{suffix}"},
        json={
            "expected_version": 3,
            "work_state": "SOAKING",
            "soak_started_at": now.isoformat(),
            "soak_review_at": (now - timedelta(minutes=1)).isoformat(),
            "soak_success_criteria": "All agent and event workflows remain green with no lost updates",
            "soak_failure_conditions": "Any duplicate mutation, stale overwrite or missing event receipt",
            "note": "Begin the API contract soak.",
        },
    )
    assert soaking.status_code == 200, soaking.text
    assert soaking.json()["work_state"] == "SOAKING"
    assert soaking.json()["version"] == 4

    observation_headers = {**auth, "Idempotency-Key": f"observation-{suffix}"}
    observation_payload = {
        "activity_type": "SOAK_OBSERVATION",
        "body": "The endpoint-first integration suite remains green.",
        "metadata": {"workflow": "events-usage", "result": "pass"},
    }
    observation = client.post(
        f"/api/v1/initiatives/{initiative_id}/activities",
        headers=observation_headers,
        json=observation_payload,
    )
    assert observation.status_code == 201, observation.text
    assert observation.json()["initiative_version"] == 5
    repeated_observation = client.post(
        f"/api/v1/initiatives/{initiative_id}/activities",
        headers=observation_headers,
        json=observation_payload,
    )
    assert repeated_observation.status_code == 201
    assert repeated_observation.json()["activity_id"] == observation.json()["activity_id"]

    link = client.post(
        f"/api/v1/initiatives/{initiative_id}/links",
        headers={**auth, "Idempotency-Key": f"link-{suffix}"},
        json={
            "relation": "CHANGE_PROPOSAL",
            "resource_type": "CHANGE",
            "resource_id": "pending-wp8-merge-slice",
        },
    )
    assert link.status_code == 201, link.text
    assert link.json()["created"] is True

    promotion = client.post(
        f"/api/v1/initiatives/{initiative_id}/promotion",
        headers={**auth, "Idempotency-Key": f"promotion-{suffix}"},
        json={"expected_version": 5, "promotion_state": "READY"},
    )
    assert promotion.status_code == 200, promotion.text
    assert promotion.json()["promotion_state"] == "READY"
    assert promotion.json()["version"] == 6

    parking_payload = {
        "initiative_key": f"parking-{suffix}",
        "workspace_id": workspace_id,
        "title": "Deferred compatibility cleanup",
        "objective": "Track a deferred compatibility concern without confusing it with completion",
        "work_state": "PARKED",
        "priority": "LOW",
        "parked_reason": "Wait for the clean DocPlane cutover before deleting legacy compatibility",
        "parked_review_at": (now - timedelta(days=1)).isoformat(),
    }
    parked = client.post(
        "/api/v1/initiatives",
        headers={**auth, "Idempotency-Key": f"parked-{suffix}"},
        json=parking_payload,
    )
    assert parked.status_code == 201, parked.text
    assert parked.json()["work_state"] == "PARKED"

    queues = client.get("/api/v1/work/queues", headers=auth)
    assert queues.status_code == 200, queues.text
    assert any(
        item["initiative_id"] == initiative_id
        for item in queues.json()["queues"]["soaking_due"]
    )
    assert any(
        item["initiative_id"] == parked.json()["initiative_id"]
        for item in queues.json()["queues"]["parked_due"]
    )
    assert any(
        item["initiative_id"] == initiative_id
        for item in queues.json()["queues"]["ready_to_promote"]
    )

    detail = client.get(f"/api/v1/initiatives/{initiative_id}", headers=auth)
    assert detail.status_code == 200, detail.text
    assert detail.json()["owner_principal_id"] == principal_id
    assert any(item["activity_type"] == "SOAK_OBSERVATION" for item in detail.json()["activities"])
    assert any(item["relation"] == "CHANGE_PROPOSAL" for item in detail.json()["links"])

    events = client.get("/api/v1/events?limit=200", headers=auth)
    assert events.status_code == 200, events.text
    event_types = {item["event_type"] for item in events.json()["events"]}
    assert {
        "WORKSPACE_CREATED",
        "INITIATIVE_CREATED",
        "INITIATIVE_UPDATED",
        "INITIATIVE_ACTIVITY_ADDED",
        "INITIATIVE_PROMOTION_UPDATED",
    } <= event_types
