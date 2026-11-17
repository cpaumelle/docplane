"""WORK read tools: typed GETs only, redaction preserved, explicit pagination."""
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
os.environ.setdefault("DOCPLANE_AUDIT_LOG", os.devnull)


def load():
    spec = importlib.util.spec_from_file_location("docplane_mcp_under_test", HERE / "docplane_mcp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


IID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
OTHER = "66666666-7777-4888-9999-aaaaaaaaaaaa"
ALLOWED = [
    re.compile(r"^/api/v1/initiatives/[0-9a-f-]{36}/brief$"),
    re.compile(r"^/api/v1/initiatives/[0-9a-f-]{36}\?activity_bodies=redacted$"),
    re.compile(r"^/api/v1/initiatives\?key=[a-z0-9_-]+&include_closed=true&view=summary&limit=5$"),
]


class Fake:
    def __init__(self, responses):
        self.responses, self.calls = responses, []
        self.principal_id = "synthetic"

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        for pattern, value in self.responses:
            if re.search(pattern, path):
                value = value() if callable(value) else value
                return 200, json.loads(json.dumps(value)), 100
        raise AssertionError("unexpected request " + path)


def brief(iid=IID):
    return {"contract": "work-brief-v1", "context_token": "synthetic-context",
            "initiative": {"initiative_id": iid, "version": 3}, "items": [],
            "recent_activity": [{"activity_id": "a", "activity_type": "NOTE", "body": "x"}]}


def history(n, redacted=True, body="b"):
    v = {"initiative_id": IID, "initiative_key": "k", "title": "t", "version": 4,
         "links": [], "dependencies": [{"initiative_id": OTHER, "dependency_kind": "BLOCKS"}],
         "activities": [{"activity_id": str(i), "body": body, "body_withheld": False}
                        for i in range(n)]}
    if redacted:
        v.update(activity_bodies="redacted", activity_metadata="allowlisted-v1")
    return v


@pytest.fixture
def mod():
    return load()


def use(mod, responses):
    fake = Fake(responses)
    mod.CLIENT = fake
    return fake


def assert_only_allowed_gets(fake):
    assert fake.calls
    for method, path, body in fake.calls:
        assert method == "GET" and body is None
        assert any(p.match(path) for p in ALLOWED), path


def test_tool_list_is_exactly_five_page_tools_plus_two_work_reads(mod):
    assert [t["name"] for t in mod.TOOLS] == [
        "search", "list", "read", "outline", "section", "work_brief", "work_history"]


def test_brief_removes_context_token_and_marks_read_only(mod):
    fake = use(mod, [(r"/brief$", brief)])
    out, status, _n, rev = mod.tool_work_brief({"initiative": IID})
    assert "context_token" not in out and "synthetic-context" not in json.dumps(out)
    assert out["projection"]["read_only"] and out["projection"]["context_token_removed"]
    assert rev == 3
    assert_only_allowed_gets(fake)


def test_brief_refuses_unknown_contract(mod):
    use(mod, [(r"/brief$", {"contract": "other"})])
    with pytest.raises(mod.DocPlaneError):
        mod.tool_work_brief({"initiative": IID})


def test_brief_over_budget_drops_bodies_and_says_so(mod):
    big = brief()
    big["recent_activity"] = [{"activity_id": str(i), "body": "x" * 9000} for i in range(6)]
    use(mod, [(r"/brief$", big)])
    out, *_ = mod.tool_work_brief({"initiative": IID})
    assert out["projection"]["truncated"] is True
    assert all("body" not in a for a in out["recent_activity"])


def test_all_work_is_readable_no_initiative_allowlist(mod):
    use(mod, [(r"/brief$", lambda: brief(OTHER))])
    out, *_ = mod.tool_work_brief({"initiative": OTHER})
    assert out["initiative"]["initiative_id"] == OTHER


def test_exact_key_resolution(mod):
    fake = use(mod, [(r"key=example-initiative", {"initiatives": [
        {"initiative_id": IID, "initiative_key": "example-initiative"}]}),
        (r"/brief$", brief)])
    out, *_ = mod.tool_work_brief({"initiative": "example-initiative"})
    assert out["projection"]["resolved_from_key"] == "example-initiative"
    assert_only_allowed_gets(fake)


def test_near_miss_key_is_refused(mod):
    use(mod, [(r"key=", {"initiatives": [{"initiative_id": IID, "initiative_key": "abc-1"}]})])
    with pytest.raises(mod.DocPlaneError):
        mod.tool_work_brief({"initiative": "abc"})


@pytest.mark.parametrize("ref", [
    "../pages", "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d/../../auth", IID + "?x=1", IID.upper(),
    "https://example.invalid/", "", None, "A" * 97, "key with space"])
def test_unsafe_references_never_reach_the_network(mod, ref):
    fake = use(mod, [])
    with pytest.raises(mod.DocPlaneError):
        mod.tool_work_brief({"initiative": ref})
    with pytest.raises(mod.DocPlaneError):
        mod.tool_work_history({"initiative": ref})
    assert fake.calls == []


def test_history_fails_closed_without_redaction_stamp(mod):
    use(mod, [(r"activity_bodies=redacted$", history(3, redacted=False))])
    with pytest.raises(mod.DocPlaneError):
        mod.tool_work_history({"initiative": IID})


def test_history_pagination_is_explicit(mod):
    fake = use(mod, [(r"activity_bodies=redacted$", history(25))])
    out, *_ = mod.tool_work_history({"initiative": IID, "offset": 10, "limit": 10})
    p = out["pagination"]
    assert [a["activity_id"] for a in out["activities"]] == [str(i) for i in range(10, 20)]
    assert p == {"order": "oldest_first", "offset": 10, "requested_limit": 10, "returned": 10,
                 "total": 25, "budget_truncated": False, "next_offset": 20, "has_more": True}
    out, *_ = mod.tool_work_history({"initiative": IID, "offset": 20, "limit": 10})
    assert out["pagination"]["has_more"] is False and out["pagination"]["next_offset"] is None
    assert out["dependencies"] == [{"initiative_id": OTHER, "dependency_kind": "BLOCKS"}]
    assert_only_allowed_gets(fake)


def test_history_budget_shrinks_page_never_bodies(mod):
    use(mod, [(r"activity_bodies=redacted$", history(10, body="y" * 9000))])
    out, *_ = mod.tool_work_history({"initiative": IID, "limit": 10})
    p = out["pagination"]
    assert p["budget_truncated"] is True and 0 < p["returned"] < 10
    assert p["next_offset"] == p["returned"] and p["has_more"]
    assert all(a["body"] == "y" * 9000 for a in out["activities"])


@pytest.mark.parametrize("args", [{"offset": -1}, {"limit": 0}, {"limit": 51}, {"limit": "x"}])
def test_history_bounds(mod, args):
    use(mod, [(r".", history(1))])
    with pytest.raises(mod.DocPlaneError):
        mod.tool_work_history(dict(initiative=IID, **args))
