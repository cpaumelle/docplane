"""HTTP-first agent discovery and read contract for DocPlane.

This module owns the unauthenticated cold-start document and the two read
endpoints whose original response semantics were ambiguous. It does not issue
credentials and does not create a second write authority.
"""
from __future__ import annotations

import os
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.agent_api import _page_dict, _page_select
from app.agent_auth import Principal, require_contributor
from app.db import get_conn
from app.publication import operation_types

router = APIRouter(tags=["docplane-contract"])

REPLACED_AGENT_PATHS = {
    "/.well-known/docplane.json",
    "/api/v1/pages",
    "/api/v1/search",
}

DISCOVERY_URL = "/.well-known/docplane.json"

ERROR_CATALOG: dict[str, dict[str, str]] = {
    "AUTH_REQUIRED": {
        "message": "An individual DocPlane contributor bearer token is required.",
        "remedy": "Ask a DocPlane operator to issue a named token, then send Authorization: Bearer <token>.",
    },
    "AUTH_SCHEME_INVALID": {
        "message": "The Authorization header is not a valid bearer credential.",
        "remedy": "Send exactly Authorization: Bearer <token>.",
    },
    "AUTH_TOKEN_INVALID": {
        "message": "The bearer token is unknown or malformed.",
        "remedy": "Check that the complete issued token was supplied, or ask an operator for a replacement.",
    },
    "AUTH_PRINCIPAL_INACTIVE": {
        "message": "The principal or token has been revoked or disabled.",
        "remedy": "Stop retrying this credential and ask an operator to issue or reactivate the correct named principal.",
    },
    "AUTH_TOKEN_EXPIRED": {
        "message": "The bearer token has expired.",
        "remedy": "Ask an operator to issue a new time-bounded token.",
    },
    "IDEMPOTENCY_KEY_REQUIRED": {
        "message": "This mutation requires an Idempotency-Key header.",
        "remedy": "Retry with a stable unique key and reuse it only for an identical request.",
    },
    "IDEMPOTENCY_KEY_REUSED": {
        "message": "The supplied Idempotency-Key already identifies different request data.",
        "remedy": "Replay the original request exactly or choose a new key for the new intent.",
    },
    "PAGE_NOT_FOUND": {
        "message": "The requested page does not exist.",
        "remedy": "Use /api/v1/resolve or /api/v1/search to resolve the current resource_id and path.",
    },
    "PAGE_VERSION_NOT_FOUND": {
        "message": "The requested historical page revision does not exist.",
        "remedy": "Read /api/v1/pages/{resource_id}/history and choose a listed revision.",
    },
    "PAGE_REVISION_STALE": {
        "message": "The page changed after this edit was prepared.",
        "remedy": "Read the current page, rebase the intended edit on the returned current revision, and submit again with a new Idempotency-Key.",
    },
    "CHANGE_NOT_FOUND": {
        "message": "The requested change does not exist.",
        "remedy": "List recent changes or create a new change for the intended work.",
    },
    "CHANGE_NOT_MUTABLE": {
        "message": "The change is no longer mutable in its current state.",
        "remedy": "Create a new corrective change; published changes remain immutable audit records.",
    },
    "CHANGE_VALIDATION_FAILED": {
        "message": "The candidate change failed validation and was not published.",
        "remedy": "Inspect validation.errors and each operation receipt, correct the request, then validate again.",
    },
    "WORKSPACE_NOT_FOUND": {
        "message": "The requested workspace classification does not exist.",
        "remedy": "Use an existing workspace_key returned by a page or the corpus structure endpoints.",
    },
    "RESOLVE_INPUT_REQUIRED": {
        "message": "Page resolution requires either path or title.",
        "remedy": "Retry /api/v1/resolve with exactly one useful path or title value.",
    },
}

REQUIRED_IDEMPOTENCY_PATHS = {
    "/api/v1/pages/{resource_id}/rollback",
    "/api/v1/pages/{resource_id}/replace",
    "/api/v1/changes",
    "/api/v1/changes/{change_id}/operations",
    "/api/v1/changes/{change_id}/publish",
    "/api/v1/changes/{change_id}/comments",
    "/api/v1/changes/{change_id}/abandon",
    "/api/v1/publication/retry",
}


def remove_replaced_routes(agent_router: Any) -> None:
    """Remove the superseded monolithic routes before assembling the ASGI app."""
    agent_router.routes[:] = [
        route
        for route in agent_router.routes
        if getattr(route, "path", None) not in REPLACED_AGENT_PATHS
    ]


def enrich_error_detail(detail: Any) -> Any:
    if not isinstance(detail, dict):
        return detail
    code = detail.get("code")
    if not isinstance(code, str) or code not in ERROR_CATALOG:
        return detail
    return {
        "code": code,
        **ERROR_CATALOG[code],
        "docs_url": DISCOVERY_URL,
        **detail,
    }


async def enriched_http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": jsonable_encoder(enrich_error_detail(exc.detail))},
        headers=exc.headers,
    )


def install_agent_contract(app: Any) -> None:
    """Install error enrichment and accurate required-header OpenAPI metadata."""
    app.add_exception_handler(HTTPException, enriched_http_exception_handler)

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        for path in REQUIRED_IDEMPOTENCY_PATHS:
            operation = schema.get("paths", {}).get(path, {}).get("post")
            if operation is None:
                continue
            for parameter in operation.get("parameters", []):
                if parameter.get("in") == "header" and parameter.get("name") == "Idempotency-Key":
                    parameter["required"] = True
                    break
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi


def _error_catalog_document() -> dict[str, dict[str, str]]:
    return {
        code: {**entry, "docs_url": DISCOVERY_URL}
        for code, entry in sorted(ERROR_CATALOG.items())
    }


@router.get("/.well-known/docplane.json")
def discovery() -> dict[str, Any]:
    operations = operation_types()
    site_name = os.environ.get("DOCPLANE_SITE_NAME", "DocPlane").strip() or "DocPlane"
    return {
        "product": "DocPlane",
        "site_name": site_name,
        "contract_version": "docplane-agent-discovery-v2",
        "entrypoint": DISCOVERY_URL,
        "openapi": "/openapi.json",
        "health": "/healthz",
        "authority": {
            "content": "PostgreSQL",
            "publication": "direct revision-bound publication",
            "review_required": False,
            "rendered_docs_are_derived": True,
        },
        "authentication": {
            "scheme": "Bearer",
            "header": "Authorization: Bearer <token>",
            "principal_model": "one named token per human, agent or automation principal",
            "token_acquisition": {
                "mode": "operator-issued",
                "self_service": False,
                "credentials_returned_by_discovery": False,
                "request": ["display_name", "principal_kind", "requested_expiry"],
                "procedure": "Ask a DocPlane operator to issue a named contributor token. The bootstrap credential remains operator-only and is never exposed over discovery.",
                "operator_helper": "scripts/bootstrap-contributor.sh",
                "agent_default_expiry": "24h when the helper is used without an explicit expiry",
            },
        },
        "required_headers": {
            "authenticated_requests": ["Authorization"],
            "mutations": ["Authorization", "Idempotency-Key"],
            "idempotency_rule": "Reuse a key only for an exactly identical request; use a new key for changed intent.",
        },
        "quick_start": {
            "discover": [
                "GET /.well-known/docplane.json",
                "GET /openapi.json",
            ],
            "read": [
                "GET /api/v1/search?q=<term>",
                "GET /api/v1/resolve?path=<markdown-path>",
                "GET /api/v1/pages/{resource_id}?view=edit_context",
            ],
            "single_page_replace": [
                "Read the page and retain resource_id plus revision.",
                "POST /api/v1/pages/{resource_id}/replace with expected_revision, content and purpose.",
                "Treat HTTP 409 PAGE_REVISION_STALE as a required rebase, never as success.",
            ],
            "multi_operation_change": [
                "POST /api/v1/changes",
                "POST /api/v1/changes/{change_id}/operations for each bounded operation",
                "POST /api/v1/changes/{change_id}/validate",
                "POST /api/v1/changes/{change_id}/publish",
            ],
            "cleanup": "POST /api/v1/changes/{change_id}/abandon closes an unpublished probe or discarded change.",
        },
        "operation_types": operations,
        "surfaces": {
            "pages": "/api/v1/pages",
            "search": "/api/v1/search",
            "resolve": "/api/v1/resolve",
            "changes": "/api/v1/changes",
            "single_page_replace": "/api/v1/pages/{resource_id}/replace",
            "reorganisation": "/api/v1/reorganisation/plans",
            "events": "/api/v1/events",
            "work": "/api/v1/initiatives",
            "certification": "/api/v1/certification/status",
        },
        "result_counts": {
            "count": "number of records returned in this response",
            "total": "total records matching the filters before limit",
        },
        "errors": _error_catalog_document(),
        "legacy": {
            "legacy_docs_api_contract_applies": False,
            "instruction": "Do not use legacy Docs API endpoint names. Start only from this discovery document or /openapi.json.",
        },
    }


def _page_filters(status: str, path: str | None, workspace_key: str | None) -> tuple[str, list[Any]]:
    predicates: list[str] = []
    params: list[Any] = []
    if status != "all":
        predicates.append("p.status = %s")
        params.append(status)
    if path:
        predicates.append("p.path = %s")
        params.append(path)
    if workspace_key:
        predicates.append("w.workspace_key = %s")
        params.append(workspace_key)
    where = " WHERE " + " AND ".join(predicates) if predicates else ""
    return where, params


@router.get("/api/v1/pages")
def list_pages(
    status: str = Query(default="active", pattern="^(active|archived|all)$"),
    path: str | None = Query(default=None),
    workspace_key: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    del principal
    where, filter_params = _page_filters(status, path, workspace_key)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM docs.pages p JOIN docplane.workspaces w ON w.workspace_id = p.workspace_id" + where,
            filter_params,
        )
        total = int(cur.fetchone()[0])
        cur.execute(
            _page_select() + where + " ORDER BY p.path LIMIT %s",
            [*filter_params, limit],
        )
        rows = cur.fetchall()
    pages = [_page_dict(row) for row in rows]
    return {"pages": pages, "count": len(pages), "total": total, "limit": limit}


def search_snippet(content: str, query: str, radius: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(content)).strip()
    if not text:
        return ""
    index = text.casefold().find(query.casefold())
    if index < 0:
        return text[: radius * 2] + ("…" if len(text) > radius * 2 else "")
    start = max(0, index - radius)
    end = min(len(text), index + len(query) + radius)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def _matched_in(page: dict[str, Any], query: str) -> list[str]:
    needle = query.casefold()
    fields = {
        "title": page.get("title"),
        "path": page.get("path"),
        "nav_path": page.get("nav_path"),
        "content": page.get("content"),
    }
    return [name for name, value in fields.items() if needle in str(value or "").casefold()]


@router.get("/api/v1/search")
def search_pages(
    q: str = Query(min_length=2, max_length=500),
    include_archived: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    del principal
    pattern = f"%{q}%"
    active_clause = "" if include_archived else " AND p.status = 'active'"
    predicate = " WHERE (p.title ILIKE %s OR p.path ILIKE %s OR p.nav_path ILIKE %s OR p.content ILIKE %s)" + active_clause
    filter_params = [pattern, pattern, pattern, pattern]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM docs.pages p JOIN docplane.workspaces w ON w.workspace_id = p.workspace_id" + predicate,
            filter_params,
        )
        total = int(cur.fetchone()[0])
        cur.execute(
            _page_select()
            + predicate
            + " ORDER BY CASE WHEN p.title ILIKE %s THEN 0 WHEN p.path ILIKE %s THEN 1 ELSE 2 END, p.path LIMIT %s",
            [*filter_params, pattern, pattern, limit],
        )
        rows = cur.fetchall()
    results = []
    for row in rows:
        page = _page_dict(row, include_content=True)
        results.append(
            {
                "resource_id": page["resource_id"],
                "path": page["path"],
                "title": page["title"],
                "nav_path": page["nav_path"],
                "revision": page["revision"],
                "version": page["version"],
                "workspace_key": page["workspace_key"],
                "summary": page["summary"],
                "snippet": search_snippet(page["content"], q),
                "matched_in": _matched_in(page, q),
                "uri": page["uri"],
            }
        )
    return {"query": q, "results": results, "count": len(results), "total": total, "limit": limit}
