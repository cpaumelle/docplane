from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_api import router as agent_router
from app.db import get_conn
from app.event_api import router as event_router
from app.usage_middleware import UsageEventMiddleware


app = FastAPI()
app.include_router(agent_router)
app.include_router(event_router)
app.add_middleware(UsageEventMiddleware)
client = TestClient(app)


def _issue_observer_agent() -> tuple[str, str]:
    os.environ["DOCPLANE_BOOTSTRAP_ADMIN_TOKEN"] = "test-bootstrap-token"
    os.environ["DOCPLANE_EVENT_CURSOR_SECRET"] = "test-only-event-cursor-secret"
    response = client.post(
        "/api/v1/admin/principals",
        headers={"X-DocPlane-Bootstrap-Token": "test-bootstrap-token"},
        json={
            "principal_kind": "AGENT",
            "display_name": "usage-observer-agent",
            "scopes": [
                "docs:read",
                "docs:propose",
                "events:write",
                "events:subscribe",
                "analytics:read",
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["principal_id"], body["token"]


def _insert_page() -> dict:
    suffix = uuid.uuid4().hex[:10]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO docs.pages (path, title, nav_path, content, updated_by)
            VALUES (%s, %s, %s, %s, 'event-test')
            RETURNING resource_id::text, revision, path
            """,
            (
                f"operations/usage-{suffix}.md",
                "Usage Test",
                "Operations/Usage Test",
                "# Usage Test\n\n**Lifecycle:** OPERATION\n<!-- lifecycle: OPERATION -->\n\nRunbook.\n",
            ),
        )
        resource_id, revision, path = cur.fetchone()
        conn.commit()
    return {"resource_id": resource_id, "revision": revision, "path": path}


def test_events_are_idempotent_cursor_driven_and_split_by_actor():
    principal_id, token = _issue_observer_agent()
    auth = {"Authorization": f"Bearer {token}"}
    page = _insert_page()

    read = client.get(
        f"/api/v1/pages/{page['resource_id']}?view=summary",
        headers={**auth, "X-Request-ID": "auto-read-usage-test"},
    )
    assert read.status_code == 200, read.text

    batch_payload = {
        "producer_id": "integration-suite",
        "events": [
            {
                "event_type": "PAGE_VIEW",
                "channel": "WEB",
                "idempotency_key": "page-view-1",
                "page_resource_id": page["resource_id"],
                "page_path": page["path"],
                "page_revision": page["revision"],
                "resource_type": "PAGE",
                "resource_id": page["resource_id"],
                "metadata": {"surface": "reader"},
            },
            {
                "event_type": "SEARCH_ZERO_RESULTS",
                "channel": "SEARCH",
                "idempotency_key": "search-gap-1",
                "resource_type": "SEARCH",
                "metadata": {"query_fingerprint": "a" * 64, "result_count": 0},
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
    types = {event["event_type"] for event in stream.json()["events"]}
    assert {"PAGE_READ_API", "PAGE_VIEW", "SEARCH_ZERO_RESULTS"} <= types
    cursor = stream.json()["next_cursor"]
    empty_delta = client.get("/api/v1/events", headers=auth, params={"after": cursor})
    assert empty_delta.status_code == 200
    assert empty_delta.json()["events"] == []

    other_principal, other_token = _issue_observer_agent()
    assert other_principal != principal_id
    wrong_scope_cursor = client.get(
        "/api/v1/events",
        headers={"Authorization": f"Bearer {other_token}"},
        params={"after": cursor},
    )
    assert wrong_scope_cursor.status_code == 400
    assert wrong_scope_cursor.json()["detail"]["code"] == "EVENT_CURSOR_INVALID"

    usage = client.get("/api/v1/analytics/pages?days=30", headers=auth)
    assert usage.status_code == 200, usage.text
    row = next(item for item in usage.json()["pages"] if item["page_resource_id"] == page["resource_id"])
    assert row["events"] >= 2
    assert row["agent_events"] >= 2
    assert row["human_events"] == 0

    detail = client.get(f"/api/v1/analytics/pages/{page['resource_id']}", headers=auth)
    assert detail.status_code == 200
    assert detail.json()["page"]["path"] == page["path"]

    gaps = client.get("/api/v1/analytics/search-gaps", headers=auth)
    assert gaps.status_code == 200
    assert gaps.json()["gaps"][0]["query_fingerprint"] == "a" * 64

    search_event_id = first.json()["event_ids"][1]
    restricted = client.post(
        "/api/v1/events/restricted-payload",
        headers=auth,
        json={
            "event_id": search_event_id,
            "payload_kind": "SEARCH_QUERY",
            "payload": {"query": "missing operational procedure"},
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        },
    )
    assert restricted.status_code == 201, restricted.text
    assert restricted.json()["stored"] is True

    retention = client.get("/api/v1/analytics/retention", headers=auth)
    assert retention.status_code == 200
    assert retention.json()["policy"]["raw_event_days"] == 90
    assert retention.json()["restricted_payloads"]["count"] >= 1
