from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import docs  # noqa: E402


@pytest.fixture
def page() -> dict:
    return {
        "resource_id": "11111111-1111-1111-1111-111111111111",
        "path": "reference/example.md",
        "title": "Example",
        "nav_path": "Reference/Example",
        "revision": "revision-current",
        "workspace_key": "reference",
        "status": "active",
    }


def test_existing_write_requires_revision_observed_by_caller(monkeypatch, page):
    monkeypatch.setattr(docs, "_find_path", lambda *_args, **_kwargs: page)

    result = docs.write_doc_impl(
        "reference/example.md",
        content="# Example\n\nCorrected.\n",
        purpose="Correct the example",
    )

    assert result["error"] == "expected_revision_required"
    assert result["current_revision"] == "revision-current"


def test_existing_write_preserves_metadata_when_omitted(monkeypatch, page):
    monkeypatch.setattr(docs, "_find_path", lambda *_args, **_kwargs: page)
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return 200, {"status": "PUBLISHED"}

    monkeypatch.setattr(docs, "_request", fake_request)

    result = docs.write_doc_impl(
        "reference/example.md",
        content="# Example\n\nCorrected.\n",
        purpose="Correct the example",
        expected_revision="revision-read-by-caller",
    )

    assert result["status"] == "PUBLISHED"
    body = calls[0][2]["body"]
    assert body["expected_revision"] == "revision-read-by-caller"
    assert body["content"].startswith("# Example")
    assert "title" not in body
    assert "nav_path" not in body


def test_existing_write_passes_optional_metadata_only_when_requested(monkeypatch, page):
    monkeypatch.setattr(docs, "_find_path", lambda *_args, **_kwargs: page)
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return 200, {"status": "PUBLISHED"}

    monkeypatch.setattr(docs, "_request", fake_request)

    docs.write_doc_impl(
        "reference/example.md",
        title="Renamed example",
        nav_path="Reference/Renamed example",
        content="# Renamed example\n",
        purpose="Rename deliberately",
        expected_revision="revision-current",
    )

    body = calls[0][2]["body"]
    assert body["title"] == "Renamed example"
    assert body["nav_path"] == "Reference/Renamed example"


def test_stale_write_returns_actionable_conflict(monkeypatch, page):
    monkeypatch.setattr(docs, "_find_path", lambda *_args, **_kwargs: page)

    def fake_request(method, path, **kwargs):
        return 409, {"detail": {"code": "PAGE_REVISION_STALE", "current": "revision-current"}}

    monkeypatch.setattr(docs, "_request", fake_request)

    result = docs.write_doc_impl(
        "reference/example.md",
        content="# Example\n\nOld edit.\n",
        purpose="Exercise conflict",
        expected_revision="revision-stale",
    )

    assert result["error"] == "conflict"
    assert result["current_revision"] == "revision-current"
    assert "re-read" in result["detail"].lower()


def _successful_change_recorder(monkeypatch, page):
    monkeypatch.setattr(docs, "_find_path", lambda *_args, **_kwargs: page)
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/api/v1/changes":
            return 201, {"change_id": "22222222-2222-2222-2222-222222222222"}
        if path.endswith("/operations"):
            return 201, {"status": "DRAFT"}
        if path.endswith("/validate"):
            return 200, {"validation_summary": {"passed": True}}
        if path.endswith("/publish"):
            return 200, {"status": "PUBLISHED", "publication_receipt": {"status": "COMPLETED"}}
        raise AssertionError((method, path, kwargs))

    monkeypatch.setattr(docs, "_request", fake_request)
    return calls


@pytest.mark.parametrize(
    ("function", "operation_type"),
    [
        (docs.replace_doc_section_impl, "REPLACE_SECTION"),
        (docs.insert_doc_before_heading_impl, "INSERT_BEFORE_HEADING"),
        (docs.insert_doc_after_heading_impl, "INSERT_AFTER_HEADING"),
    ],
)
def test_bounded_section_tools_preserve_revision_and_section_hash(
    monkeypatch, page, function, operation_type
):
    calls = _successful_change_recorder(monkeypatch, page)

    result = function(
        "reference/example.md",
        "acceptance",
        "revision-read-by-caller",
        "section-hash-read-by-caller",
        "## Acceptance {#acceptance}\n\nUpdated.\n",
        "Update one bounded section",
    )

    assert result["status"] == "PUBLISHED"
    operation_call = next(call for call in calls if call[1].endswith("/operations"))
    operation = operation_call[2]["body"]
    assert operation["operation_type"] == operation_type
    assert operation["expected_revision"] == "revision-read-by-caller"
    assert operation["expected_section_hash"] == "section-hash-read-by-caller"
    assert operation["payload"]["heading_id"] == "acceptance"


def test_metadata_patch_is_separate_from_content_replacement(monkeypatch, page):
    calls = _successful_change_recorder(monkeypatch, page)

    result = docs.patch_doc_metadata_impl(
        "reference/example.md",
        "revision-read-by-caller",
        "Move only the navigation label",
        nav_path="Reference/Examples/Example",
    )

    assert result["status"] == "PUBLISHED"
    operation_call = next(call for call in calls if call[1].endswith("/operations"))
    operation = operation_call[2]["body"]
    assert operation == {
        "operation_type": "PATCH_METADATA",
        "page_resource_id": page["resource_id"],
        "expected_revision": "revision-read-by-caller",
        "payload": {"nav_path": "Reference/Examples/Example"},
    }


def test_metadata_patch_rejects_empty_intent(monkeypatch, page):
    monkeypatch.setattr(docs, "_find_path", lambda *_args, **_kwargs: page)

    result = docs.patch_doc_metadata_impl(
        "reference/example.md",
        "revision-current",
        "No actual metadata supplied",
    )

    assert result["error"] == "at least one metadata field is required"


def test_archive_requires_caller_revision(monkeypatch, page):
    monkeypatch.setattr(docs, "_find_path", lambda *_args, **_kwargs: page)

    result = docs.archive_doc_impl("reference/example.md", "Archive obsolete page")

    assert result["error"] == "expected_revision_required"
    assert result["current_revision"] == "revision-current"
