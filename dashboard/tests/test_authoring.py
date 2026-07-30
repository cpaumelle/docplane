from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import dashboard.authoring as authoring

ROOT = Path(__file__).resolve().parents[2]


class FakeControlPlane:
    def __init__(self):
        self.calls = []

    def get(self, path, *, authorization=None, params=None):
        self.calls.append(("GET", path, authorization, params, None, None))
        if path == "/api/v1/search":
            return {"count": 1, "results": [{"resource_id": "11111111-1111-1111-1111-111111111111"}]}
        if path == "/api/v1/pages":
            return {"pages": [{"resource_id": "11111111-1111-1111-1111-111111111111", "path": "reference/example.md", "title": "Example"}]}
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
        return {
            "change_id": "22222222-2222-2222-2222-222222222222",
            "status": "PUBLISHED" if path.endswith(("/publish", "/replace")) else "DRAFT",
        }


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


def test_inline_replace_uses_canonical_one_call_endpoint():
    resource_id = "11111111-1111-1111-1111-111111111111"
    body = {"expected_revision": "rev-1", "content": "# Updated\n", "purpose": "Correct the page"}
    response = client.post(
        f"/api/control-plane/authoring/pages/{resource_id}/replace",
        headers={"Authorization": "Bearer human-token", "Idempotency-Key": "inline-1"},
        json=body,
    )
    assert response.status_code == 200
    call = authoring.client.calls[-1]
    assert call == (
        "POST",
        f"/api/v1/pages/{resource_id}/replace",
        "Bearer human-token",
        None,
        "inline-1",
        body,
    )


def test_reader_pencil_enters_same_page_visual_mode():
    template = (ROOT / "mkdocs/overrides/main.html").read_text(encoding="utf-8")
    assert "DocPlaneInlineEditor.start" in template
    assert "articleSelector: '.md-content__inner'" in template
    assert "window.open('/dashboard/" not in template
    assert 'src="/assets/inline-editor.js"' in template
    assert 'href="/assets/inline-editor.css"' in template


def test_visual_editor_assets_are_built_locally():
    javascript = ROOT / "dashboard/static/inline-editor.js"
    stylesheet = ROOT / "dashboard/static/inline-editor.css"
    assert javascript.is_file()
    assert stylesheet.is_file()
    assert "DocPlaneInlineEditor" in javascript.read_text(encoding="utf-8")
    assert ".dp-inline-toolbar" in stylesheet.read_text(encoding="utf-8")


def test_unified_front_routes_visual_editor_assets_exactly():
    config = (ROOT / "mkdocs/docplane-front.conf").read_text(encoding="utf-8")
    assert "location = /assets/inline-editor.js" in config
    assert "location = /assets/inline-editor.css" in config


def test_inline_publish_timeout_is_coherent_through_the_unified_front():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    config = (ROOT / "mkdocs/docplane-front.conf").read_text(encoding="utf-8")
    assert "DOCPLANE_API_TIMEOUT: ${DOCPLANE_API_TIMEOUT:-180}" in compose
    assert "proxy_read_timeout 300s;" in config
    assert "proxy_send_timeout 300s;" in config


def test_dashboard_navigation_preserves_exact_page_deep_links_and_browser_history():
    app_js = (ROOT / "dashboard/static/app.js").read_text(encoding="utf-8")
    authoring_js = (ROOT / "dashboard/static/authoring.js").read_text(encoding="utf-8")
    assert "const url = new URL(location.href);" in app_js
    assert "url.hash = requested;" in app_js
    assert 'history.replaceState({ view: requested }, "", url);' in app_js
    assert "else location.hash = view;" in app_js
    assert 'window.addEventListener("hashchange"' in app_js
    assert 'new URLSearchParams(location.search).get("edit")' in authoring_js
    assert 'history.replaceState({}, "", "#" + name)' not in app_js


def test_dashboard_uses_discovered_site_name_and_reciprocal_navigation():
    app_js = (ROOT / "dashboard/static/app.js").read_text(encoding="utf-8")
    dashboard = (ROOT / "dashboard/static/index.html").read_text(encoding="utf-8")
    template = (ROOT / "mkdocs/overrides/main.html").read_text(encoding="utf-8")
    assert 'api("/api/control-plane/discovery")' in app_js
    assert "discovery.site_name" in app_js
    assert "data-product-name" in dashboard
    assert 'href="/" title="Browse documentation"' in dashboard
    assert 'a.href = "/dashboard/"' in template


def test_dashboard_can_bootstrap_a_private_fabric_contributor_session():
    app_js = (ROOT / "dashboard/static/app.js").read_text(encoding="utf-8")
    dashboard = (ROOT / "dashboard/static/index.html").read_text(encoding="utf-8")
    assert 'api("/api/v1/auth/self-issue"' in app_js
    assert 'client_context: "DocPlane browser dashboard"' in app_js
    assert "token = issued.token;" in app_js
    assert 'id="display-name"' in dashboard
    assert 'id="start-session"' in dashboard
    assert "Use existing token" in dashboard


def test_dashboard_has_no_submit_or_review_controls():
    from dashboard.combined_app import app as combined_app

    response = TestClient(combined_app).get("/")
    assert response.status_code == 200
    html = response.text
    assert 'id="authoring-publish"' in html
    assert "Submit for review" not in html
    assert "Approve" not in html
    assert "There is no direct save" not in html
