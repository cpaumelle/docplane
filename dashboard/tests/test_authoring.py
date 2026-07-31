from __future__ import annotations

from pathlib import Path
import subprocess
import textwrap

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


def test_authoring_exposes_governed_nullable_knowledge_class_select():
    html = (ROOT / "dashboard/static/index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "dashboard/static/authoring.js").read_text(encoding="utf-8")
    assert 'id="authoring-knowledge-class"' in html
    assert '<option value="">(unclassified)</option>' in html
    assert "/classification`" in javascript
    assert "from Authoring" in javascript
    assert "expected_metadata_version:trust.metadata_version" in javascript


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
    # The Docs | Dashboard switcher is one shared markup contract on both
    # surfaces, styled by the single shared /assets/header.css.
    assert 'href="/dashboard/"' in template
    assert '"topnav"' in template
    assert 'class="topnav"' in dashboard
    header_css = (ROOT / "mkdocs/overrides/assets/header.css").read_text(encoding="utf-8")
    assert ".topbar" in header_css and ".md-header__inner" in header_css
    assert '/assets/header.css' in dashboard


def test_dashboard_authentication_state_machine_behaviour():
    app_path = ROOT / "dashboard/static/app.js"
    dashboard = (ROOT / "dashboard/static/index.html").read_text(encoding="utf-8")
    harness = textwrap.dedent(
        f"""
        const assert = require("node:assert/strict");
        require({str(app_path)!r});
        const {{createAuthentication, startDashboardAuthentication, TOKEN_KEY}} = globalThis.DocPlaneAuth;

        class Storage {{
          constructor(token = "") {{ this.values = new Map(token ? [[TOKEN_KEY, token]] : []); }}
          getItem(key) {{ return this.values.get(key) || null; }}
          setItem(key, value) {{ this.values.set(key, value); }}
          removeItem(key) {{ this.values.delete(key); }}
        }}
        const error = (status, message = "rejected") => Object.assign(new Error(message), {{status}});
        const privateDiscovery = {{
          authentication: {{token_acquisition: {{
            self_service: true,
            endpoint: "/advertised/bootstrap",
            method: "POST",
            access_profile: "private_fabric",
            procedure: "automatic",
          }}}},
        }};
        function makeRequest({{discovery = privateDiscovery, valid = [], issueError = null}} = {{}}) {{
          const calls = [];
          const request = async (path, options = {{}}) => {{
            calls.push({{path, options}});
            if (path === "/api/control-plane/discovery") return discovery;
            if (path === "/advertised/bootstrap") {{
              if (issueError) throw issueError;
              return {{token: "issued-token", principal_kind: "AGENT", role: "CONTRIBUTOR"}};
            }}
            if (path === "/api/control-plane/capabilities") {{
              const token = options.headers?.Authorization?.replace("Bearer ", "");
              if (![...valid, "issued-token"].includes(token)) throw error(401);
              return {{principal: {{display_name: "browser-agent", role: "CONTRIBUTOR"}}}};
            }}
            if (path === "/api/control-plane/overview") return {{modules: {{structure: {{data: {{summary: {{active_pages: 3}}}}}}}}}};
            throw error(404, path);
          }};
          return {{request, calls}};
        }}
        const count = (calls, path) => calls.filter((call) => call.path === path).length;

        (async () => {{
          // Valid cached token: validate, do not mint, then load overview.
          {{
            const storage = new Storage("cached");
            const {{request, calls}} = makeRequest({{valid: ["cached"]}});
            const auth = createAuthentication({{request, storage}});
            await startDashboardAuthentication(auth, () => request("/api/control-plane/overview"));
            assert.equal(count(calls, "/advertised/bootstrap"), 0);
            assert.equal(count(calls, "/api/control-plane/overview"), 1);
          }}

          // Invalid cached token: fully clear, mint exactly once, validate, store, load.
          {{
            const storage = new Storage("expired");
            let clears = 0;
            const {{request, calls}} = makeRequest();
            const auth = createAuthentication({{request, storage, onCleared: () => clears++}});
            await startDashboardAuthentication(auth, () => request("/api/control-plane/overview"));
            assert.equal(clears, 1);
            assert.equal(count(calls, "/advertised/bootstrap"), 1);
            assert.equal(storage.getItem(TOKEN_KEY), "issued-token");
            assert.equal(auth.state(), "connected");
            assert.equal(count(calls, "/api/control-plane/overview"), 1);
          }}

          // No cache and concurrent initialization: discovery and advertised issuance are single-flight.
          {{
            const storage = new Storage();
            const {{request, calls}} = makeRequest();
            const auth = createAuthentication({{request, storage}});
            await Promise.all([auth.initialize(), auth.initialize(), auth.initialize()]);
            assert.equal(count(calls, "/api/control-plane/discovery"), 1);
            assert.equal(count(calls, "/advertised/bootstrap"), 1);
            assert.equal(JSON.parse(calls.find((call) => call.path === "/advertised/bootstrap").options.body).display_name, undefined);
          }}

          // Managed policy: never self-issue and expose the operator procedure.
          {{
            const states = [];
            const discovery = {{authentication: {{token_acquisition: {{self_service: false, procedure: "Ask the operator for a named token."}}}}}};
            const {{request, calls}} = makeRequest({{discovery}});
            const auth = createAuthentication({{request, storage: new Storage(), onState: (state, detail) => states.push({{state, detail}})}});
            assert.equal(await auth.initialize(), false);
            assert.equal(count(calls, "/advertised/bootstrap"), 0);
            assert.equal(states.at(-1).state, "managed-token-required");
            assert.match(states.at(-1).detail.procedure, /operator/);
          }}

          // Generic and rate-limit failures remain bounded and truthful.
          for (const status of [500, 429]) {{
            const states = [];
            const {{request, calls}} = makeRequest({{issueError: error(status)}});
            const auth = createAuthentication({{request, storage: new Storage(), onState: (state, detail) => states.push({{state, detail}})}});
            await Promise.all([auth.initialize(), auth.initialize()]);
            assert.equal(count(calls, "/advertised/bootstrap"), 1);
            assert.equal(auth.state(), "bootstrap-failed");
            assert.match(states.at(-1).detail.message, status === 429 ? /rate limited/ : /routed DocPlane URL/);
            assert.equal(auth.token(), "");
          }}

          // A 401 or 403 after connection gets one bounded, single-flight replacement.
          for (const status of [401, 403]) {{
            const storage = new Storage("cached");
            const {{request, calls}} = makeRequest({{valid: ["cached"]}});
            const auth = createAuthentication({{request, storage}});
            await auth.initialize();
            await Promise.all([
              auth.handleRejectedStatus(status),
              auth.handleRejectedStatus(status),
              auth.handleRejectedStatus(status),
            ]);
            assert.equal(count(calls, "/advertised/bootstrap"), 1);
            assert.equal(storage.getItem(TOKEN_KEY), "issued-token");
            auth.clear();
            assert.equal(auth.token(), "");
            assert.equal(storage.getItem(TOKEN_KEY), null);
          }}
        }})().catch((failure) => {{ console.error(failure); process.exit(1); }});
        """
    )
    subprocess.run(["node", "-e", harness], check=True, cwd=ROOT)
    app_js = app_path.read_text(encoding="utf-8")
    assert "localStorage" not in app_js
    assert 'id="auth-status"' in dashboard
    assert 'id="auth-fallback"' in dashboard
    assert 'id="display-name"' not in dashboard
    assert 'id="start-session"' not in dashboard
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
