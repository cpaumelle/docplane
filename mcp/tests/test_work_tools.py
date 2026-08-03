"""Contract tests for the work-domain MCP tools.

The MCP surface must grow additively: the six document tools keep their
names, and the work tools register alongside them with domain-verb prefixes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import docs as docs_tools  # noqa: E402
from tools import work as work_tools  # noqa: E402


class RecordingMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorate


def test_work_tools_register_with_domain_prefix():
    mcp = RecordingMCP()
    work_tools.register(mcp)
    assert set(mcp.tools) == {
        "work_capture",
        "work_inbox",
        "work_triage",
        "work_list",
        "work_get",
        "work_note",
        "work_transition",
        "work_promote",
        "work_dispositions",
        "work_link",
    }


def test_mcp_callers_can_supply_replayable_idempotency_keys():
    mcp = RecordingMCP()
    work_tools.register(mcp)
    import inspect

    for tool in ("work_capture", "work_transition"):
        assert "idempotency_key" in inspect.signature(mcp.tools[tool]).parameters


def test_document_tool_names_survive_unchanged():
    mcp = RecordingMCP()
    docs_tools.register(mcp)
    assert {
        "search_docs",
        "read_doc",
        "read_doc_outline",
        "read_doc_section",
        "patch_doc",
        "list_docs",
        "know_classify_doc",
        "write_doc",
        "archive_doc",
        "resolve_concept",
    } <= set(mcp.tools)


def test_patch_doc_forwards_caller_revision_and_idempotency(monkeypatch):
    mcp = RecordingMCP()
    docs_tools.register(mcp)
    calls = []

    def fake_request(method, path, *, body=None, idempotency_key=None):
        calls.append((method, path, body, idempotency_key))
        if path.startswith("/api/v1/pages?"):
            return 200, {"pages": [{"resource_id": "page-1", "path": "x.md", "revision": "newer"}]}
        return 200, {"status": "PUBLISHED"}

    monkeypatch.setattr(docs_tools, "_request", fake_request)
    result = mcp.tools["patch_doc"](
        "x.md", "revision-from-earlier-read",
        [{"old_text": "before", "new_text": "after", "expected_occurrences": 1}],
        "fix wording", "stable-key",
    )
    assert result == {"status": "PUBLISHED"}
    mutation = calls[-1]
    assert mutation[1] == "/api/v1/pages/page-1/patch"
    assert mutation[2]["expected_revision"] == "revision-from-earlier-read"
    assert mutation[3] == "stable-key"


def test_know_classify_tool_wraps_trust_verb_and_converges(monkeypatch):
    mcp = RecordingMCP()
    docs_tools.register(mcp)
    calls = []

    def fake_request(method, path, *, body=None, idempotency_key=None):
        calls.append((method, path, body, idempotency_key))
        if path.startswith("/api/v1/pages?"):
            return 200, {"pages": [{"resource_id": "page-1", "path": "operations/x.md"}]}
        if path.endswith("/trust"):
            return 200, {
                "resource_id": "page-1", "path": "operations/x.md", "metadata_version": 4,
                "workspace_id": "workspace-1", "publication_state": "PUBLISHED",
                "knowledge_class": None, "criticality": "NORMAL", "owner_principal_id": None,
                "review_due_at": None, "provenance": "AUTHORED",
            }
        return 200, {"knowledge_class": body["knowledge_class"], "metadata_version": 5}

    monkeypatch.setattr(docs_tools, "_request", fake_request)
    result = mcp.tools["know_classify_doc"]("operations/x.md", "OPERATION", "curated", "stable-key")
    assert result["knowledge_class"] == "OPERATION"
    mutation = calls[-1]
    assert mutation[0:2] == ("POST", "/api/v1/pages/page-1/classification")
    assert mutation[2]["expected_metadata_version"] == 4
    assert mutation[2]["reason"] == "curated (from MCP)"
    assert mutation[3] == "stable-key"


def test_know_classify_tool_rejects_unknown_class_before_api_calls(monkeypatch):
    monkeypatch.setattr(docs_tools, "_request", lambda *args, **kwargs: pytest.fail("API called"))
    mcp = RecordingMCP()
    docs_tools.register(mcp)
    result = mcp.tools["know_classify_doc"]("x.md", "TYPO", "reason")
    assert result["error"] == "unknown knowledge_class"


def test_work_capture_is_zero_decision_and_guards_kind():
    mcp = RecordingMCP()
    work_tools.register(mcp)
    result = mcp.tools["work_capture"]("an idea", kind="WRONG")
    assert "error" in result
    triage = mcp.tools["work_triage"]("id", "explode")
    assert "error" in triage
    attach = mcp.tools["work_triage"]("id", "attach")
    assert "error" in attach
