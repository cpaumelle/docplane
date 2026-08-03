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


def test_information_architecture_is_verb_first_with_one_view_per_domain():
    html = (dashboard_app._STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (dashboard_app._STATIC / "app.js").read_text(encoding="utf-8")
    for view in ("overview", "work", "explore", "review", "classify", "authoring", "model", "observe", "history"):
        assert f'data-view="{view}"' in html
    # The verbs are navigation, not decoration: the sidebar groups by domain.
    for verb in ("work", "know", "model", "observe"):
        assert f'<span class="nav-group">{verb}</span>' in html
    assert 'id="verify-prefix"' in html
    assert "VERIFICATION_SCOPE_TOO_LARGE" in javascript
    assert 'id="reorg-plans"' in html
    assert 'id="auth-presentation"' in html
    assert '$("auth-presentation").hidden = state === "connected"' in javascript
    # Errors surface in the toast; blocking browser dialogs must not come back.
    assert "window.alert" not in javascript


def test_reorganisation_is_staged_from_selection_not_raw_payloads():
    html = (dashboard_app._STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (dashboard_app._STATIC / "app.js").read_text(encoding="utf-8")
    # The agent-shaped form (hand-typed operation JSON, resource IDs and
    # revisions) is gone; humans select pages and pick a target directory.
    assert 'id="operation-payload"' not in html
    assert 'id="operation-type"' not in html
    assert 'id="operation-page-id"' not in html
    assert 'id="plan-title"' not in html
    assert 'id="explore-move-stage"' in html
    assert 'id="explore-selection"' in html
    assert 'id="nav-organizer-body"' in html
    assert 'id="nav-tree"' in html
    assert 'id="nav-undo"' in html
    assert 'id="nav-redo"' in html
    assert 'draggable="${overview ? "false" : "true"}"' in javascript
    assert 'operation_type:moved ? "MOVE_PAGE" : "REPARENT_NAV"' in javascript
    assert "link_impacts" in javascript
    # The builder binds identity server-known values, never typed ones.
    assert "page_resource_id: page.resource_id" in javascript
    assert "expected_revision: String(page.revision)" in javascript
    assert '"MOVE_PAGE"' in javascript


def test_classify_workbench_burns_down_the_backfill():
    html = (dashboard_app._STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (dashboard_app._STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="classify-burndown"' in html
    assert 'id="classify-queue"' in html
    assert 'id="classify-preview"' in html
    # Scope is selectable: backfill of (missing) or accuracy review of a class.
    assert 'id="classify-class-scope"' in html
    assert "missing_by_section" in javascript
    # Assignments reuse the one governed classify call (optimistic-locked).
    assert "classifyCall" in javascript
    assert "expected_metadata_version" in javascript
    # Keyboard triage: 1-8 assign, j/k navigate.
    assert "classifyKeyHandler" in javascript


def test_model_and_observe_views_read_the_domain_apis():
    html = (dashboard_app._STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (dashboard_app._STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="model-entities"' in html
    assert 'id="model-detail"' in html
    assert 'id="observe-unwatched"' in html
    assert 'id="observe-observations"' in html
    assert "/api/v1/model/entities" in javascript
    assert "/api/v1/model/contracts" in javascript
    assert "/api/v1/observe/coverage" in javascript
    assert "/api/v1/observations" in javascript


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
    assert '<option value="__missing__">(missing)</option>' in javascript
