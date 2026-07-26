from __future__ import annotations

from fastapi.testclient import TestClient

from dashboard import app as dashboard_app
from dashboard.control_plane import ControlPlaneError


class FakeClient:
    def __init__(self):
        self.calls = []

    def discovery(self):
        return {
            "product": "DocPlane",
            "api_contract": "v1",
            "capabilities": {"reorganisation_plans": True},
        }

    def get(self, path, *, authorization=None, params=None):
        self.calls.append(("GET", path, authorization, params, None, None))
        if path == "/api/v1/dashboard/structure":
            return {
                "contract_version": "structure-v1",
                "fingerprint": "abc123",
                "summary": {"published_pages": 10, "archived_pages": 2, "sections": 3},
                "sections": [],
                "signals": {},
            }
        if path == "/api/v1/reorganisation/plans":
            return {"plans": [{"plan_id": "11111111-1111-1111-1111-111111111111", "title": "Move pages", "status": "PLAN"}]}
        if path == "/api/v1/reorganisation/tree":
            return {"tree": [{"Reference": ["reference/index.md"]}]}
        if path == "/api/v1/certification/status":
            return {"state": "CURRENT"}
        if path == "/api/v1/work/queues":
            return {"counts": {"active": 1}}
        if path == "/api/v1/maintenance/pages":
            return {"counts": {"unverified": 2}}
        if path.startswith("/api/v1/reorganisation/plans/"):
            return {"plan_id": path.split("/")[-1], "status": "VALIDATED"}
        raise ControlPlaneError(404, "CAPABILITY_NOT_AVAILABLE", {"path": path})

    def post(self, path, *, authorization, idempotency_key=None, json_body=None):
        self.calls.append(("POST", path, authorization, None, idempotency_key, json_body))
        return {
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "status": path.rsplit("/", 1)[-1].upper() if "/" in path else "PLAN",
            "received": json_body,
            "idempotency_key": idempotency_key,
        }


def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(dashboard_app, "client", fake)
    return TestClient(dashboard_app.app), fake


def test_dashboard_is_api_backed_control_plane(monkeypatch):
    test_client, fake = client(monkeypatch)
    health = test_client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["database_access"] is False
    assert health.json()["dashboard_authority"] == "human-control-surface"

    discovery = test_client.get("/api/control-plane/discovery")
    assert discovery.status_code == 200
    assert discovery.json()["dashboard"]["role"] == "first-class human control surface"
    assert discovery.json()["dashboard"]["direct_database_access"] is False
    assert "reorganisation" in discovery.json()["dashboard"]["domains"]


def test_overview_requires_a_named_operator_and_tolerates_pending_modules(monkeypatch):
    test_client, fake = client(monkeypatch)
    unauthenticated = test_client.get("/api/control-plane/overview")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["detail"]["code"] == "DASHBOARD_AUTH_REQUIRED"

    response = test_client.get(
        "/api/control-plane/overview",
        headers={"Authorization": "Bearer human-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "dashboard-control-plane-v1"
    assert {"corpus", "reorganisation", "certification", "work", "trust"} <= set(body["available"])
    assert {"changes", "usage", "schema_catalog", "operations"} <= set(body["pending"])
    assert all(call[2] == "Bearer human-token" for call in fake.calls if call[1] != "/.well-known/docplane.json")


def test_reorganisation_is_a_first_class_governed_workflow(monkeypatch):
    test_client, fake = client(monkeypatch)
    auth = {"Authorization": "Bearer operator"}
    workspace = test_client.get("/api/control-plane/reorganisation", headers=auth)
    assert workspace.status_code == 200
    assert workspace.json()["workflow"] == [
        "PLAN",
        "ANALYZE_IMPACT",
        "VALIDATE",
        "SUBMIT",
        "APPROVE",
        "EXECUTE",
        "CERTIFY",
        "STABILIZE",
        "CLOSE_OR_COMPENSATE",
    ]
    assert workspace.json()["execution_authority"] == "WP8 guarded APIs only"
    assert workspace.json()["direct_filesystem_mutation"] is False

    created = test_client.post(
        "/api/control-plane/reorganisation/plans",
        headers={**auth, "Idempotency-Key": "plan-1"},
        json={"title": "Move infrastructure pages", "objective": "Create a clearer reference tree"},
    )
    assert created.status_code == 201
    assert created.json()["idempotency_key"] == "plan-1"
    call = fake.calls[-1]
    assert call[0] == "POST"
    assert call[1] == "/api/v1/reorganisation/plans"
    assert call[2] == "Bearer operator"

    plan_id = "11111111-1111-1111-1111-111111111111"
    analyzed = test_client.post(
        f"/api/control-plane/reorganisation/plans/{plan_id}/analyze",
        headers={**auth, "Idempotency-Key": "analyze-1"},
        json={},
    )
    assert analyzed.status_code == 200
    assert fake.calls[-1][1].endswith("/analyze")


def test_control_plane_errors_are_preserved(monkeypatch):
    test_client, fake = client(monkeypatch)

    def failed_discovery():
        raise ControlPlaneError(503, "CONTROL_PLANE_UNAVAILABLE", {"type": "ConnectError"})

    fake.discovery = failed_discovery
    health = test_client.get("/healthz")
    assert health.status_code == 503
    assert health.json()["detail"]["code"] == "CONTROL_PLANE_UNAVAILABLE"
