"""DocPlane MCP tools using the same contributor API as the dashboard."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx

import common
from common import DOCPLANE_API_URL, DOCPLANE_TOKEN

# Bound for content_path reads: generous for documentation, small enough that
# a mistaken path (a log, a dump) fails loudly instead of publishing garbage.
CONTENT_PATH_MAX_BYTES = 5 * 1024 * 1024
KNOWLEDGE_CLASSES = {
    "ARCHITECTURE", "OPERATION", "REFERENCE", "POLICY",
    "DECISION", "EVIDENCE", "DESIGN", "WORK_NOTE",
}


def _key(prefix: str) -> str:
    return f"mcp-{prefix}-{uuid4()}"


def _load_content_path(content_path: str) -> tuple[str | None, dict | None]:
    """Read page content from the allowlisted exchange directory.

    The path must resolve INSIDE DOCPLANE_MCP_CONTENT_DIR after symlink
    resolution — anything else would turn this tool into a file-disclosure
    primitive for whatever the MCP container can see (docplane#88 review
    constraint). Returns (content, None) or (None, error)."""
    base = common.DOCPLANE_MCP_CONTENT_DIR
    if not base:
        return None, {
            "error": "content_path is disabled: DOCPLANE_MCP_CONTENT_DIR is not configured",
            "remedy": "Mount an exchange directory into the docs-mcp container and set DOCPLANE_MCP_CONTENT_DIR to it, or pass content inline.",
        }
    base_dir = Path(base).resolve()
    if not base_dir.is_dir():
        return None, {"error": f"DOCPLANE_MCP_CONTENT_DIR does not exist in the MCP container: {base}"}
    candidate = (base_dir / content_path).resolve()
    if not candidate.is_relative_to(base_dir):
        return None, {"error": "content_path escapes the allowlisted exchange directory", "content_path": content_path}
    if not candidate.is_file():
        return None, {"error": "content_path not found in the exchange directory", "content_path": content_path}
    size = candidate.stat().st_size
    if size > CONTENT_PATH_MAX_BYTES:
        return None, {"error": f"content_path exceeds {CONTENT_PATH_MAX_BYTES} bytes", "bytes": size}
    try:
        return candidate.read_text(encoding="utf-8"), None
    except UnicodeDecodeError:
        return None, {"error": "content_path is not valid UTF-8 text", "content_path": content_path}


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

    def _resolve_page(path_or_slug: str) -> dict:
        page = _find_path(path_or_slug)
        if page is not None:
            return page
        params = httpx.QueryParams({"q": path_or_slug, "limit": 5})
        code, body = _request("GET", f"/api/v1/search?{params}")
        if code != 200:
            return _error(code, body)
        results = body.get("results", [])
        if len(results) != 1:
            return {"error": "page is not uniquely resolved", "query": path_or_slug, "matches": results}
        return results[0]

    @mcp.tool()
    def read_doc_outline(path_or_slug: str) -> dict:
        """Read page metadata, revision and heading outline without the full Markdown body."""
        page = _resolve_page(path_or_slug)
        if "error" in page:
            return page
        code, body = _request("GET", f"/api/v1/pages/{page['resource_id']}?view=outline")
        return body if code == 200 else _error(code, body)

    @mcp.tool()
    def read_doc_section(path_or_slug: str, heading_id: str) -> dict:
        """Read one heading-bounded section plus its page revision and content hash."""
        page = _resolve_page(path_or_slug)
        if "error" in page:
            return page
        code, body = _request("GET", f"/api/v1/pages/{page['resource_id']}/sections/{heading_id}")
        return body if code == 200 else _error(code, body)

    @mcp.tool()
    def patch_doc(path: str, expected_revision: str, edits: list[dict], purpose: str, idempotency_key: str = "") -> dict:
        """Safely patch exact text in a page without resending the whole document.

        This is the preferred tool for small edits. Each edit is
        {old_text, new_text, expected_occurrences}; ambiguous anchors, stale
        revisions and no-op patches fail closed. Read the outline or section
        first and pass its revision unchanged.
        """
        if not expected_revision.strip():
            return {"error": "expected_revision is required", "remedy": "Call read_doc_outline or read_doc_section first."}
        if not purpose.strip():
            return {"error": "purpose is required"}
        if not edits:
            return {"error": "at least one edit is required"}
        existing = _find_path(path)
        if existing is None:
            return {"error": "page not found", "path": path}
        code, body = _request(
            "POST",
            f"/api/v1/pages/{existing['resource_id']}/patch",
            body={"expected_revision": expected_revision, "edits": edits, "purpose": purpose},
            idempotency_key=idempotency_key.strip() or _key("patch"),
        )
        return body if code == 200 else _error(code, body)

    @mcp.tool()
    def list_docs(status: str = "active") -> list[dict]:
        """List active, archived or all canonical pages."""
        if status not in {"active", "archived", "all"}:
            return [{"error": "status must be active, archived or all"}]
        code, body = _request("GET", f"/api/v1/pages?status={status}&limit=2000")
        return body.get("pages", []) if code == 200 else [_error(code, body)]

    @mcp.tool()
    def know_classify_doc(path: str, knowledge_class: str, reason: str, idempotency_key: str = "") -> dict:
        """Classify a canonical page through the optimistic-locked trust verb.

        Reads current trust metadata, changes only knowledge_class, and
        surfaces stale-metadata conflicts from DocPlane without retrying over
        concurrent edits. Supply idempotency_key to replay the same intent.
        """
        proposed = knowledge_class.strip().upper()
        if proposed not in KNOWLEDGE_CLASSES:
            return {"error": "unknown knowledge_class", "allowed": sorted(KNOWLEDGE_CLASSES)}
        if not reason.strip():
            return {"error": "reason is required"}
        existing = _find_path(path, include_archived=True)
        if existing is None:
            return {"error": "page not found", "path": path}
        code, trust = _request("GET", f"/api/v1/pages/{existing['resource_id']}/trust")
        if code != 200:
            return _error(code, trust)
        if trust.get("knowledge_class") == proposed:
            return {
                "status": "UNCHANGED",
                "resource_id": existing["resource_id"],
                "path": existing["path"],
                "knowledge_class": proposed,
                "metadata_version": trust["metadata_version"],
            }
        body = {
            "expected_metadata_version": trust["metadata_version"],
            "workspace_id": trust["workspace_id"],
            "publication_state": trust["publication_state"],
            "knowledge_class": proposed,
            "criticality": trust["criticality"],
            "owner_principal_id": trust.get("owner_principal_id"),
            "review_due_at": trust.get("review_due_at"),
            "provenance": trust.get("provenance"),
            "reason": f"{reason.strip()} (from MCP)",
        }
        code, response = _request(
            "POST",
            f"/api/v1/pages/{existing['resource_id']}/classification",
            body=body,
            idempotency_key=idempotency_key.strip() or _key("classify"),
        )
        return response if code == 200 else _error(code, response)

    @mcp.tool()
    def write_doc(path: str, title: str, nav_path: str, content: str = "", purpose: str = "", content_path: str = "", knowledge_class: str = "", expected_revision: str = "") -> dict:
        """Create a page or replace its complete Markdown body.

        For small edits use patch_doc. Existing-page replacement requires the
        expected_revision returned by the caller's earlier read; DocPlane will
        never substitute a newer revision on the caller's behalf.

        Provide the document either inline via `content` or — for large pages —
        via `content_path`, a file path relative to the server's allowlisted
        exchange directory (DOCPLANE_MCP_CONTENT_DIR), which the MCP server
        reads itself so the document never has to transit the calling agent's
        context. Exactly one of the two must be provided; `purpose` is always
        required."""
        if not purpose.strip():
            return {"error": "purpose is required"}
        proposed_class = knowledge_class.strip().upper()
        if proposed_class and proposed_class not in KNOWLEDGE_CLASSES:
            return {"error": "unknown knowledge_class", "allowed": sorted(KNOWLEDGE_CLASSES)}
        if bool(content) == bool(content_path):
            return {"error": "provide exactly one of content or content_path"}
        if content_path:
            loaded, failure = _load_content_path(content_path)
            if failure is not None:
                return failure
            content = loaded
        existing = _find_path(path)
        if existing and not expected_revision.strip():
            return {
                "error": "expected_revision is required when replacing an existing page",
                "remedy": "Call read_doc first, or prefer patch_doc for a small exact edit.",
            }
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
                "expected_revision": expected_revision.strip(),
                "payload": {"content": content, "title": title, "nav_path": nav_path},
            }
        else:
            payload = {"path": path, "title": title, "nav_path": nav_path, "content": content, "workspace_key": "reference"}
            if proposed_class:
                # CREATE_PAGE owns birth metadata. Existing pages use the
                # separately audited know_classify_doc trust verb.
                payload["knowledge_class"] = proposed_class
            operation = {
                "operation_type": "CREATE_PAGE",
                "payload": payload,
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
