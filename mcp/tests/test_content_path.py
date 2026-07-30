"""write_doc's content_path variant (docplane#88): large documents are read
by the MCP server from an allowlisted exchange directory instead of
transiting the calling agent's context.

The allowlist is the security boundary: anything that resolves outside
DOCPLANE_MCP_CONTENT_DIR — traversal, absolute paths, symlink escapes — must
be refused, or the tool becomes a file-disclosure primitive for whatever the
MCP container can see.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common  # noqa: E402
from tools import docs as docs_tools  # noqa: E402


class RecordingMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorate


def write_doc():
    mcp = RecordingMCP()
    docs_tools.register(mcp)
    return mcp.tools["write_doc"]


def call(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(common, "DOCPLANE_MCP_CONTENT_DIR", str(tmp_path))
    defaults = {"path": "guides/x.md", "title": "X", "nav_path": "Guides / X", "purpose": "test"}
    return write_doc()(**{**defaults, **kwargs})


def test_disabled_by_default_with_a_remedy(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "DOCPLANE_MCP_CONTENT_DIR", "")
    result = write_doc()(path="p", title="t", nav_path="n", purpose="test", content_path="doc.md")
    assert "DOCPLANE_MCP_CONTENT_DIR" in result["error"]
    assert "remedy" in result


def test_purpose_is_always_required(monkeypatch, tmp_path):
    result = call(tmp_path, monkeypatch, purpose="", content="body")
    assert result == {"error": "purpose is required"}


def test_exactly_one_content_source(monkeypatch, tmp_path):
    assert "exactly one" in call(tmp_path, monkeypatch)["error"]
    (tmp_path / "doc.md").write_text("body", encoding="utf-8")
    assert "exactly one" in call(tmp_path, monkeypatch, content="body", content_path="doc.md")["error"]


def test_traversal_and_absolute_paths_are_refused(monkeypatch, tmp_path):
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("credentials", encoding="utf-8")
    result = call(exchange, monkeypatch, content_path="../secret.txt")
    assert "escapes" in result["error"]
    result = call(exchange, monkeypatch, content_path=str(secret))
    assert "escapes" in result["error"]


def test_symlink_escape_is_refused(monkeypatch, tmp_path):
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (exchange / "link.md").symlink_to(outside)
    result = call(exchange, monkeypatch, content_path="link.md")
    assert "escapes" in result["error"]


def test_missing_file_and_size_cap(monkeypatch, tmp_path):
    assert "not found" in call(tmp_path, monkeypatch, content_path="absent.md")["error"]
    big = tmp_path / "big.md"
    big.write_bytes(b"x" * (docs_tools.CONTENT_PATH_MAX_BYTES + 1))
    assert "exceeds" in call(tmp_path, monkeypatch, content_path="big.md")["error"]


def test_valid_file_is_loaded_and_flows_into_the_change(monkeypatch, tmp_path):
    (tmp_path / "doc.md").write_text("# Large page\n" * 3, encoding="utf-8")
    seen = {}

    def fake_request(method, path, *, body=None, idempotency_key=None):
        if method == "GET":
            return 200, {"pages": []}
        seen.setdefault("calls", []).append((method, path, body))
        if path == "/api/v1/changes":
            return 201, {"change_id": "change-1"}
        if path.endswith("/operations"):
            return 201, {"ok": True}
        if path.endswith("/validate"):
            return 200, {"validation_summary": {"passed": True}}
        return 200, {"published": True}

    monkeypatch.setattr(docs_tools, "_request", fake_request)
    result = call(tmp_path, monkeypatch, content_path="doc.md")
    assert result == {"published": True}
    operation = next(body for method, path, body in seen["calls"] if path.endswith("/operations"))
    assert operation["payload"]["content"] == "# Large page\n" * 3
