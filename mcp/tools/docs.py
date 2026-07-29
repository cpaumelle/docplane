"""DocPlane MCP tools using the same contributor API as the dashboard.

The MCP surface is a convenience client, never a second document authority.
Existing-page mutations require the revision observed by the caller. Small edits
use the bounded operation types exposed by DocPlane rather than forcing a full
page replacement.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx

from common import DOCPLANE_API_URL, DOCPLANE_TOKEN


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
    passed = code == 200 and isinstance(body, dict) and body.get("validation_summary", {}).get("passed") is True
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
            return {"error": "page is not uniquely resolved", "query": path_or_slug, "matches": results}
        page = results[0]
    code, body = _request("GET", f"/api/v1/pages/{page['resource_id']}?view=edit_context")
    return body if code == 200 else _error(code, body)


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
    """Create a page or replace an existing page with caller-owned concurrency."""
    if not content:
        return {"error": "content is required", "path": path}
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
        return {"error": "expected_revision must be omitted when creating a page", "path": path}
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


def archive_doc_impl(path: str, purpose: str, expected_revision: str | None = None) -> dict:
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
        """Read a canonical page, including revision and section hashes for safe edits."""
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
        """Create or replace a page.

        Existing pages require expected_revision from read_doc. title and nav_path
        are optional and preserved when omitted. New pages require title and
        nav_path and must omit expected_revision.
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
        """Replace one explicitly identified section without resending the page."""
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
        """Insert Markdown before an explicit heading with revision/hash safety."""
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
        """Insert Markdown after an explicit heading with revision/hash safety."""
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
    def archive_doc(path: str, purpose: str, expected_revision: str | None = None) -> dict:
        """Archive a page using the exact revision observed by the caller."""
        return archive_doc_impl(path, purpose, expected_revision)

    @mcp.tool()
    def resolve_concept(term: str) -> dict:
        """Resolve an internal term using canonical search results."""
        results = search_docs_impl(term, top_k=5)
        return {"term": term, "matches": results, "unique": len(results) == 1}
