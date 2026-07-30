"""Contract tests for the work-domain MCP tools.

The MCP surface must grow additively: the six document tools keep their
names, and the work tools register alongside them with domain-verb prefixes.
"""
from __future__ import annotations

import sys
from pathlib import Path

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
    }


def test_document_tool_names_survive_unchanged():
    mcp = RecordingMCP()
    docs_tools.register(mcp)
    assert {
        "search_docs",
        "read_doc",
        "list_docs",
        "write_doc",
        "archive_doc",
        "resolve_concept",
    } <= set(mcp.tools)


def test_work_capture_is_zero_decision_and_guards_kind():
    mcp = RecordingMCP()
    work_tools.register(mcp)
    result = mcp.tools["work_capture"]("an idea", kind="WRONG")
    assert "error" in result
    triage = mcp.tools["work_triage"]("id", "explode")
    assert "error" in triage
    attach = mcp.tools["work_triage"]("id", "attach")
    assert "error" in attach
