"""Contract tests for the verb-first shell: the overview aggregates coverage,
and the dashboard proxy no longer carries routes nothing calls."""
from __future__ import annotations

from fastapi.testclient import TestClient

import dashboard.app as dashboard_app


class FakeControlPlane:
    def __init__(self):
        self.calls = []

    def get(self, path, *, authorization=None, params=None):
        self.calls.append((path, authorization, params))
        if path == "/api/v1/observe/coverage":
            return {"services": [], "unwatched_services": [], "rule_count": 0}
        return {}

    def post(self, path, *, authorization=None, idempotency_key=None, json_body=None):
        self.calls.append((path, authorization, json_body))
        return {}


client = TestClient(dashboard_app.app)


def setup_function():
    dashboard_app.client = FakeControlPlane()


def test_overview_aggregates_coverage_alongside_the_existing_modules():
    response = client.get(
        "/api/control-plane/overview", headers={"Authorization": "Bearer contributor"}
    )
    assert response.status_code == 200
    modules = response.json()["modules"]
    assert modules["coverage"]["available"] is True
    called_paths = [call[0] for call in dashboard_app.client.calls]
    assert "/api/v1/observe/coverage" in called_paths


def test_unused_proxy_routes_are_gone():
    headers = {"Authorization": "Bearer contributor"}
    assert client.get("/api/control-plane/structure", headers=headers).status_code == 404
    assert client.get("/api/control-plane/reorganisation/tree", headers=headers).status_code == 200
