"""Every outgoing request, from every tool, has an allowed fixed route shape."""
import importlib.util
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

HERE = Path(__file__).resolve().parent.parent
os.environ.setdefault("DOCPLANE_AUDIT_LOG", os.devnull)

PAGE = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
INI = "66666666-7777-4888-9999-aaaaaaaaaaaa"
U = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
SHAPES = [
    (rf"^/api/v1/pages/{U}$", r"^view=(summary|outline|full)$"),
    (rf"^/api/v1/pages/{U}/sections/[A-Za-z0-9_-]+$", r"^$"),
    (r"^/api/v1/resolve$", r"^path=[^&]+$"),
    (r"^/api/v1/search$", r".*"),
    (r"^/api/v1/pages$", r".*"),
    (r"^/api/v1/initiatives$", r"^key=[a-z0-9_-]+&include_closed=true&view=summary&limit=5$"),
    (rf"^/api/v1/initiatives/{U}/brief$", r"^$"),
    (rf"^/api/v1/initiatives/{U}$", r"^activity_bodies=redacted$"),
]


def load():
    spec = importlib.util.spec_from_file_location("shim_routes", HERE / "docplane_mcp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Fake:
    principal_id = "synthetic"

    def __init__(self, page_rid=PAGE):
        self.calls = []
        self.page_rid = page_rid

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        route = urlsplit(path).path
        page = {"resource_id": self.page_rid, "path": "guides/x.md", "revision": "r",
                "content": "# T", "outline": [{"heading_id": "t", "level": 1}]}
        if route == "/api/v1/resolve":
            return 200, {"count": 1, "matches": [page]}, 10
        if "/sections/" in route:
            return 200, {"heading_id": "t", "content": "x"}, 10
        if route.startswith("/api/v1/pages/"):
            return 200, page, 10
        if route == "/api/v1/pages":
            return 200, {"pages": [], "total": 0}, 10
        if route == "/api/v1/search":
            return 200, {"results": [], "total": 0}, 10
        if route == "/api/v1/initiatives":
            return 200, {"initiatives": []}, 10
        if route.endswith("/brief"):
            return 200, {"contract": "work-brief-v1", "initiative": {}}, 10
        return 200, {"activity_bodies": "redacted", "activity_metadata": "allowlisted-v1",
                     "activities": []}, 10


def assert_shapes(calls):
    for method, path, body in calls:
        assert method == "GET" and body is None, path
        parts = urlsplit(path)
        assert ".." not in parts.path and "%2f" not in parts.path.lower(), path
        assert any(re.match(p, parts.path) and re.match(q, parts.query) for p, q in SHAPES), path


HOSTILE = [
    "../../../initiatives/" + INI, "../" * 6 + "auth/self-issue", "x/../../y", "%2e%2e/%2e%2e",
    "a/b", "a?b=c", "a#b", "a b", "", "é", "x" * 201, "/", "\\..\\..", "urn:uuid:" + PAGE,
    "{" + PAGE + "}", PAGE.replace("-", ""), PAGE + "/../../x", PAGE.upper(), None, 7,
]


@pytest.fixture
def mod():
    m = load()
    m.CLIENT = Fake()
    return m


@pytest.mark.parametrize("bad", HOSTILE)
def test_hostile_inputs_to_every_tool_keep_fixed_route_shapes(mod, bad):
    for tool in mod.TOOLS:
        for field in ("path", "heading_id", "initiative", "query", "path_prefix", "workspace_key"):
            args = {"path": "guides/x.md", "heading_id": "t", "initiative": INI, "query": "q"}
            args[field] = bad
            try:
                tool["fn"](args)
            except mod.DocPlaneError:
                pass
    assert_shapes(mod.CLIENT.calls)


@pytest.mark.parametrize("bad", ["../../../initiatives/" + INI, "a/b", "a b", "", None, "x" * 201, "é"])
def test_invalid_heading_id_is_refused_before_any_request(mod, bad):
    with pytest.raises(mod.DocPlaneError):
        mod.tool_docplane_section({"path": "guides/x.md", "heading_id": bad})
    assert mod.CLIENT.calls == []


@pytest.mark.parametrize("ok", ["development-instance-vm8555", "Explicit_ID-2", "section", "a"])
def test_ordinary_heading_ids_reach_the_section_route(mod, ok):
    mod.tool_docplane_section({"path": "guides/x.md", "heading_id": ok})
    assert mod.CLIENT.calls[-1][1] == "/api/v1/pages/%s/sections/%s" % (PAGE, ok)
    assert_shapes(mod.CLIENT.calls)


def test_canonical_uuid_page_reference_is_used_in_canonical_form(mod):
    mod.tool_docplane_outline({"path": PAGE.upper()})
    assert mod.CLIENT.calls[0][1] == "/api/v1/pages/%s?view=summary" % PAGE
    assert_shapes(mod.CLIENT.calls)


@pytest.mark.parametrize("ref", ["urn:uuid:" + PAGE, "{" + PAGE + "}", PAGE.replace("-", "")])
def test_non_canonical_uuid_forms_only_reach_resolve_as_a_query(mod, ref):
    mod.tool_docplane_outline({"path": ref})
    assert urlsplit(mod.CLIENT.calls[0][1]).path == "/api/v1/resolve"
    assert_shapes(mod.CLIENT.calls)


@pytest.mark.parametrize("rid", ["../../initiatives/" + INI, "", None, "not-a-uuid", PAGE + "/x"])
def test_untrusted_server_resource_id_is_never_a_path_segment(rid):
    m = load()
    m.CLIENT = Fake(page_rid=rid)
    for name, args in (("read", {"path": "guides/x.md"}), ("outline", {"path": "guides/x.md"}),
                       ("section", {"path": "guides/x.md", "heading_id": "t"})):
        with pytest.raises(m.DocPlaneError):
            m.TOOLS_BY_NAME[name]["fn"](args)
    assert_shapes(m.CLIENT.calls)
