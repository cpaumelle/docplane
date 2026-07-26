from __future__ import annotations

import pathlib

from fastapi.testclient import TestClient

from dashboard import app as dashboard_app


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, path, *, authorization=None, params=None):
        self.calls.append(("GET", path, authorization, None, params))
        if path == "/api/v1/search":
            return {
                "count": 1,
                "results": [
                    {
                        "resource_id": "11111111-1111-1111-1111-111111111111",
                        "title": "Backups",
                        "path": "reference/backups.md",
                    }
                ],
            }
        if path.startswith("/api/v1/pages/"):
            return {
                "resource_id": path.rsplit("/", 1)[-1],
                "revision": "22222222-2222-2222-2222-222222222222",
                "path": "reference/backups.md",
                "title": "Backups",
                "content": "# Backups {#backups}\n\nCurrent source.\n",
                "outline": [
                    {
                        "heading_id": "backups",
                        "title": "Backups",
                        "level": 1,
                        "start_line": 1,
                        "end_line": 3,
                        "content_hash": "a" * 64,
                        "explicit_id": True,
                    }
                ],
            }
        raise AssertionError(path)

    def post(self, path, *, authorization, idempotency_key=None, json_body=None):
        self.calls.append(("POST", path, authorization, idempotency_key, json_body))
        if path == "/api/v1/authoring/preview":
            return {
                "workspace_key": "reference",
                "operation": {
                    "operation_type": "REPLACE_DOCUMENT",
                    "page_resource_id": "11111111-1111-1111-1111-111111111111",
                    "expected_revision": "22222222-2222-2222-2222-222222222222",
                    "payload": {"content": "updated"},
                },
                "rendered_html": "<h1>Backups</h1>",
                "raw_diff": "diff",
                "semantic_diff": [],
                "diagnostics": [],
            }
        if path == "/api/v1/changes":
            return {"change_id": "33333333-3333-3333-3333-333333333333", "status": "DRAFT"}
        if path.endswith("/operations"):
            return {"change_id": "33333333-3333-3333-3333-333333333333", "status": "DRAFT"}
        if path.endswith("/validate"):
            return {"change_id": "33333333-3333-3333-3333-333333333333", "status": "VALIDATED"}
        if path.endswith("/submit"):
            return {"change_id": "33333333-3333-3333-3333-333333333333", "status": "SUBMITTED"}
        raise AssertionError(path)


def test_authoring_workspace_is_first_class_and_locally_bundled(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(dashboard_app, "client", fake)
    client = TestClient(dashboard_app.app)

    response = client.get("/authoring")
    assert response.status_code == 200
    html = response.text
    assert "/assets/editor.bundle.js" in html
    assert "/assets/authoring.js" in html
    assert '<iframe id="preview-frame"' in html
    assert "sandbox" in html
    assert "direct save" in html.lower()

    static = pathlib.Path(__file__).resolve().parents[1] / "static"
    script = (static / "authoring.js").read_text(encoding="utf-8")
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "iframe.srcdoc" not in script  # assignment is through the named previewFrame element
    assert "previewFrame.srcdoc" in script


def test_authoring_facade_preserves_identity_and_idempotency(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(dashboard_app, "client", fake)
    client = TestClient(dashboard_app.app)
    auth = {"Authorization": "Bearer named-human"}
    resource_id = "11111111-1111-1111-1111-111111111111"
    change_id = "33333333-3333-3333-3333-333333333333"

    search = client.get("/api/control-plane/authoring/search?q=backups", headers=auth)
    assert search.status_code == 200
    assert fake.calls[-1] == (
        "GET",
        "/api/v1/search",
        "Bearer named-human",
        None,
        {"q": "backups", "limit": 20},
    )

    page = client.get(f"/api/control-plane/authoring/pages/{resource_id}", headers=auth)
    assert page.status_code == 200
    assert fake.calls[-1][1] == f"/api/v1/pages/{resource_id}"
    assert fake.calls[-1][4] == {"view": "edit_context"}

    preview = client.post(
        "/api/control-plane/authoring/preview",
        headers=auth,
        json={"page_resource_id": resource_id, "expected_revision": "revision", "content": "updated"},
    )
    assert preview.status_code == 200
    assert fake.calls[-1][1] == "/api/v1/authoring/preview"
    assert fake.calls[-1][2] == "Bearer named-human"

    created = client.post(
        "/api/control-plane/authoring/changes",
        headers={**auth, "Idempotency-Key": "human-change-1"},
        json={"title": "Update Backups", "purpose": "Correct recovery order", "workspace_key": "reference"},
    )
    assert created.status_code == 201
    assert fake.calls[-1][3] == "human-change-1"

    operation = client.post(
        f"/api/control-plane/authoring/changes/{change_id}/operations",
        headers={**auth, "Idempotency-Key": "human-operation-1"},
        json={"operation_type": "REPLACE_DOCUMENT", "payload": {"content": "updated"}},
    )
    assert operation.status_code == 201
    assert fake.calls[-1][3] == "human-operation-1"

    validated = client.post(
        f"/api/control-plane/authoring/changes/{change_id}/validate",
        headers=auth,
    )
    assert validated.status_code == 200
    assert validated.json()["status"] == "VALIDATED"

    submitted = client.post(
        f"/api/control-plane/authoring/changes/{change_id}/submit",
        headers=auth,
        json={"note": "Ready for review"},
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "SUBMITTED"
    assert all(call[2] == "Bearer named-human" for call in fake.calls)


def test_authoring_requires_a_named_principal(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(dashboard_app, "client", fake)
    client = TestClient(dashboard_app.app)

    response = client.get("/api/control-plane/authoring/search?q=backups")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "DASHBOARD_AUTH_REQUIRED"
    assert fake.calls == []
