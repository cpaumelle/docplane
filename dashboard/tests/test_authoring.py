from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import dashboard.authoring as authoring


class FakeControlPlane:
    def __init__(self):
        self.calls = []

    def get(self, path, *, authorization=None, params=None):
        self.calls.append(("GET", path, authorization, params, None, None))
        if path == "/api/v1/search":
            return {"count": 1, "results": [{"resource_id": "11111111-1111-1111-1111-111111111111"}]}
        if path.endswith("/history"):
            return {"versions": []}
        return {
            "resource_id": "11111111-1111-1111-1111-111111111111",
            "path": "reference/example.md",
            "title": "Example",
            "nav_path": "Reference/Example",
            "workspace_key": "reference",
            "revision": "rev-1",
            "version": 1,
            "content": "# Example\n",
            "outline": [],
        }

    def post(self, path, *, authorization=None, idempotency_key=None, json_body=None):
        self.calls.append(("POST", path, authorization, None, idempotency_key, json_body))
        return {"change_id": "22222222-2222-2222-2222-222222222222", "status": "PUBLISHED" if path.endswith("/publish") else "DRAFT"}


app = FastAPI()
app.include_router(authoring.router)
client = TestClient(app)


def setup_function():
    authoring.client = FakeControlPlane()


def test_capabilities_define_direct_publish_without_review_gate():
    body = client.get("/api/control-plane/authoring/capabilities").json()
    assert body["workflow"][-1] == "PUBLISH"
    assert body["review_required"] is False
    assert body["exact_revision_required"] is True
    assert body["version_history"] is True


def test_search_and_edit_context_forward_identity():
    headers = {"Authorization": "Bearer human-token"}
    assert client.get("/api/control-plane/authoring/search?q=example", headers=headers).status_code == 200
    assert client.get("/api/control-plane/authoring/pages/11111111-1111-1111-1111-111111111111", headers=headers).status_code == 200
    assert authoring.client.calls[0][2] == "Bearer human-token"
    assert authoring.client.calls[1][3] == {"view": "edit_context"}


def test_publish_is_forwarded_with_idempotency():
    response = client.post(
        "/api/control-plane/authoring/changes/22222222-2222-2222-2222-222222222222/publish",
        headers={"Authorization": "Bearer human-token", "Idempotency-Key": "publish-1"},
        json={},
    )
    assert response.status_code == 200
    call = authoring.client.calls[-1]
    assert call[1].endswith("/publish")
    assert call[4] == "publish-1"


def test_dashboard_has_no_submit_or_review_controls():
    from dashboard.combined_app import app as combined_app

    response = TestClient(combined_app).get("/")
    assert response.status_code == 200
    html = response.text
    assert 'id="authoring-publish"' in html
    assert "Submit for review" not in html
    assert "Approve" not in html
    assert "There is no direct save" not in html
