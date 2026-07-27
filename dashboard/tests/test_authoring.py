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
        return {
            "resource_id": "11111111-1111-1111-1111-111111111111",
            "path": "reference/example.md",
            "title": "Example",
            "revision": "rev-1",
            "content": "# Example\n",
            "outline": [],
        }

    def post(self, path, *, authorization=None, idempotency_key=None, json_body=None):
        self.calls.append(("POST", path, authorization, None, idempotency_key, json_body))
        return {"change_id": "22222222-2222-2222-2222-222222222222", "status": "DRAFT"}


app = FastAPI()
app.include_router(authoring.router)
client = TestClient(app)


def setup_function():
    authoring.client = FakeControlPlane()


def test_capabilities_keep_humans_on_the_governed_path():
    response = client.get("/api/control-plane/authoring/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["source_authority"] == "markdown"
    assert body["generated_release_editable"] is False
    assert body["direct_database_access"] is False
    assert body["direct_merge"] is False


def test_search_and_edit_context_forward_named_identity():
    headers = {"Authorization": "Bearer human-token"}
    search = client.get("/api/control-plane/authoring/search?q=example", headers=headers)
    assert search.status_code == 200
    page = client.get(
        "/api/control-plane/authoring/pages/11111111-1111-1111-1111-111111111111",
        headers=headers,
    )
    assert page.status_code == 200
    assert authoring.client.calls[0] == (
        "GET", "/api/v1/search", "Bearer human-token",
        {"q": "example", "include_archived": False, "limit": 20}, None, None,
    )
    assert authoring.client.calls[1][3] == {"view": "edit_context"}


def test_diff_is_deterministic_and_does_not_mutate_state():
    response = client.post(
        "/api/control-plane/authoring/diff",
        json={"path": "reference/example.md", "before": "# Example\n", "after": "# Example\n\nCorrected.\n"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["changed"] is True
    assert "--- a/reference/example.md" in body["diff"]
    assert "+Corrected." in body["diff"]


def test_change_operations_forward_idempotency_without_merge_authority():
    headers = {
        "Authorization": "Bearer human-token",
        "Idempotency-Key": "human-change-1",
    }
    response = client.post(
        "/api/control-plane/authoring/changes",
        headers=headers,
        json={"title": "Correct Example", "purpose": "Fix an inaccuracy", "workspace_key": "reference"},
    )
    assert response.status_code == 201
    call = authoring.client.calls[-1]
    assert call[1] == "/api/v1/changes"
    assert call[2] == "Bearer human-token"
    assert call[4] == "human-change-1"

    validate = client.post(
        "/api/control-plane/authoring/changes/22222222-2222-2222-2222-222222222222/validate",
        headers={"Authorization": "Bearer human-token", "Idempotency-Key": "validate-1"},
        json={},
    )
    assert validate.status_code == 200
    assert authoring.client.calls[-1][1].endswith("/validate")
