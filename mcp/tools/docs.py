"""DocPlane MCP tools using the same contributor API as the dashboard."""
from __future__ import annotations

from uuid import uuid4

import httpx

from common import DOCPLANE_API_URL, DOCPLANE_TOKEN


def _key(prefix: str) -> str:
    return f"mcp-{prefix}-{uuid4()}"


def _request(method: str, path: str, *, body: dict | None = None, idempotency_key: str | None = None):
    if not DOCPLANE_TOKEN:
        return 503, {"error": "DOCPLANE_TOKEN is not configured"}
    headers = {"Authorization": f"Bearer {DOCPLANE_TOKEN}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        response = httpx.request(method, f"{DOCPLANE_API_URL}{path}", headers=headers, json=body, timeout=60)
        try:
            payload = response.json()
        except Exception:
            payload = {"raw": response.text}
        return response.status_code, payload
    except Exception as exc:
        return 503, {"error": f"DocPlane API unreachable: {exc}"}


def _error(code: int, body) -> dict:
    return {"error": f"DocPlane API returned {code}", "detail": body}


def _find_path(path: str, *, include_archived: bool = True) -> dict | None:
    status = "all" if include_archived else "active"
    params = httpx.QueryParams({'status': status, 'path': path})
    code, body = _request("GET", f"/api/v1/pages?{params}")
    if code != 200:
        return None
    pages = body.get("pages", []) if isinstance(body, dict) else []
    return pages[0] if pages else None


def register(mcp) -> None:
    @mcp.tool()
    def search_docs(query: str, top_k: int = 10) -> list[dict]:
        """Search canonical documentation before creating or editing content."""
        params = httpx.QueryParams({"q": query, "limit": top_k})
        code, body = _request("GET", f"/api/v1/search?{params}")
        if code != 200:
            return [_error(code, body)]
        return body.get("results", [])

    @mcp.tool()
    def read_doc(path_or_slug: str) -> dict:
        """Read a canonical Markdown page by exact path, title or search term."""
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

    @mcp.tool()
    def list_docs(status: str = "active") -> list[dict]:
        """List active, archived or all canonical pages."""
        if status not in {"active", "archived", "all"}:
            return [{"error": "status must be active, archived or all"}]
        code, body = _request("GET", f"/api/v1/pages?status={status}&limit=2000")
        return body.get("pages", []) if code == 200 else [_error(code, body)]

    @mcp.tool()
    def write_doc(path: str, title: str, nav_path: str, content: str, purpose: str) -> dict:
        """Create or replace a Markdown page and publish it directly with exact-revision safety."""
        existing = _find_path(path)
        change_key = _key("change")
        code, change = _request(
            "POST",
            "/api/v1/changes",
            body={"title": f"Update {title}", "purpose": purpose, "workspace_key": existing.get("workspace_key", "reference") if existing else "reference"},
            idempotency_key=change_key,
        )
        if code != 201:
            return _error(code, change)
        if existing:
            operation = {
                "operation_type": "REPLACE_DOCUMENT",
                "page_resource_id": existing["resource_id"],
                "expected_revision": existing["revision"],
                "payload": {"content": content, "title": title, "nav_path": nav_path},
            }
        else:
            operation = {
                "operation_type": "CREATE_PAGE",
                "payload": {"path": path, "title": title, "nav_path": nav_path, "content": content, "workspace_key": "reference"},
            }
        change_id = change["change_id"]
        code, body = _request("POST", f"/api/v1/changes/{change_id}/operations", body=operation, idempotency_key=_key("operation"))
        if code != 201:
            return _error(code, body)
        code, body = _request("POST", f"/api/v1/changes/{change_id}/validate", body={})
        if code != 200 or not body.get("validation_summary", {}).get("passed"):
            return _error(code, body)
        code, body = _request("POST", f"/api/v1/changes/{change_id}/publish", body={}, idempotency_key=_key("publish"))
        return body if code == 200 else _error(code, body)

    @mcp.tool()
    def archive_doc(path: str, purpose: str) -> dict:
        """Archive a page through the direct publication transaction."""
        existing = _find_path(path, include_archived=False)
        if existing is None:
            return {"error": "active page not found", "path": path}
        code, change = _request("POST", "/api/v1/changes", body={"title": f"Archive {existing['title']}", "purpose": purpose, "workspace_key": existing["workspace_key"]}, idempotency_key=_key("change"))
        if code != 201:
            return _error(code, change)
        change_id = change["change_id"]
        operation = {"operation_type": "ARCHIVE_PAGE", "page_resource_id": existing["resource_id"], "expected_revision": existing["revision"], "payload": {}}
        code, body = _request("POST", f"/api/v1/changes/{change_id}/operations", body=operation, idempotency_key=_key("operation"))
        if code != 201:
            return _error(code, body)
        code, body = _request("POST", f"/api/v1/changes/{change_id}/publish", body={}, idempotency_key=_key("publish"))
        return body if code == 200 else _error(code, body)

    @mcp.tool()
    def resolve_concept(term: str) -> dict:
        """Resolve an internal term using canonical search results."""
        results = search_docs(term, top_k=5)
        return {"term": term, "matches": results, "unique": len(results) == 1}
