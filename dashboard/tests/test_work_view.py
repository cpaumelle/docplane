"""Contract tests for the dashboard Work-view proxy routes.

The dashboard stays a pure HTTP client of the API: these routes forward
identity and idempotency and hold no work-domain state of their own.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import dashboard.app as dashboard_app


class FakeControlPlane:
    def __init__(self):
        self.calls = []

    def get(self, path, *, authorization=None, params=None):
        self.calls.append(("GET", path, authorization, params, None, None))
        if path == "/api/v1/work/queues":
            return {"by_state": {}, "by_priority": {}, "parked_review_due": 0, "soak_review_due": 0, "inbox": 2}
        if path == "/api/v1/work/captures":
            return {"captures": [], "count": 0, "total": 0, "truncated": False}
        if path == "/api/v1/initiatives":
            return {"initiatives": [], "count": 0}
        return {}

    def post(self, path, *, authorization=None, idempotency_key=None, json_body=None):
        self.calls.append(("POST", path, authorization, None, idempotency_key, json_body))
        return {"capture": {"status": "INBOX"}}


client = TestClient(dashboard_app.app)


def setup_function():
    dashboard_app.client = FakeControlPlane()


def test_queues_and_captures_forward_identity():
    headers = {"Authorization": "Bearer human-token"}
    assert client.get("/api/control-plane/work/queues", headers=headers).status_code == 200
    assert client.get("/api/control-plane/work/captures?status=INBOX", headers=headers).status_code == 200
    calls = dashboard_app.client.calls
    assert calls[0][1] == "/api/v1/work/queues"
    assert calls[0][2] == "Bearer human-token"
    assert calls[1][1] == "/api/v1/work/captures"
    assert calls[1][3] == {"status": "INBOX", "limit": 200}


def test_capture_create_and_triage_forward_idempotency():
    headers = {"Authorization": "Bearer human-token", "Idempotency-Key": "cap-1"}
    assert client.post("/api/control-plane/work/captures", headers=headers, json={"body": "idea"}).status_code == 201
    capture_id = "33333333-3333-3333-3333-333333333333"
    assert client.post(f"/api/control-plane/work/captures/{capture_id}/promote", headers=headers, json={}).status_code == 200
    assert client.post(f"/api/control-plane/work/captures/{capture_id}/nonsense", headers=headers, json={}).status_code == 404
    create_call, promote_call = dashboard_app.client.calls[0], dashboard_app.client.calls[1]
    assert create_call[1] == "/api/v1/work/captures"
    assert create_call[4] == "cap-1"
    assert promote_call[1].endswith("/promote")


def test_unauthenticated_work_routes_refuse():
    assert client.get("/api/control-plane/work/queues").status_code == 401
    assert client.post("/api/control-plane/work/captures", json={"body": "x"}).status_code == 401
