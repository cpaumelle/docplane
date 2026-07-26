"""Versioned endpoint-first API for human and software-agent clients.

This router deliberately stops at validated/submitted change proposals. Applying a proposal to authored
state requires the WP8 mutation guard and certified release orchestrator and is not implemented here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import psycopg2.errors
import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.agent_auth import (
    Principal,
    issue_token,
    require_bootstrap_token,
    require_scopes,
    validate_scopes,
)
from app.agent_models import (
    CanonicalResolveRequest,
    ChangeCreate,
    ChangeOperationCreate,
    ChangeReceipt,
    ChangeSubmitRequest,
    PageReadView,
    PrincipalCreate,
    PrincipalIssued,
    PrincipalView,
)
from app.agent_sections import find_section, outline, sections, summary
from app.db import get_conn


router = APIRouter(tags=["agent-v1"])

_API_BASE = os.environ.get("DOCPLANE_API_PUBLIC_URL", "http://localhost:8010").rstrip("/")
_MCP_URL = os.environ.get("DOCPLANE_MCP_PUBLIC_URL", "").rstrip("/") or None
_APP_URL = os.environ.get("DOCPLANE_APP_PUBLIC_URL", "").rstrip("/") or None
_DASHBOARD_URL = os.environ.get("DOCPLANE_DASHBOARD_PUBLIC_URL", "").rstrip("/") or None
_PATH_RE = re.compile(r"^[a-z0-9/_-]+\.md$")
_CHANGE_MUTABLE = {"DRAFT", "VALIDATED"}


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True))


def _permitted_operations(principal: Principal) -> list[str]:
    allowed = ["read"]
    if "docs:propose" in principal.scopes:
        allowed.extend(["create_change", "validate_change", "submit_change"])
    if "docs:review" in principal.scopes:
        allowed.append("review_change")
    if "docs:merge" in principal.scopes:
        allowed.append("merge_change")
    return allowed


def _load_page(resource_id: UUID) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT resource_id::text, path, title, nav_path, content, revision,
                   version, status, updated_at, updated_by
              FROM docs.pages
             WHERE resource_id = %s
            """,
            (str(resource_id),),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PAGE_NOT_FOUND", "resource_id": str(resource_id)},
        )
    keys = (
        "resource_id",
        "path",
        "title",
        "nav_path",
        "content",
        "revision",
        "version",
        "status",
        "updated_at",
        "updated_by",
    )
    return dict(zip(keys, row))


def _change_row(change_id: UUID, principal: Principal, *, for_update: bool = False) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT change_id::text, workspace_key, title, purpose,
                   author_principal_id::text, status, base_state_identity,
                   validation_summary, preview_receipt, created_at, updated_at
              FROM docs.change_proposals
             WHERE change_id = %s
            """
            + (" FOR UPDATE" if for_update else ""),
            (str(change_id),),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "CHANGE_NOT_FOUND"})
    result = dict(
        zip(
            (
                "change_id",
                "workspace_key",
                "title",
                "purpose",
                "author_principal_id",
                "status",
                "base_state_identity",
                "validation_summary",
                "preview_receipt",
                "created_at",
                "updated_at",
            ),
            row,
        )
    )
    if result["author_principal_id"] != principal.principal_id and not (
        {"docs:review", "docs:merge"} & principal.scopes
    ):
        raise HTTPException(
            status_code=403,
            detail={"code": "CHANGE_ACCESS_DENIED", "message": "change belongs to another principal"},
        )
    return result


def _load_operations(change_id: UUID) -> list[dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT operation_id::text, sequence, idempotency_key, operation_type,
                   page_resource_id::text, expected_revision, expected_section_hash,
                   payload, validation_result, created_at
              FROM docs.change_operations
             WHERE change_id = %s
             ORDER BY sequence, operation_id
            """,
            (str(change_id),),
        )
        rows = cur.fetchall()
    keys = (
        "operation_id",
        "sequence",
        "idempotency_key",
        "operation_type",
        "page_resource_id",
        "expected_revision",
        "expected_section_hash",
        "payload",
        "validation_result",
        "created_at",
    )
    return [dict(zip(keys, row)) for row in rows]


def _receipt(change: dict[str, Any], operations: list[dict[str, Any]]) -> ChangeReceipt:
    public_operations = [
        {key: value for key, value in operation.items() if key != "idempotency_key"}
        for operation in operations
    ]
    return ChangeReceipt(
        change_id=change["change_id"],
        status=change["status"],
        title=change["title"],
        purpose=change["purpose"],
        workspace_key=change["workspace_key"],
        author_principal_id=change["author_principal_id"],
        base_state_identity=change["base_state_identity"],
        validation_summary=change["validation_summary"],
        preview_receipt=change["preview_receipt"],
        created_at=change["created_at"],
        updated_at=change["updated_at"],
        operations=public_operations,
    )


@router.get("/.well-known/docplane.json", include_in_schema=False)
def capabilities() -> dict[str, Any]:
    """Credential-free product discovery. Never returns an API key or bearer token."""
    return {
        "product": "DocPlane",
        "api_contract": "v1",
        "knowledge_planes": ["durable", "work", "generated"],
        "endpoints": {
            "api": _API_BASE + "/api/v1",
            "openapi": _API_BASE + "/openapi.json",
            "mcp": _MCP_URL,
            "application": _APP_URL,
            "dashboard": _DASHBOARD_URL,
        },
        "authentication": {
            "type": "bearer",
            "named_principals": True,
            "network_position_is_identity": False,
        },
        "capabilities": {
            "stable_page_ids": True,
            "bounded_reads": ["summary", "outline", "section", "full", "edit_context"],
            "canonical_resolution": True,
            "idempotent_changes": True,
            "multi_operation_changes": True,
            "proposal_validation": True,
            "proposal_submission": True,
            "proposal_merge": False,
            "event_subscriptions": False,
        },
        "limits": {
            "search_results_default": 20,
            "search_results_max": 100,
            "change_operations_max": 500,
        },
        "invariant": "Routine operation requires no SSH, container shell or direct SQL.",
    }


@router.post("/api/v1/admin/principals", response_model=PrincipalIssued, status_code=201)
def create_principal(
    request: PrincipalCreate,
    x_docplane_bootstrap_token: str | None = Header(default=None),
) -> PrincipalIssued:
    """Bootstrap a named principal and return its clear token exactly once."""
    require_bootstrap_token(x_docplane_bootstrap_token)
    try:
        scopes = validate_scopes(request.scopes)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "PRINCIPAL_SCOPES_INVALID", "message": str(exc)},
        ) from exc
    clear, digest, prefix = issue_token()
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO docplane.principals
                    (principal_kind, display_name, owner_principal_id, metadata)
                VALUES (%s, %s, %s, %s)
                RETURNING principal_id::text
                """,
                (
                    request.principal_kind,
                    request.display_name.strip(),
                    str(request.owner_principal_id) if request.owner_principal_id else None,
                    _json(request.metadata),
                ),
            )
            principal_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO docplane.api_tokens
                    (principal_id, token_hash, token_prefix, scopes, description, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING token_id::text
                """,
                (
                    principal_id,
                    digest,
                    prefix,
                    scopes,
                    request.description,
                    request.expires_at,
                ),
            )
            token_id = cur.fetchone()[0]
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return PrincipalIssued(
        principal_id=principal_id,
        token_id=token_id,
        token=clear,
        token_prefix=prefix,
        scopes=scopes,
        expires_at=request.expires_at,
    )


@router.get("/api/v1/me", response_model=PrincipalView)
def me(principal: Principal = Depends(require_scopes())) -> PrincipalView:
    return PrincipalView(
        principal_id=principal.principal_id,
        principal_kind=principal.principal_kind,
        display_name=principal.display_name,
        scopes=sorted(principal.scopes),
        token_id=principal.token_id,
    )


@router.get("/api/v1/pages/{resource_id}", response_model=PageReadView)
def read_page(
    resource_id: UUID,
    view: Literal["summary", "outline", "section", "full", "edit_context"] = Query("summary"),
    heading_id: str | None = Query(default=None, min_length=1, max_length=200),
    principal: Principal = Depends(require_scopes("docs:read")),
) -> PageReadView:
    page = _load_page(resource_id)
    result = PageReadView(
        resource_id=page["resource_id"],
        path=page["path"],
        title=page["title"],
        nav_path=page["nav_path"],
        revision=page["revision"],
        version=page["version"],
        status=page["status"],
        updated_at=page["updated_at"],
        updated_by=page["updated_by"],
        view=view,
        permitted_operations=_permitted_operations(principal),
    )
    if view == "summary":
        result.summary = summary(page["content"])
    elif view == "outline":
        result.outline = outline(page["content"])
    elif view == "section":
        if not heading_id:
            raise HTTPException(
                status_code=422,
                detail={"code": "HEADING_ID_REQUIRED", "message": "heading_id is required for section view"},
            )
        section = find_section(page["content"], heading_id)
        if section is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "SECTION_NOT_FOUND", "heading_id": heading_id},
            )
        result.section = {
            "heading_id": section.heading_id,
            "title": section.title,
            "level": section.level,
            "content": section.content,
            "content_hash": section.content_hash,
            "explicit_id": section.explicit_id,
        }
    elif view == "full":
        result.content = page["content"]
        result.outline = outline(page["content"])
    else:
        result.content = page["content"]
        result.outline = outline(page["content"])
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT resource_id::text, path, title
                  FROM docs.pages
                 WHERE status = 'active'
                   AND resource_id <> %s
                   AND content LIKE %s
                 ORDER BY path
                 LIMIT 50
                """,
                (str(resource_id), "%" + page["path"] + "%"),
            )
            result.backlinks = [
                {"resource_id": row[0], "path": row[1], "title": row[2]}
                for row in cur.fetchall()
            ]
    return result


@router.get("/api/v1/search")
def search_pages(
    q: str = Query(min_length=2, max_length=500),
    include_archived: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(require_scopes("docs:read")),
) -> dict[str, Any]:
    needle = q.strip()
    pattern = "%" + needle + "%"
    status_clause = "status IN ('active', 'archived')" if include_archived else "status = 'active'"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT resource_id::text, path, title, nav_path, revision, version, status,
                   CASE
                     WHEN lower(title) = lower(%s) THEN 100
                     WHEN lower(path) = lower(%s) THEN 95
                     WHEN title ILIKE %s THEN 70
                     WHEN path ILIKE %s THEN 60
                     ELSE 20
                   END AS score
              FROM docs.pages
             WHERE {status_clause}
               AND (title ILIKE %s OR path ILIKE %s OR content ILIKE %s)
             ORDER BY score DESC, title, path
             LIMIT %s
            """,
            (needle, needle, pattern, pattern, pattern, pattern, pattern, limit),
        )
        rows = cur.fetchall()
    return {
        "query": q,
        "count": len(rows),
        "results": [
            {
                "resource_id": row[0],
                "uri": f"docplane://pages/{row[0]}",
                "path": row[1],
                "title": row[2],
                "nav_path": row[3],
                "revision": row[4],
                "version": row[5],
                "publication_state": row[6].upper(),
                "score": row[7],
                "permitted_operations": _permitted_operations(principal),
            }
            for row in rows
        ],
    }


@router.post("/api/v1/resolve-canonical")
def resolve_canonical(
    request: CanonicalResolveRequest,
    principal: Principal = Depends(require_scopes("docs:read")),
) -> dict[str, Any]:
    needle = request.intended_title or request.intended_path or request.query
    pattern = "%" + request.query.strip() + "%"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT resource_id::text, path, title, nav_path, revision,
                   CASE
                     WHEN lower(title) = lower(%s) THEN 100
                     WHEN lower(path) = lower(%s) THEN 98
                     WHEN title ILIKE %s THEN 75
                     WHEN path ILIKE %s THEN 65
                     ELSE 25
                   END AS score
              FROM docs.pages
             WHERE status = 'active'
               AND (title ILIKE %s OR path ILIKE %s OR content ILIKE %s)
             ORDER BY score DESC, title, path
             LIMIT 12
            """,
            (needle, request.intended_path or needle, pattern, pattern, pattern, pattern, pattern),
        )
        rows = cur.fetchall()
    candidates = [
        {
            "resource_id": row[0],
            "uri": f"docplane://pages/{row[0]}",
            "path": row[1],
            "title": row[2],
            "nav_path": row[3],
            "revision": row[4],
            "score": row[5],
            "reason": "exact_or_strong_match" if row[5] >= 90 else "similar_existing_content",
        }
        for row in rows
    ]
    preferred = candidates[0] if candidates and candidates[0]["score"] >= 90 else None
    create_allowed = preferred is None and bool(request.creation_reason and request.creation_reason.strip())
    return {
        "query": request.query,
        "preferred": preferred,
        "alternatives": candidates[1:] if preferred else candidates,
        "create_allowed": create_allowed,
        "creation_requirements": None
        if create_allowed
        else {
            "reason_required": not bool(request.creation_reason and request.creation_reason.strip()),
            "similar_content_acknowledgement_required": bool(candidates),
            "message": "Extend the canonical resource when possible; creation requires a reason and acknowledgement of similar content.",
        },
        "caller": principal.principal_id,
    }


@router.post("/api/v1/changes", response_model=ChangeReceipt, status_code=201)
def create_change(
    request: ChangeCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_scopes("docs:propose")),
) -> ChangeReceipt:
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=428,
            detail={"code": "IDEMPOTENCY_KEY_REQUIRED", "message": "Idempotency-Key header is required"},
        )
    key = idempotency_key.strip()[:256]
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO docs.change_proposals
                    (workspace_key, title, purpose, author_principal_id,
                     idempotency_key, base_state_identity)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING change_id::text, workspace_key, title, purpose,
                          author_principal_id::text, status, base_state_identity,
                          validation_summary, preview_receipt, created_at, updated_at
                """,
                (
                    request.workspace_key,
                    request.title.strip(),
                    request.purpose.strip(),
                    principal.principal_id,
                    key,
                    request.base_state_identity,
                ),
            )
            row = cur.fetchone()
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT change_id::text, workspace_key, title, purpose,
                       author_principal_id::text, status, base_state_identity,
                       validation_summary, preview_receipt, created_at, updated_at
                  FROM docs.change_proposals
                 WHERE author_principal_id = %s AND idempotency_key = %s
                """,
                (principal.principal_id, key),
            )
            row = cur.fetchone()
            if row is None:
                raise
            if (row[1], row[2], row[3], row[6]) != (
                request.workspace_key,
                request.title.strip(),
                request.purpose.strip(),
                request.base_state_identity,
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "IDEMPOTENCY_KEY_REUSED",
                        "message": "the key already identifies a different change request",
                    },
                )
    keys = (
        "change_id",
        "workspace_key",
        "title",
        "purpose",
        "author_principal_id",
        "status",
        "base_state_identity",
        "validation_summary",
        "preview_receipt",
        "created_at",
        "updated_at",
    )
    change = dict(zip(keys, row))
    return _receipt(change, [])


@router.get("/api/v1/changes/{change_id}", response_model=ChangeReceipt)
def get_change(
    change_id: UUID,
    principal: Principal = Depends(require_scopes("docs:propose")),
) -> ChangeReceipt:
    change = _change_row(change_id, principal)
    return _receipt(change, _load_operations(change_id))


def _validate_operation_payload(request: ChangeOperationCreate) -> None:
    payload = request.payload
    operation = request.operation_type
    required: dict[str, tuple[str, ...]] = {
        "REPLACE_SECTION": ("heading_id", "content"),
        "INSERT_BEFORE_HEADING": ("heading_id", "content"),
        "INSERT_AFTER_HEADING": ("heading_id", "content"),
        "REPLACE_CONTENT_RANGE": ("range_hash", "replacement"),
        "PATCH_METADATA": ("fields",),
        "MOVE_PAGE": ("to_path",),
        "REPARENT_NAV": ("to_nav_path",),
        "ADD_REDIRECT": ("from_path",),
        "CREATE_FROM_TEMPLATE": ("template", "path", "title"),
        "REPLACE_DOCUMENT": ("content",),
    }
    missing = [key for key in required.get(operation, ()) if key not in payload]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CHANGE_OPERATION_PAYLOAD_INVALID",
                "operation_type": operation,
                "missing": missing,
            },
        )
    candidate_path = payload.get("to_path") or payload.get("path") or payload.get("from_path")
    if candidate_path is not None and not _PATH_RE.fullmatch(str(candidate_path)):
        raise HTTPException(
            status_code=422,
            detail={"code": "CHANGE_OPERATION_PATH_INVALID", "path": candidate_path},
        )
    if operation in {"REPLACE_SECTION", "INSERT_BEFORE_HEADING", "INSERT_AFTER_HEADING"}:
        if request.expected_section_hash is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "EXPECTED_SECTION_HASH_REQUIRED",
                    "message": "section operations require expected_section_hash",
                },
            )


@router.post("/api/v1/changes/{change_id}/operations", response_model=ChangeReceipt, status_code=201)
def add_change_operation(
    change_id: UUID,
    request: ChangeOperationCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_scopes("docs:propose")),
) -> ChangeReceipt:
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    _validate_operation_payload(request)
    change = _change_row(change_id, principal)
    if change["status"] not in _CHANGE_MUTABLE:
        raise HTTPException(
            status_code=409,
            detail={"code": "CHANGE_NOT_MUTABLE", "status": change["status"]},
        )
    key = idempotency_key.strip()[:256]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ("docplane-change:" + str(change_id),),
        )
        cur.execute(
            "SELECT status FROM docs.change_proposals WHERE change_id = %s FOR UPDATE",
            (str(change_id),),
        )
        locked = cur.fetchone()
        if locked is None:
            raise HTTPException(status_code=404, detail={"code": "CHANGE_NOT_FOUND"})
        if locked[0] not in _CHANGE_MUTABLE:
            raise HTTPException(status_code=409, detail={"code": "CHANGE_NOT_MUTABLE", "status": locked[0]})
        cur.execute(
            "SELECT operation_type, page_resource_id::text, expected_revision, expected_section_hash, payload "
            "FROM docs.change_operations WHERE change_id = %s AND idempotency_key = %s",
            (str(change_id), key),
        )
        existing = cur.fetchone()
        intended = (
            request.operation_type,
            str(request.page_resource_id) if request.page_resource_id else None,
            request.expected_revision,
            request.expected_section_hash,
            request.payload,
        )
        if existing is not None:
            if tuple(existing) != intended:
                raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED"})
        else:
            if request.sequence is None:
                cur.execute(
                    "SELECT coalesce(max(sequence), -1) + 1 FROM docs.change_operations WHERE change_id = %s",
                    (str(change_id),),
                )
                sequence = cur.fetchone()[0]
            else:
                sequence = request.sequence
            cur.execute(
                "SELECT count(*) FROM docs.change_operations WHERE change_id = %s",
                (str(change_id),),
            )
            if cur.fetchone()[0] >= 500:
                raise HTTPException(status_code=413, detail={"code": "CHANGE_OPERATION_LIMIT"})
            try:
                cur.execute(
                    """
                    INSERT INTO docs.change_operations
                        (change_id, sequence, idempotency_key, operation_type,
                         page_resource_id, expected_revision, expected_section_hash, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(change_id),
                        sequence,
                        key,
                        request.operation_type,
                        str(request.page_resource_id) if request.page_resource_id else None,
                        request.expected_revision,
                        request.expected_section_hash,
                        _json(request.payload),
                    ),
                )
            except psycopg2.errors.UniqueViolation as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "CHANGE_OPERATION_SEQUENCE_CONFLICT", "sequence": sequence},
                ) from exc
            cur.execute(
                """
                UPDATE docs.change_proposals
                   SET status = 'DRAFT', validation_summary = NULL, preview_receipt = NULL
                 WHERE change_id = %s
                """,
                (str(change_id),),
            )
        conn.commit()
    refreshed = _change_row(change_id, principal)
    return _receipt(refreshed, _load_operations(change_id))


def _validate_one(operation: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "operation_id": operation["operation_id"],
        "sequence": operation["sequence"],
        "operation_type": operation["operation_type"],
        "errors": [],
        "warnings": [],
    }
    if operation["operation_type"] == "CREATE_FROM_TEMPLATE":
        path = operation["payload"].get("path")
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT resource_id::text FROM docs.pages WHERE path = %s", (path,))
            if cur.fetchone() is not None:
                result["errors"].append({"code": "CREATE_PATH_ALREADY_EXISTS", "path": path})
        return result

    try:
        page = _load_page(UUID(operation["page_resource_id"]))
    except HTTPException:
        result["errors"].append(
            {"code": "PAGE_NOT_FOUND", "resource_id": operation["page_resource_id"]}
        )
        return result
    result["page"] = {
        "resource_id": page["resource_id"],
        "path": page["path"],
        "revision": page["revision"],
    }
    if page["revision"] != operation["expected_revision"]:
        result["errors"].append(
            {
                "code": "PAGE_REVISION_STALE",
                "expected": operation["expected_revision"],
                "current": page["revision"],
            }
        )
        return result

    if operation["operation_type"] in {
        "REPLACE_SECTION",
        "INSERT_BEFORE_HEADING",
        "INSERT_AFTER_HEADING",
    }:
        heading_id = str(operation["payload"].get("heading_id", ""))
        section = find_section(page["content"], heading_id)
        if section is None:
            result["errors"].append({"code": "SECTION_NOT_FOUND", "heading_id": heading_id})
        else:
            result["section"] = {
                "heading_id": heading_id,
                "content_hash": section.content_hash,
                "explicit_id": section.explicit_id,
            }
            if not section.explicit_id:
                result["errors"].append(
                    {
                        "code": "SECTION_ID_NOT_EXPLICIT",
                        "heading_id": heading_id,
                        "message": "precise mutations require an explicit {#heading-id}",
                    }
                )
            if section.content_hash != operation["expected_section_hash"]:
                result["errors"].append(
                    {
                        "code": "SECTION_HASH_STALE",
                        "expected": operation["expected_section_hash"],
                        "current": section.content_hash,
                    }
                )
    return result


@router.post("/api/v1/changes/{change_id}/validate", response_model=ChangeReceipt)
def validate_change(
    change_id: UUID,
    principal: Principal = Depends(require_scopes("docs:propose")),
) -> ChangeReceipt:
    change = _change_row(change_id, principal)
    if change["status"] not in _CHANGE_MUTABLE:
        raise HTTPException(status_code=409, detail={"code": "CHANGE_NOT_MUTABLE", "status": change["status"]})
    operations = _load_operations(change_id)
    results = [_validate_one(operation) for operation in operations]
    errors = [error for result in results for error in result["errors"]]
    warnings = [warning for result in results for warning in result["warnings"]]
    if not operations:
        errors.append({"code": "CHANGE_HAS_NO_OPERATIONS"})
    canonical = json.dumps(
        {
            "change_id": str(change_id),
            "base_state_identity": change["base_state_identity"],
            "operations": [
                {
                    "sequence": operation["sequence"],
                    "type": operation["operation_type"],
                    "page": operation["page_resource_id"],
                    "expected_revision": operation["expected_revision"],
                    "expected_section_hash": operation["expected_section_hash"],
                    "payload": operation["payload"],
                }
                for operation in operations
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    candidate_identity = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    validation = {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "operations_checked": len(operations),
        "validated_at": datetime.utcnow().isoformat() + "Z",
        "contract_version": "agent-change-v1",
    }
    preview = {
        "candidate_identity": candidate_identity,
        "operations": results,
        "publication": {
            "merge_supported": False,
            "reason": "WP8 mutation guard and certified release orchestration not wired in this slice",
        },
    }
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE docs.change_proposals
               SET status = %s, validation_summary = %s, preview_receipt = %s
             WHERE change_id = %s
            """,
            ("VALIDATED" if not errors else "DRAFT", _json(validation), _json(preview), str(change_id)),
        )
        for result in results:
            cur.execute(
                "UPDATE docs.change_operations SET validation_result = %s WHERE operation_id = %s",
                (_json(result), result["operation_id"]),
            )
        conn.commit()
    refreshed = _change_row(change_id, principal)
    return _receipt(refreshed, _load_operations(change_id))


@router.post("/api/v1/changes/{change_id}/submit", response_model=ChangeReceipt)
def submit_change(
    change_id: UUID,
    request: ChangeSubmitRequest,
    principal: Principal = Depends(require_scopes("docs:propose")),
) -> ChangeReceipt:
    change = _change_row(change_id, principal)
    if change["status"] != "VALIDATED":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CHANGE_NOT_VALIDATED",
                "status": change["status"],
                "message": "validate the exact proposal before submission",
            },
        )
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE docs.change_proposals
               SET status = 'SUBMITTED', submitted_at = now(),
                   preview_receipt = preview_receipt || %s::jsonb
             WHERE change_id = %s AND status = 'VALIDATED'
            """,
            (_json({"submission_note": request.note}), str(change_id)),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail={"code": "CHANGE_SUBMIT_RACE"})
        conn.commit()
    refreshed = _change_row(change_id, principal)
    return _receipt(refreshed, _load_operations(change_id))
