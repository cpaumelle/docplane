"""Redaction fail-closed guards and heading-anchored section tools (docplane#54).

`main` already requires caller-owned revisions and offers text-anchored bounded
edits (patch_doc). These tests cover the two pieces that were missing: the MCP
layer must fail closed around migration redaction markers (`<REDACTED:...>`,
sanitised bytes that publication does not rehydrate), and it must expose the
heading-anchored REPLACE_SECTION / INSERT_*_HEADING operations with exact
revision and section-hash concurrency.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import docs as docs_tools  # noqa: E402


class RecordingMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorate


def _tools():
    mcp = RecordingMCP()
    docs_tools.register(mcp)
    return mcp.tools


PAGE = {
    "resource_id": "11111111-1111-1111-1111-111111111111",
    "path": "reference/example.md",
    "title": "Example",
    "nav_path": "Reference/Example",
    "revision": "revision-current",
    "workspace_key": "reference",
    "status": "active",
}

CLEAN_CONTENT = (
    "# Example\n\n"
    "## Acceptance {#acceptance}\n\n"
    "Current acceptance text.\n\n"
    "```markdown\n"
    "## Not a real section {#fake-inside-fence}\n"
    "```\n\n"
    "## Other {#other}\n\n"
    "Other text.\n"
)


def _router(*, page=PAGE, content=CLEAN_CONTENT, revision="revision-current", mutations):
    """Build a fake _request. `mutations` records every non-GET call."""

    def fake_request(method, path, *, body=None, idempotency_key=None):
        if method == "GET":
            if "view=edit_context" in path:
                return 200, {"resource_id": page["resource_id"], "revision": revision, "content": content}
            if path.startswith("/api/v1/pages"):
                return 200, {"pages": [page]}
            if path.startswith("/api/v1/search"):
                return 200, {"results": []}
            return 200, {}
        mutations.append((method, path, body))
        if path == "/api/v1/changes":
            return 201, {"change_id": "change-1"}
        if path.endswith("/operations"):
            return 201, {"status": "DRAFT"}
        if path.endswith("/validate"):
            return 200, {"validation_summary": {"passed": True}}
        if path.endswith("/publish"):
            return 200, {"status": "PUBLISHED"}
        if path.endswith("/abandon"):
            return 200, {"status": "ABANDONED"}
        raise AssertionError((method, path))

    return fake_request


# --- read_doc redaction metadata -------------------------------------------

def test_read_doc_reports_no_redactions_on_a_clean_page(monkeypatch):
    monkeypatch.setattr(docs_tools, "_request", _router(mutations=[]))
    result = _tools()["read_doc"]("reference/example.md")
    assert result["redactions_present"] is False
    assert result["redaction_marker_count"] == 0
    assert result["full_document_replace_allowed"] is True


def test_read_doc_flags_redactions_and_disallows_full_replace(monkeypatch):
    content = CLEAN_CONTENT + "\n<REDACTED:SSH_PASSWORD:FAMILY-0027>\n"
    monkeypatch.setattr(docs_tools, "_request", _router(content=content, mutations=[]))
    result = _tools()["read_doc"]("reference/example.md")
    assert result["redactions_present"] is True
    assert result["redaction_marker_count"] == 1
    assert result["full_document_replace_allowed"] is False


# --- write_doc redaction guards --------------------------------------------

def test_write_doc_rejects_marker_in_submitted_content_before_mutation(monkeypatch):
    mutations: list = []
    monkeypatch.setattr(docs_tools, "_request", _router(mutations=mutations))
    result = _tools()["write_doc"](
        path="reference/example.md",
        title="Example",
        nav_path="Reference/Example",
        content="# Example\n\n<REDACTED:SSH_PASSWORD:FAMILY-0027>\n",
        purpose="Do not author markers",
        expected_revision="revision-current",
    )
    assert result["error"] == "redaction_marker_in_submitted_content"
    assert mutations == []


def test_write_doc_refuses_full_replace_of_a_redacted_page(monkeypatch):
    content = CLEAN_CONTENT + "\n<REDACTED:ENV_SECRET_ASSIGNMENT:FAMILY-0014>\n"
    mutations: list = []
    monkeypatch.setattr(docs_tools, "_request", _router(content=content, mutations=mutations))
    result = _tools()["write_doc"](
        path="reference/example.md",
        title="Example",
        nav_path="Reference/Example",
        content="# Example\n\nApparently clean reconstruction.\n",
        purpose="Unsafe full rewrite",
        expected_revision="revision-current",
    )
    assert result["error"] == "redacted_full_document_replace_forbidden"
    assert result["redaction_marker_count"] == 1
    assert mutations == []


def test_write_doc_replaces_a_clean_page(monkeypatch):
    mutations: list = []
    monkeypatch.setattr(docs_tools, "_request", _router(mutations=mutations))
    result = _tools()["write_doc"](
        path="reference/example.md",
        title="Example",
        nav_path="Reference/Example",
        content="# Example\n\nCorrected.\n",
        purpose="Correct the example",
        expected_revision="revision-current",
    )
    assert result["status"] == "PUBLISHED"
    operation = next(body for method, path, body in mutations if path.endswith("/operations"))
    assert operation["operation_type"] == "REPLACE_DOCUMENT"
    assert operation["expected_revision"] == "revision-current"


# --- patch_doc redaction guard ---------------------------------------------

def test_patch_doc_rejects_marker_in_an_edit(monkeypatch):
    mutations: list = []
    monkeypatch.setattr(docs_tools, "_request", _router(mutations=mutations))
    result = _tools()["patch_doc"](
        path="reference/example.md",
        expected_revision="revision-current",
        edits=[{"old_text": "Current acceptance text.", "new_text": "<REDACTED:SSH_PASSWORD:FAMILY-0027>"}],
        purpose="Do not patch in a marker",
    )
    assert result["error"] == "redaction_marker_in_submitted_content"
    assert mutations == []


# --- heading-anchored section tools ----------------------------------------

def test_replace_doc_section_sends_revision_and_section_hash(monkeypatch):
    mutations: list = []
    monkeypatch.setattr(docs_tools, "_request", _router(mutations=mutations))
    result = _tools()["replace_doc_section"](
        "reference/example.md",
        "acceptance",
        "revision-current",
        "section-hash-read-by-caller",
        "## Acceptance {#acceptance}\n\nUpdated.\n",
        "Update one bounded section",
    )
    assert result["status"] == "PUBLISHED"
    operation = next(body for method, path, body in mutations if path.endswith("/operations"))
    assert operation["operation_type"] == "REPLACE_SECTION"
    assert operation["expected_revision"] == "revision-current"
    assert operation["expected_section_hash"] == "section-hash-read-by-caller"
    assert operation["payload"]["heading_id"] == "acceptance"


def test_insert_after_heading_uses_its_operation_type(monkeypatch):
    mutations: list = []
    monkeypatch.setattr(docs_tools, "_request", _router(mutations=mutations))
    result = _tools()["insert_doc_after_heading"](
        "reference/example.md",
        "acceptance",
        "revision-current",
        "section-hash-read-by-caller",
        "Appended paragraph.\n",
        "Insert after a section",
    )
    assert result["status"] == "PUBLISHED"
    operation = next(body for method, path, body in mutations if path.endswith("/operations"))
    assert operation["operation_type"] == "INSERT_AFTER_HEADING"


def test_section_edit_is_a_conflict_on_stale_revision(monkeypatch):
    mutations: list = []
    monkeypatch.setattr(docs_tools, "_request", _router(revision="revision-current", mutations=mutations))
    result = _tools()["replace_doc_section"](
        "reference/example.md",
        "acceptance",
        "revision-stale",
        "section-hash-read-by-caller",
        "## Acceptance {#acceptance}\n\nUpdated.\n",
        "Exercise the stale path",
    )
    assert result["error"] == "conflict"
    assert mutations == []


def test_section_edit_refuses_a_redacted_target_section(monkeypatch):
    content = CLEAN_CONTENT.replace(
        "Current acceptance text.",
        "Current acceptance text.\n<REDACTED:ENV_SECRET_ASSIGNMENT:FAMILY-0014>",
    )
    mutations: list = []
    monkeypatch.setattr(docs_tools, "_request", _router(content=content, mutations=mutations))
    result = _tools()["replace_doc_section"](
        "reference/example.md",
        "acceptance",
        "revision-current",
        "section-hash-read-by-caller",
        "## Acceptance {#acceptance}\n\nUpdated.\n",
        "Do not touch a redacted section",
    )
    assert result["error"] == "redacted_section_edit_forbidden"
    assert result["heading_id"] == "acceptance"
    assert mutations == []


def test_section_edit_allowed_when_redaction_is_in_a_different_section(monkeypatch):
    content = CLEAN_CONTENT + (
        "\n## Redacted appendix {#redacted-appendix}\n\n"
        "<REDACTED:SSH_PASSWORD:FAMILY-0027>\n"
    )
    mutations: list = []
    monkeypatch.setattr(docs_tools, "_request", _router(content=content, mutations=mutations))
    result = _tools()["replace_doc_section"](
        "reference/example.md",
        "acceptance",
        "revision-current",
        "section-hash-read-by-caller",
        "## Acceptance {#acceptance}\n\nUpdated safely.\n",
        "Update the clean section",
    )
    assert result["status"] == "PUBLISHED"


def test_section_edit_rejects_marker_in_new_content(monkeypatch):
    mutations: list = []
    monkeypatch.setattr(docs_tools, "_request", _router(mutations=mutations))
    result = _tools()["insert_doc_after_heading"](
        "reference/example.md",
        "acceptance",
        "revision-current",
        "section-hash-read-by-caller",
        "<REDACTED:SSH_PASSWORD:FAMILY-0027>",
        "Do not insert a marker",
    )
    assert result["error"] == "redaction_marker_in_submitted_content"
    assert mutations == []


def test_missing_explicit_heading_is_reported(monkeypatch):
    mutations: list = []
    monkeypatch.setattr(docs_tools, "_request", _router(mutations=mutations))
    result = _tools()["replace_doc_section"](
        "reference/example.md",
        "does-not-exist",
        "revision-current",
        "section-hash-read-by-caller",
        "## New {#does-not-exist}\n\nBody.\n",
        "Target a missing heading",
    )
    assert result["error"] == "explicit_section_not_found"
    assert mutations == []


# --- section parser ---------------------------------------------------------

def test_section_parser_ignores_headings_inside_fenced_code():
    assert docs_tools._explicit_section_content(CLEAN_CONTENT, "fake-inside-fence") is None
    real = docs_tools._explicit_section_content(CLEAN_CONTENT, "acceptance")
    assert real is not None
    assert "Current acceptance text." in real
