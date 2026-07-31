from __future__ import annotations

from fastapi.testclient import TestClient

import dashboard.app as dashboard_app


class FakeControlPlane:
    def __init__(self):
        self.calls = []

    def get(self, path, *, authorization=None, params=None):
        self.calls.append((path, authorization, params))
        if path == "/api/v1/dashboard/observatory":
            return {"fingerprint": "abc", "review_candidates": {"items": []}}
        if path == "/api/v1/dashboard/observatory/pages":
            return {"fingerprint": "abc", "pages": {"items": [], "has_more": False}}
        if path == "/api/v1/dashboard/observatory/export":
            return {"fingerprint": "abc", "manifest": []}
        return {}


client = TestClient(dashboard_app.app)


def setup_function():
    dashboard_app.client = FakeControlPlane()


def test_observatory_routes_forward_named_cursor_and_identity():
    headers = {"Authorization": "Bearer contributor"}
    response = client.get(
        "/api/control-plane/observatory?candidate_limit=25&candidate_after=opaque",
        headers=headers,
    )
    assert response.status_code == 200
    assert dashboard_app.client.calls[-1] == (
        "/api/v1/dashboard/observatory",
        "Bearer contributor",
        {"candidate_limit": 25, "candidate_after": "opaque"},
    )

    response = client.get(
        "/api/control-plane/observatory/pages?limit=200&after=next&q=prox&knowledge_class=OPERATION&identifier_family=runbook&archive_state=active&dated_only=true",
        headers=headers,
    )
    assert response.status_code == 200
    assert dashboard_app.client.calls[-1][2] == {
        "limit": 200,
        "dated_only": True,
        "depth": "all",
        "after": "next",
        "q": "prox",
        "knowledge_class": "OPERATION",
        "identifier_family": "runbook",
        "archive_state": "active",
    }


def test_pages_route_forwards_the_drill_down_scope_including_the_root():
    headers = {"Authorization": "Bearer contributor"}
    response = client.get(
        "/api/control-plane/observatory/pages?path_prefix=operations/proxmox&depth=direct",
        headers=headers,
    )
    assert response.status_code == 200
    assert dashboard_app.client.calls[-1][2] == {
        "limit": 100,
        "dated_only": False,
        "depth": "direct",
        "path_prefix": "operations/proxmox",
    }

    # The corpus root ("") is a real scope and must not be dropped as falsy.
    response = client.get(
        "/api/control-plane/observatory/pages?path_prefix=&depth=direct",
        headers=headers,
    )
    assert response.status_code == 200
    assert dashboard_app.client.calls[-1][2]["path_prefix"] == ""

    assert client.get(
        "/api/control-plane/observatory/pages?depth=sideways", headers=headers
    ).status_code == 422


def test_observatory_export_is_authenticated_and_server_generated():
    assert client.get("/api/control-plane/observatory/export").status_code == 401
    response = client.get(
        "/api/control-plane/observatory/export",
        headers={"Authorization": "Bearer contributor"},
    )
    assert response.status_code == 200
    assert dashboard_app.client.calls[-1][0] == "/api/v1/dashboard/observatory/export"


def test_information_architecture_preserves_work_and_folds_review_actions():
    html = (dashboard_app._STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (dashboard_app._STATIC / "app.js").read_text(encoding="utf-8")
    for view in ("overview", "work", "explore", "review", "authoring", "history"):
        assert f'data-view="{view}"' in html
    assert 'data-view="freshness"' not in html
    assert 'data-view="reorganisation"' not in html
    assert 'id="verify-prefix"' in html
    assert "VERIFICATION_SCOPE_TOO_LARGE" in javascript
    assert 'class="panel advanced"' in html
    assert 'id="reorg-plans"' in html
    assert 'id="auth-presentation"' in html
    assert '$("auth-presentation").hidden = state === "connected"' in javascript


def test_explore_is_a_drill_down_browser_not_a_flat_dump():
    html = (dashboard_app._STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (dashboard_app._STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="explore-breadcrumbs"' in html
    assert 'id="explore-descendants"' in html
    assert 'id="explore-count"' in html
    # Browsing scopes server-side via path_prefix/depth; the old client-side
    # re-filtering of loaded batches must not come back.
    assert 'params.set("path_prefix", explorePath)' in javascript
    assert 'params.set("depth", "direct")' in javascript
    assert "exploredPages.filter" not in javascript
    assert 'id="explore-classification-summary"' in html
    assert '<option value="__missing__">(missing)</option>' in javascript
    assert "missing_by_section" in javascript
