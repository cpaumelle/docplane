"""DocPlane MCP tools using the same contributor API as the dashboard.

The MCP surface is a convenience client, never a second document authority.
Existing-page mutations require the revision observed by the caller. Small edits
use bounded DocPlane operations instead of forcing full-page replacement.

Migration redaction markers are sanitised authored bytes, not references that
are rehydrated during publication. The convenience layer therefore fails closed:
full-document replacement is disabled for pages containing markers, while a
bounded edit may proceed only when its addressed section and submitted content
are marker-free.
"""
from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

import httpx

from common import DOCPLANE_API_URL, DOCPLANE_TOKEN

_REDACTION_MARKER_RE = re.compile(r"<REDACTED:[^>\r\n]+>")
_EXPLICIT_HEADING_RE = re.compile(
    r"^(?P<marks>#{1,6})[ \t]+.*?[ \t]+\{#(?P<heading_id>[A-Za-z0-9_-]+)\}[ \t]*$"
)
_CODE_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")


def _key(prefix: str) -> str:
    return f"mcp-{prefix}-{uuid4()}"


def _request(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    idempotency_key: str | None = None,
):
    if not DOCPLANE_TOKEN:
        return 503, {"error": "DOCPLANE_TOKEN is not configured"}
    headers = {"Authorization": f"Bearer {DOCPLANE_TOKEN}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        response = httpx.request(
            method,
            f"{DOCPLANE_API_URL}{path}",
            headers=headers,
            json=body,
            timeout=60,
        )
        try:
            payload = response.json()
        except Exception:
            payload = {"raw": response.text}
        return response.status_code, payload
    except Exception as exc:
        return 503, {"error": f"DocPlane API unreachable: {exc}"}


def _error(code: int, body: Any) -> dict:
    return {"error": f"DocPlane API returned {code}", "detail": body}


def _find_path(path: str, *, include_archived: bool = True) -> dict | None:
    status = "all" if include_archived else "active"
    params = httpx.QueryParams({"status": status, "path": path})
    code, body = _request("GET", f"/api/v1/pages?{params}")
    if code != 200:
        return None
    pages = body.get("pages", []) if isinstance(body, dict) else []
    return pages[0] if pages else None


def _page_context(resource_id: str) -> tuple[int, dict | Any]:
    return _request("GET", f"/api/v1/pages/{resource_id}?view=edit_context")


def _redaction_count(content: str) -> int:
    return len(_REDACTION_MARKER_RE.findall(content or ""))


def _redaction_read_metadata(content: str) -> dict[str, Any]:
    count = _redaction_count(content)
    return {
        "redactions_present": count > 0,
        "redaction_marker_count": count,
        "full_document_replace_allowed": count == 0,
    }


def _redaction_error(
    *,
    code: str,
    path: str,
    resource_id: str | None,
    marker_count: int,
    detail: str,
    heading_id: str | None = None,
) -> dict:
    result: dict[str, Any] = {
        "error": code,
        "detail": detail,
        "path": path,
        "resource_id": resource_id,
        "redaction_marker_count": marker_count,
        "remedy": (
            "Use a bounded operation on a marker-free explicit section, or route the page "
            "through the governed redaction-remediation workflow. Do not restore clear secrets "
            "to documentation."
        ),
    }
    if heading_id is not None:
        result["heading_id"] = heading_id
    return result


def _explicit_section_content(content: str, heading_id: str) -> str | None:
    """Return one explicit-ID section, ignoring headings inside fenced code."""
    lines = content.splitlines(keepends=True)
    headings: list[tuple[int, int, str]] = []
    in_fence = False
    fence_marker = ""

    for index, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        fence = _CODE_FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)[0]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        match = _EXPLICIT_HEADING_RE.match(line)
        if match:
            headings.append(
                (index, len(match.group("marks")), match.group("heading_id"))
            )

    for position, (start, level, current_id) in enumerate(headings):
        if current_id != heading_id:
            continue
        end = len(lines)
        for candidate_start, candidate_level, _candidate_id in headings[position + 1 :]:
            if candidate_level <= level:
                end = candidate_start
                break
        return "".join(lines[start:end])
    return None


def _conflict(path: str, resource_id: str | None, body: Any) -> dict:
    current = _find_path(path)
    return {
        "error": "conflict",
        "detail": "The page revision or section hash is stale. Re-read the page and retry with the current values.",
        "path": path,
        "resource_id": resource_id,
        "current_revision": (current or {}).get("revision"),
        "server_detail": body,
    }


def _abandon(change_id: str, reason: str) -> None:
    _request(
        "POST",
        f"/api/v1/changes/{change_id}/abandon",
        body={"reason": reason},
        idempotency_key=_key("abandon"),
    )


def _run_change(
    *,
    workspace_key: str,
    title: str,
    purpose: str,
    operation: dict,
    path: str,
    resource_id: str | None,
) -> dict:
    code, change = _request(
        "POST",
        "/api/v1/changes",
        body={"title": title, "purpose": purpose, "workspace_key": workspace_key},
        idempotency_key=_key("change"),
    )
    if code != 201 or not isinstance(change, dict):
        return _error(code, change)
    change_id = change.get("change_id")
    if not change_id:
        return {"error": "DocPlane change creation returned no change_id", "detail": change}

    code, body = _request(
        "POST",
        f"/api/v1/changes/{change_id}/operations",
        body=operation,
        idempotency_key=_key("operation"),
    )
    if code != 201:
        _abandon(change_id, "MCP operation creation failed")
        if code in {409, 412}:
            return _conflict(path, resource_id, body)
        return _error(code, body)

    code, body = _request("POST", f"/api/v1/changes/{change_id}/validate", body={})
    passed = (
        code == 200
        and isinstance(body, dict)
        and body.get("validation_summary", {}).get("passed") is True
    )
    if not passed:
        _abandon(change_id, "MCP change validation failed")
        if code in {409, 412} or "STALE" in str(body):
            return _conflict(path, resource_id, body)
        return _error(code, body)

    code, body = _request(
        "POST",
        f"/api/v1/changes/{change_id}/publish",
        body={},
        idempotency_key=_key("publish"),
    )
    if code != 200:
        # Publication may have crossed the database commit boundary. The abandon
        # endpoint safely refuses published changes, so this is best-effort cleanup.
        _abandon(change_id, "MCP publication failed")
        if code in {409, 412} or "STALE" in str(body):
            return _conflict(path, resource_id, body)
        return _error(code, body)
    return body


def search_docs_impl(query: str, top_k: int = 10) -> list[dict]:
    params = httpx.QueryParams({"q": query, "limit": top_k})
    code, body = _request("GET", f"/api/v1/search?{params}")
    if code != 200:
        return [_error(code, body)]
    return body.get("results", [])


def read_doc_impl(path_or_slug: str) -> dict:
    page = _find_path(path_or_slug)
    if page is None:
        params = httpx.QueryParams({"q": path_or_slug, "limit": 5})
        code, body = _request("GET", f"/api/v1/search?{params}")
        if code != 200:
            return _error(code, body)
        results = body.get("results", [])
        if len(results) != 1:
            return {
                "error": "page is not uniquely resolved",
                "query": path_or_slug,
                "matches": results,
            }
        page = results[0]
    code, body = _page_context(page["resource_id"])
    if code != 200 or not isinstance(body, dict):
        return _error(code, body)
    result = dict(body)
    result.update(_redaction_read_metadata(str(body.get("content") or "")))
    return result


def list_docs_impl(status: str = "active") -> list[dict]:
    if status not in {"active", "archived", "all"}:
        return [{"error": "status must be active, archived or all"}]
    code, body = _request("GET", f"/api/v1/pages?status={status}&limit=2000")
    return body.get("pages", []) if code == 200 else [_error(code, body)]


def write_doc_impl(
    path: str,
    title: str | None = None,
    nav_path: str | None = None,
    content: str = "",
    purpose: str = "",
    expected_revision: str | None = None,
    workspace_key: str = "reference",
) -> dict:
    """Create a page or replace an existing clean page with caller-owned concurrency."""
    if not content:
        return {"error": "content is required", "path": path}
    submitted_marker_count = _redaction_count(content)
    if submitted_marker_count:
        return _redaction_error(
            code="redaction_marker_in_submitted_content",
            path=path,
            resource_id=None,
            marker_count=submitted_marker_count,
            detail=(
                "The submitted full document contains migration redaction markers. "
                "The MCP convenience layer will not author or replay marker-bearing documents."
            ),
        )

    existing = _find_path(path)
    if existing:
        if not expected_revision:
            return {
                "error": "expected_revision_required",
                "detail": "Read the page first and pass the exact revision returned by read_doc.",
                "path": path,
                "resource_id": existing.get("resource_id"),
                "current_revision": existing.get("revision"),
            }

        code, current = _page_context(existing["resource_id"])
        if code != 200 or not isinstance(current, dict):
            return _error(code, current)
        if current.get("revision") != expected_revision:
            return _conflict(
                path,
                existing.get("resource_id"),
                {
                    "code": "PAGE_REVISION_STALE",
                    "expected": expected_revision,
                    "current": current.get("revision"),
                },
            )
        current_marker_count = _redaction_count(str(current.get("content") or ""))
        if current_marker_count:
            return _redaction_error(
                code="redacted_full_document_replace_forbidden",
                path=path,
                resource_id=existing.get("resource_id"),
                marker_count=current_marker_count,
                detail=(
                    "The current page contains migration redaction markers. Full-document "
                    "replacement is disabled because markers are sanitised authored bytes and "
                    "are not rehydrated during publication."
                ),
            )

        body: dict[str, Any] = {
            "expected_revision": expected_revision,
            "content": content,
            "purpose": purpose or f"Replace {path} through DocPlane MCP",
        }
        if title is not None:
            body["title"] = title
        if nav_path is not None:
            body["nav_path"] = nav_path
        code, response = _request(
            "POST",
            f"/api/v1/pages/{existing['resource_id']}/replace",
            body=body,
            idempotency_key=_key("replace"),
        )
        if code in {409, 412} or "STALE" in str(response):
            return _conflict(path, existing.get("resource_id"), response)
        return response if code == 200 else _error(code, response)

    if expected_revision is not None:
        return {
            "error": "expected_revision must be omitted when creating a page",
            "path": path,
        }
    if not title or not nav_path:
        return {
            "error": "title and nav_path are required when creating a page",
            "path": path,
        }
    operation = {
        "operation_type": "CREATE_PAGE",
        "payload": {
            "path": path,
            "title": title,
            "nav_path": nav_path,
            "content": content,
            "workspace_key": workspace_key,
        },
    }
    return _run_change(
        workspace_key=workspace_key,
        title=f"Create {title}",
        purpose=purpose or f"Create {path} through DocPlane MCP",
        operation=operation,
        path=path,
        resource_id=None,
    )


def _bounded_edit(
    *,
    path: str,
    operation_type: str,
    heading_id: str,
    expected_revision: str,
    expected_section_hash: str,
    content: str,
    purpose: str,
) -> dict:
    page = _find_path(path, include_archived=False)
    if page is None:
        return {"error": "active page not found", "path": path}

    submitted_marker_count = _redaction_count(content)
    if submitted_marker_count:
        return _redaction_error(
            code="redaction_marker_in_submitted_content",
            path=path,
            resource_id=page.get("resource_id"),
            heading_id=heading_id,
            marker_count=submitted_marker_count,
            detail="The bounded edit content contains migration redaction markers.",
        )

    code, current = _page_context(page["resource_id"])
    if code != 200 or not isinstance(current, dict):
        return _error(code, current)
    if current.get("revision") != expected_revision:
        return _conflict(
            path,
            page.get("resource_id"),
            {
                "code": "PAGE_REVISION_STALE",
                "expected": expected_revision,
                "current": current.get("revision"),
            },
        )

    section = _explicit_section_content(str(current.get("content") or ""), heading_id)
    if section is None:
        return {
            "error": "explicit_section_not_found",
            "detail": (
                "The requested explicit heading ID was not found outside fenced code. "
                "Re-read the page and choose an explicit heading ID from its outline."
            ),
            "path": path,
            "resource_id": page.get("resource_id"),
            "heading_id": heading_id,
        }
    target_marker_count = _redaction_count(section)
    if target_marker_count:
        return _redaction_error(
            code="redacted_section_edit_forbidden",
            path=path,
            resource_id=page.get("resource_id"),
            heading_id=heading_id,
            marker_count=target_marker_count,
            detail=(
                "The addressed section contains migration redaction markers. "
                "The MCP convenience layer will not replace or insert adjacent to that section."
            ),
        )

    operation = {
        "operation_type": operation_type,
        "page_resource_id": page["resource_id"],
        "expected_revision": expected_revision,
        "expected_section_hash": expected_section_hash,
        "payload": {"heading_id": heading_id, "content": content},
    }
    return _run_change(
        workspace_key=page.get("workspace_key", "reference"),
        title=f"{operation_type.replace('_', ' ').title()} in {path}",
        purpose=purpose,
        operation=operation,
        path=path,
        resource_id=page.get("resource_id"),
    )


def replace_doc_section_impl(
    path: str,
    heading_id: str,
    expected_revision: str,
    expected_section_hash: str,
    content: str,
    purpose: str,
) -> dict:
    return _bounded_edit(
        path=path,
        operation_type="REPLACE_SECTION",
        heading_id=heading_id,
        expected_revision=expected_revision,
        expected_section_hash=expected_section_hash,
        content=content,
        purpose=purpose,
    )


def insert_doc_before_heading_impl(
    path: str,
    heading_id: str,
    expected_revision: str,
    expected_section_hash: str,
    content: str,
    purpose: str,
) -> dict:
    return _bounded_edit(
        path=path,
        operation_type="INSERT_BEFORE_HEADING",
        heading_id=heading_id,
        expected_revision=expected_revision,
        expected_section_hash=expected_section_hash,
        content=content,
        purpose=purpose,
    )


def insert_doc_after_heading_impl(
    path: str,
    heading_id: str,
    expected_revision: str,
    expected_section_hash: str,
    content: str,
    purpose: str,
) -> dict:
    return _bounded_edit(
        path=path,
        operation_type="INSERT_AFTER_HEADING",
        heading_id=heading_id,
        expected_revision=expected_revision,
        expected_section_hash=expected_section_hash,
        content=content,
        purpose=purpose,
    )


def patch_doc_metadata_impl(
    path: str,
    expected_revision: str,
    purpose: str,
    title: str | None = None,
    nav_path: str | None = None,
    workspace_key: str | None = None,
    knowledge_class: str | None = None,
    criticality: str | None = None,
) -> dict:
    page = _find_path(path, include_archived=False)
    if page is None:
        return {"error": "active page not found", "path": path}
    payload = {
        key: value
        for key, value in {
            "title": title,
            "nav_path": nav_path,
            "workspace_key": workspace_key,
            "knowledge_class": knowledge_class,
            "criticality": criticality,
        }.items()
        if value is not None
    }
    if not payload:
        return {"error": "at least one metadata field is required", "path": path}
    operation = {
        "operation_type": "PATCH_METADATA",
        "page_resource_id": page["resource_id"],
        "expected_revision": expected_revision,
        "payload": payload,
    }
    return _run_change(
        workspace_key=page.get("workspace_key", "reference"),
        title=f"Patch metadata for {path}",
        purpose=purpose,
        operation=operation,
        path=path,
        resource_id=page.get("resource_id"),
    )


def archive_doc_impl(
    path: str,
    purpose: str,
    expected_revision: str | None = None,
) -> dict:
    existing = _find_path(path, include_archived=False)
    if existing is None:
        return {"error": "active page not found", "path": path}
    if not expected_revision:
        return {
            "error": "expected_revision_required",
            "detail": "Read the page first and pass the exact revision returned by read_doc.",
            "path": path,
            "resource_id": existing.get("resource_id"),
            "current_revision": existing.get("revision"),
        }
    operation = {
        "operation_type": "ARCHIVE_PAGE",
        "page_resource_id": existing["resource_id"],
        "expected_revision": expected_revision,
        "payload": {},
    }
    return _run_change(
        workspace_key=existing.get("workspace_key", "reference"),
        title=f"Archive {existing['title']}",
        purpose=purpose,
        operation=operation,
        path=path,
        resource_id=existing.get("resource_id"),
    )


def register(mcp) -> None:
    @mcp.tool()
    def search_docs(query: str, top_k: int = 10) -> list[dict]:
        """Search canonical documentation before creating or editing content."""
        return search_docs_impl(query, top_k)

    @mcp.tool()
    def read_doc(path_or_slug: str) -> dict:
        """Read a page with revision, section hashes and redaction safety metadata."""
        return read_doc_impl(path_or_slug)

    @mcp.tool()
    def list_docs(status: str = "active") -> list[dict]:
        """List active, archived or all canonical pages."""
        return list_docs_impl(status)

    @mcp.tool()
    def write_doc(
        path: str,
        title: str | None = None,
        nav_path: str | None = None,
        content: str = "",
        purpose: str = "",
        expected_revision: str | None = None,
        workspace_key: str = "reference",
    ) -> dict:
        """Create or fully replace a clean page.

        Existing pages require expected_revision from read_doc. title and nav_path
        are optional and preserved when omitted. New pages require title and
        nav_path and must omit expected_revision. Full replacement is refused when
        either the current page or submitted content contains migration redaction
        markers; use a marker-free bounded section operation or governed remediation.
        """
        return write_doc_impl(
            path,
            title,
            nav_path,
            content,
            purpose,
            expected_revision,
            workspace_key,
        )

    @mcp.tool()
    def replace_doc_section(
        path: str,
        heading_id: str,
        expected_revision: str,
        expected_section_hash: str,
        content: str,
        purpose: str,
    ) -> dict:
        """Replace one marker-free explicit section without resending the page."""
        return replace_doc_section_impl(
            path,
            heading_id,
            expected_revision,
            expected_section_hash,
            content,
            purpose,
        )

    @mcp.tool()
    def insert_doc_before_heading(
        path: str,
        heading_id: str,
        expected_revision: str,
        expected_section_hash: str,
        content: str,
        purpose: str,
    ) -> dict:
        """Insert marker-free Markdown before a marker-free explicit section."""
        return insert_doc_before_heading_impl(
            path,
            heading_id,
            expected_revision,
            expected_section_hash,
            content,
            purpose,
        )

    @mcp.tool()
    def insert_doc_after_heading(
        path: str,
        heading_id: str,
        expected_revision: str,
        expected_section_hash: str,
        content: str,
        purpose: str,
    ) -> dict:
        """Insert marker-free Markdown after a marker-free explicit section."""
        return insert_doc_after_heading_impl(
            path,
            heading_id,
            expected_revision,
            expected_section_hash,
            content,
            purpose,
        )

    @mcp.tool()
    def patch_doc_metadata(
        path: str,
        expected_revision: str,
        purpose: str,
        title: str | None = None,
        nav_path: str | None = None,
        workspace_key: str | None = None,
        knowledge_class: str | None = None,
        criticality: str | None = None,
    ) -> dict:
        """Patch page metadata without replacing content or moving the path."""
        return patch_doc_metadata_impl(
            path,
            expected_revision,
            purpose,
            title,
            nav_path,
            workspace_key,
            knowledge_class,
            criticality,
        )

    @mcp.tool()
    def archive_doc(
        path: str,
        purpose: str,
        expected_revision: str | None = None,
    ) -> dict:
        """Archive a page using the exact revision observed by the caller."""
        return archive_doc_impl(path, purpose, expected_revision)

    @mcp.tool()
    def resolve_concept(term: str) -> dict:
        """Resolve an internal term using canonical search results."""
        results = search_docs_impl(term, top_k=5)
        return {"term": term, "matches": results, "unique": len(results) == 1}
