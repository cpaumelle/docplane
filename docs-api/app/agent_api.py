"""The sole versioned API for DocPlane contributors."""
from __future__ import annotations

import json
import base64
import binascii
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg2.errors
import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.agent_auth import (
    Principal,
    issue_token,
    require_bootstrap_token,
    require_contributor,
)
from app.agent_models import (
    ChangeCommentCreate,
    ChangeCreate,
    ChangeOperationCreate,
    PrincipalCreate,
    PrincipalToken,
    RollbackRequest,
)
from app.corpus_structure import build as build_structure
from app.db import get_conn
from app.event_store import append_event
from app.markdown_sections import outline, summary
from app.publication import change_view, publish_change, validate_change
from app.runtime import certification_status, deploy_current_state

router = APIRouter(tags=["docplane-v1"])

OBSERVATORY_EXPORT_MAX_RESOURCES = 5000
OBSERVATORY_EXPORT_MAX_BYTES = 10 * 1024 * 1024
_DATED_PATH = re.compile(r"\d{4}-\d{2}-\d{2}")


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def _idempotency(value: str | None) -> str:
    if not value or not value.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    return value.strip()[:256]


def _page_dict(row, *, include_content: bool = False) -> dict[str, Any]:
    keys = (
        "resource_id", "path", "title", "nav_path", "content", "revision", "version",
        "status", "workspace_id", "workspace_key", "publication_state", "knowledge_class",
        "verification_state", "owner_principal_id", "review_due_at", "criticality",
        "metadata_review_required", "metadata_version", "updated_at", "updated_by",
    )
    page = dict(zip(keys, row))
    for key in ("resource_id", "workspace_id", "owner_principal_id"):
        if page.get(key) is not None:
            page[key] = str(page[key])
    if include_content:
        page["outline"] = outline(page["content"])
        page["summary"] = summary(page["content"])
    else:
        page.pop("content", None)
    page["uri"] = f"docplane://pages/{page['resource_id']}"
    return page


def _page_select() -> str:
    return """
        SELECT p.resource_id, p.path, p.title, p.nav_path, p.content,
               p.revision, p.version, p.status, p.workspace_id, w.workspace_key,
               p.publication_state, p.knowledge_class, p.verification_state,
               p.owner_principal_id, p.review_due_at, p.criticality,
               p.metadata_review_required, p.metadata_version, p.updated_at, p.updated_by
          FROM docs.pages p
          JOIN docplane.workspaces w ON w.workspace_id = p.workspace_id
    """


@router.get("/.well-known/docplane.json")
def discovery() -> dict[str, Any]:
    return {
        "product": "DocPlane",
        "api_contract": "v1",
        "authority": "postgresql",
        "authentication": "named-bearer-principal",
        "authorization": "all active principals are contributors",
        "publication": "direct revision-bound publication; review is optional",
        "surfaces": {
            "pages": "/api/v1/pages",
            "search": "/api/v1/search",
            "changes": "/api/v1/changes",
            "reorganisation": "/api/v1/reorganisation/plans",
            "events": "/api/v1/events",
            "work": "/api/v1/initiatives",
            "certification": "/api/v1/certification/status",
        },
    }


@router.get("/api/v1/capabilities")
def capabilities(principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    return {
        "contract_version": "docplane-capabilities-v1",
        "principal": {
            "principal_id": principal.principal_id,
            "display_name": principal.display_name,
            "principal_kind": principal.principal_kind,
            "role": "CONTRIBUTOR",
        },
        "documents": {
            "read": True,
            "author": True,
            "publish": True,
            "review_required": False,
            "exact_revision_required": True,
            "version_history": True,
            "rollback": True,
        },
        "workspaces": {"authorization_boundary": False, "classification_boundary": True},
        "events": {"append": True, "subscribe": True},
    }


@router.get("/api/v1/me")
def me(principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    return {
        "principal_id": principal.principal_id,
        "display_name": principal.display_name,
        "principal_kind": principal.principal_kind,
        "role": "CONTRIBUTOR",
    }


@router.post("/api/v1/bootstrap/principals", response_model=PrincipalToken, status_code=201)
def create_principal(
    request: PrincipalCreate,
    bootstrap_token: str | None = Header(default=None, alias="X-DocPlane-Bootstrap-Token"),
) -> PrincipalToken:
    require_bootstrap_token(bootstrap_token)
    clear, digest, prefix = issue_token()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO docplane.principals
                (principal_kind, display_name, status, metadata)
            VALUES (%s, %s, 'ACTIVE', %s)
            RETURNING principal_id
            """,
            (request.principal_kind, request.display_name.strip(), _json(request.metadata)),
        )
        principal_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO docplane.api_tokens
                (principal_id, token_hash, token_prefix, description, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                principal_id,
                digest,
                prefix,
                "DocPlane contributor token",
                request.expires_at,
            ),
        )
        conn.commit()
    return PrincipalToken(
        principal_id=principal_id,
        display_name=request.display_name.strip(),
        principal_kind=request.principal_kind,
        token=clear,
        token_prefix=prefix,
        expires_at=request.expires_at,
    )


@router.post("/api/v1/bootstrap/principals/{principal_id}/revoke")
def revoke_principal(
    principal_id: UUID,
    bootstrap_token: str | None = Header(default=None, alias="X-DocPlane-Bootstrap-Token"),
) -> dict[str, Any]:
    require_bootstrap_token(bootstrap_token)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE docplane.principals SET status = 'REVOKED', updated_at = now() WHERE principal_id = %s",
            (str(principal_id),),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=404, detail={"code": "PRINCIPAL_NOT_FOUND"})
        cur.execute(
            "UPDATE docplane.api_tokens SET revoked_at = now() WHERE principal_id = %s AND revoked_at IS NULL",
            (str(principal_id),),
        )
        conn.commit()
    return {"principal_id": str(principal_id), "status": "REVOKED"}


@router.get("/api/v1/pages")
def list_pages(
    status: str = Query(default="active", pattern="^(active|archived|all)$"),
    path: str | None = Query(default=None),
    workspace_key: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
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
    params.append(limit)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_page_select() + where + " ORDER BY p.path LIMIT %s", params)
        rows = cur.fetchall()
    pages = [_page_dict(row) for row in rows]
    return {"pages": pages, "count": len(pages)}


@router.get("/api/v1/pages/{resource_id}")
def get_page(
    resource_id: UUID,
    view: str = Query(default="full", pattern="^(full|edit_context|summary)$"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_page_select() + " WHERE p.resource_id = %s", (str(resource_id),))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "PAGE_NOT_FOUND"})
    page = _page_dict(row, include_content=True)
    if view == "summary":
        page.pop("content", None)
        page.pop("outline", None)
    return page


@router.get("/api/v1/search")
def search_pages(
    q: str = Query(min_length=2, max_length=500),
    include_archived: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    pattern = f"%{q}%"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _page_select()
            + " WHERE (p.title ILIKE %s OR p.path ILIKE %s OR p.nav_path ILIKE %s OR p.content ILIKE %s)"
            + ("" if include_archived else " AND p.status = 'active'")
            + " ORDER BY CASE WHEN p.title ILIKE %s THEN 0 WHEN p.path ILIKE %s THEN 1 ELSE 2 END, p.path LIMIT %s",
            (pattern, pattern, pattern, pattern, pattern, pattern, limit),
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
                "uri": page["uri"],
            }
        )
    return {"query": q, "results": results, "count": len(results)}


@router.get("/api/v1/resolve")
def resolve_page(
    path: str | None = None,
    title: str | None = None,
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    if not path and not title:
        raise HTTPException(status_code=422, detail={"code": "RESOLVE_INPUT_REQUIRED"})
    with get_conn() as conn:
        cur = conn.cursor()
        if path:
            cur.execute(_page_select() + " WHERE p.path = %s", (path,))
        else:
            cur.execute(_page_select() + " WHERE lower(p.title) = lower(%s) ORDER BY p.path", (title,))
        rows = cur.fetchall()
    pages = [_page_dict(row) for row in rows]
    return {"matches": pages, "count": len(pages), "unique": len(pages) == 1}


@router.get("/api/v1/pages/{resource_id}/history")
def page_history(
    resource_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT version_id::text, path, title, nav_path, revision, version,
                   status, updated_by, archived_at, change_id::text
              FROM docs.page_versions
             WHERE resource_id = %s
             ORDER BY archived_at DESC
             LIMIT %s
            """,
            (str(resource_id), limit),
        )
        rows = cur.fetchall()
        cur.execute(
            "SELECT revision, version, path, updated_by, updated_at FROM docs.pages WHERE resource_id = %s",
            (str(resource_id),),
        )
        current = cur.fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail={"code": "PAGE_NOT_FOUND"})
    versions = [
        {
            "version_id": row[0],
            "path": row[1],
            "title": row[2],
            "nav_path": row[3],
            "revision": row[4],
            "version": row[5],
            "status": row[6],
            "updated_by": row[7],
            "archived_at": row[8],
            "change_id": row[9],
        }
        for row in rows
    ]
    return {
        "resource_id": str(resource_id),
        "current": {"revision": current[0], "version": current[1], "path": current[2], "updated_by": current[3], "updated_at": current[4]},
        "versions": versions,
    }


@router.get("/api/v1/pages/{resource_id}/history/{revision}")
def page_version(
    resource_id: UUID,
    revision: str,
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT path, title, nav_path, content, revision, version, status,
                   updated_by, archived_at, change_id::text
              FROM docs.page_versions
             WHERE resource_id = %s AND revision = %s
            """,
            (str(resource_id), revision),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "PAGE_VERSION_NOT_FOUND"})
    keys = ("path", "title", "nav_path", "content", "revision", "version", "status", "updated_by", "archived_at", "change_id")
    return {"resource_id": str(resource_id), **dict(zip(keys, row))}


@router.post("/api/v1/pages/{resource_id}/rollback")
def rollback_page(
    resource_id: UUID,
    request: RollbackRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _idempotency(idempotency_key)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT path, title, nav_path, content, status FROM docs.page_versions WHERE resource_id = %s AND revision = %s",
            (str(resource_id), request.target_revision),
        )
        target = cur.fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail={"code": "PAGE_VERSION_NOT_FOUND"})
        cur.execute("SELECT revision, workspace_id::text FROM docs.pages WHERE resource_id = %s", (str(resource_id),))
        current = cur.fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail={"code": "PAGE_NOT_FOUND"})
        if current[0] != request.expected_revision:
            raise HTTPException(status_code=409, detail={"code": "PAGE_REVISION_STALE", "current": current[0]})
        cur.execute("SELECT workspace_key FROM docplane.workspaces WHERE workspace_id = %s", (current[1],))
        workspace_key = cur.fetchone()[0]
        try:
            cur.execute(
                """
                INSERT INTO docs.changes
                    (workspace_key, title, purpose, author_principal_id, idempotency_key)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING change_id::text
                """,
                (workspace_key, f"Rollback {target[0]}", request.purpose, principal.principal_id, key),
            )
            change_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO docs.change_operations
                    (change_id, sequence, idempotency_key, operation_type,
                     page_resource_id, expected_revision, payload)
                VALUES (%s, 0, %s, 'REPLACE_DOCUMENT', %s, %s, %s)
                """,
                (
                    change_id,
                    "rollback-operation",
                    str(resource_id),
                    request.expected_revision,
                    _json({"content": target[3], "title": target[1], "nav_path": target[2]}),
                ),
            )
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT change_id::text, workspace_key, title, purpose
                  FROM docs.changes
                 WHERE author_principal_id = %s AND idempotency_key = %s
                """,
                (principal.principal_id, key),
            )
            existing = cur.fetchone()
            intended = (workspace_key, f"Rollback {target[0]}", request.purpose)
            if existing is None or tuple(existing[1:]) != intended:
                raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED"})
            change_id = existing[0]
    return publish_change(change_id, principal)


@router.get("/api/v1/changes")
def list_changes(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    params: list[Any] = []
    where = ""
    if status:
        where = " WHERE status = %s"
        params.append(status)
    params.append(limit)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT change_id::text FROM docs.changes
            """ + where + " ORDER BY updated_at DESC LIMIT %s",
            params,
        )
        ids = [row[0] for row in cur.fetchall()]
        changes = [change_view(conn, change_id) for change_id in ids]
    return {"changes": changes, "count": len(changes)}


@router.get("/api/v1/changes/{change_id}")
def get_change(
    change_id: UUID,
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    with get_conn() as conn:
        return change_view(conn, str(change_id))


@router.post("/api/v1/changes", status_code=201)
def create_change(
    request: ChangeCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _idempotency(idempotency_key)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM docplane.workspaces WHERE workspace_key = %s", (request.workspace_key,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail={"code": "WORKSPACE_NOT_FOUND"})
        try:
            cur.execute(
                """
                INSERT INTO docs.changes
                    (workspace_key, title, purpose, author_principal_id,
                     idempotency_key, base_state_identity)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING change_id::text
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
            change_id = cur.fetchone()[0]
            append_event(
                conn,
                event_type="CHANGE_CREATED",
                channel="API",
                producer_id="docplane-api",
                idempotency_key=f"CHANGE_CREATED:{principal.principal_id}:{key}",
                principal=principal,
                workspace_key=request.workspace_key,
                resource_type="CHANGE",
                resource_id=change_id,
                metadata={"title": request.title.strip()},
            )
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            cur = conn.cursor()
            cur.execute(
                "SELECT change_id::text, title, purpose, workspace_key, base_state_identity FROM docs.changes WHERE author_principal_id = %s AND idempotency_key = %s",
                (principal.principal_id, key),
            )
            row = cur.fetchone()
            if row is None:
                raise
            intended = (request.title.strip(), request.purpose.strip(), request.workspace_key, request.base_state_identity)
            if tuple(row[1:]) != intended:
                raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED"})
            change_id = row[0]
        return change_view(conn, change_id)


@router.post("/api/v1/changes/{change_id}/operations", status_code=201)
def add_operation(
    change_id: UUID,
    request: ChangeOperationCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _idempotency(idempotency_key)
    with get_conn() as conn:
        change = change_view(conn, str(change_id))
        if change["status"] not in {"DRAFT", "VALIDATED"}:
            raise HTTPException(status_code=409, detail={"code": "CHANGE_NOT_MUTABLE", "status": change["status"]})
        cur = conn.cursor()
        if request.sequence is None:
            cur.execute("SELECT coalesce(max(sequence), -1) + 1 FROM docs.change_operations WHERE change_id = %s", (str(change_id),))
            sequence = int(cur.fetchone()[0])
        else:
            sequence = request.sequence
        payload = dict(request.payload)
        if request.operation_type == "CREATE_PAGE":
            payload.setdefault("resource_id", str(uuid4()))
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
                    _json(payload),
                ),
            )
            cur.execute(
                """
                UPDATE docs.changes
                   SET status = 'DRAFT', validation_summary = NULL,
                       preview_receipt = NULL, updated_at = now()
                 WHERE change_id = %s
                """,
                (str(change_id),),
            )
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT operation_type, page_resource_id::text, expected_revision,
                       expected_section_hash, payload
                  FROM docs.change_operations
                 WHERE change_id = %s AND idempotency_key = %s
                """,
                (str(change_id), key),
            )
            existing = cur.fetchone()
            intended = (
                request.operation_type,
                str(request.page_resource_id) if request.page_resource_id else None,
                request.expected_revision,
                request.expected_section_hash,
                payload,
            )
            if existing is None or tuple(existing) != intended:
                raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED"})
        return change_view(conn, str(change_id))


@router.post("/api/v1/changes/{change_id}/validate")
def validate_change_endpoint(
    change_id: UUID,
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    return validate_change(change_id, principal)


@router.post("/api/v1/changes/{change_id}/publish")
def publish_change_endpoint(
    change_id: UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    _idempotency(idempotency_key)
    return publish_change(change_id, principal)


@router.post("/api/v1/changes/{change_id}/comments", status_code=201)
def comment_change(
    change_id: UUID,
    request: ChangeCommentCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _idempotency(idempotency_key)
    with get_conn() as conn:
        change = change_view(conn, str(change_id))
        event_id, _, _ = append_event(
            conn,
            event_type="CHANGE_COMMENTED",
            channel="API",
            producer_id=f"docplane-comments:{principal.principal_id}",
            idempotency_key=key,
            principal=principal,
            workspace_key=change["workspace_key"],
            resource_type="CHANGE",
            resource_id=str(change_id),
            metadata={"body": request.body.strip()},
        )
        conn.commit()
    return {"event_id": event_id, "change_id": str(change_id), "optional": True}


@router.get("/api/v1/certification/status")
def get_certification_status(principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    return certification_status()


@router.get("/api/v1/deployments/attempts")
def deployment_attempts(
    limit: int = Query(default=20, ge=1, le=200),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT deployment_cycle_id::text, change_id::text, requested_by::text,
                   status, target_page_identity, target_state_identity, release_id,
                   sealed_digest, receipt, failure, started_at, completed_at
              FROM docs.deployment_attempts
             ORDER BY started_at DESC
             LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    keys = (
        "deployment_cycle_id", "change_id", "requested_by", "status",
        "target_page_identity", "target_state_identity", "release_id",
        "sealed_digest", "receipt", "failure", "started_at", "completed_at",
    )
    return {"attempts": [dict(zip(keys, row)) for row in rows], "count": len(rows)}


@router.post("/api/v1/publication/retry")
def retry_publication(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    _idempotency(idempotency_key)
    return deploy_current_state(requested_by=principal.principal_id)


@router.get("/api/v1/dashboard/structure")
def dashboard_structure(principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    return _observatory_snapshot()


def _observatory_snapshot() -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.resource_id::text, p.path, p.title, p.nav_path, p.content,
                   p.revision, p.version, p.status, p.updated_at, p.updated_by,
                   w.workspace_key, p.publication_state, p.knowledge_class,
                   p.verification_state, p.owner_principal_id::text,
                   p.review_due_at, p.criticality, p.metadata_review_required,
                   p.metadata_version, p.provenance
              FROM docs.pages p
              JOIN docplane.workspaces w ON w.workspace_id = p.workspace_id
             ORDER BY p.path
            """
        )
        rows = cur.fetchall()
    keys = (
        "resource_id", "path", "title", "nav_path", "content", "revision",
        "version", "status", "updated_at", "updated_by", "workspace_key",
        "publication_state", "knowledge_class", "verification_state",
        "owner_principal_id", "review_due_at", "criticality",
        "metadata_review_required", "metadata_version", "provenance",
    )
    return build_structure([dict(zip(keys, row)) for row in rows])


def _cursor(value: str | None) -> int:
    if value is None:
        return 0
    try:
        decoded = base64.urlsafe_b64decode(value.encode() + b"==").decode()
        offset = int(decoded)
        if offset < 0:
            raise ValueError
        return offset
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise HTTPException(
            status_code=422, detail={"code": "OBSERVATORY_CURSOR_INVALID"}
        )


def _next_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def _page(values: list[dict[str, Any]], after: str | None, limit: int) -> dict[str, Any]:
    offset = _cursor(after)
    selected = values[offset:offset + limit]
    next_offset = offset + len(selected)
    has_more = next_offset < len(values)
    return {
        "items": selected,
        "count": len(selected),
        "total": len(values),
        "has_more": has_more,
        "next_after": _next_cursor(next_offset) if has_more else None,
    }


@router.get("/api/v1/dashboard/observatory")
def dashboard_observatory(
    candidate_limit: int = Query(default=50, ge=1, le=200),
    candidate_after: str | None = Query(default=None),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    snapshot = _observatory_snapshot()
    candidates = _page(
        snapshot.pop("review_candidates"), candidate_after, candidate_limit
    )
    snapshot.pop("pages")
    return {
        **snapshot,
        "review_candidates": candidates,
        "pagination_contract": {
            "dialect": "docplane-named-after-v1",
            "count": "records returned in this response",
            "total": "records available in the snapshot",
            "has_more": "true when another page exists",
            "next_after": "opaque cursor for the matching after parameter",
        },
    }


def _filter_observatory_pages(
    pages: list[dict[str, Any]],
    *,
    q: str | None = None,
    path_prefix: str | None = None,
    depth: str = "all",
    knowledge_class: str | None = None,
    identifier_family: str | None = None,
    archive_state: str | None = None,
    dated_only: bool = False,
) -> list[dict[str, Any]]:
    """Server-authoritative page filtering for the Observatory.

    path_prefix scopes to a directory: "" is the corpus root, "a/b" covers
    every page under a/b/. depth="direct" then keeps only pages immediately
    inside that directory — the drill-down contract the dashboard browses
    with, computed here so the UI never re-derives structure client-side.
    """
    if q:
        needle = q.casefold()
        pages = [
            page for page in pages
            if needle in f"{page['path']} {page.get('title') or ''}".casefold()
        ]
    if path_prefix is not None:
        prefix = path_prefix.strip("/")
        base = f"{prefix}/" if prefix else ""
        pages = [page for page in pages if page["path"].startswith(base)]
        if depth == "direct":
            pages = [page for page in pages if "/" not in page["path"][len(base):]]
    if knowledge_class:
        if knowledge_class == "__missing__":
            pages = [page for page in pages if not page.get("knowledge_class")]
        else:
            pages = [page for page in pages if page.get("knowledge_class") == knowledge_class]
    if identifier_family:
        pages = [page for page in pages if page.get("identifier_family") == identifier_family]
    if archive_state:
        pages = [page for page in pages if page.get("status") == archive_state]
    if dated_only:
        pages = [page for page in pages if _DATED_PATH.search(page["path"])]
    return pages


@router.get("/api/v1/dashboard/observatory/pages")
def dashboard_observatory_pages(
    limit: int = Query(default=100, ge=1, le=200),
    after: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=300),
    path_prefix: str | None = Query(default=None, max_length=300),
    depth: str = Query(default="all", pattern="^(direct|all)$"),
    knowledge_class: str | None = Query(default=None, max_length=100),
    identifier_family: str | None = Query(default=None, max_length=100),
    archive_state: str | None = Query(default=None, pattern="^(active|archived)$"),
    dated_only: bool = Query(default=False),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    snapshot = _observatory_snapshot()
    pages = _filter_observatory_pages(
        snapshot["pages"],
        q=q,
        path_prefix=path_prefix,
        depth=depth,
        knowledge_class=knowledge_class,
        identifier_family=identifier_family,
        archive_state=archive_state,
        dated_only=dated_only,
    )
    return {
        "fingerprint": snapshot["fingerprint"],
        "pages": _page(pages, after, limit),
    }


@router.get("/api/v1/dashboard/observatory/export")
def dashboard_observatory_export(
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    snapshot = _observatory_snapshot()
    manifest = [
        {
            "resource_id": page["resource_id"],
            "revision": page["revision"],
            "path": page["path"],
        }
        for page in snapshot["pages"]
    ]
    value = {
        "contract_version": "docplane-observatory-export-v1",
        "fingerprint": snapshot["fingerprint"],
        "manifest": manifest,
        "observatory": snapshot,
    }
    encoded_size = len(json.dumps(value, default=str).encode("utf-8"))
    if len(manifest) > OBSERVATORY_EXPORT_MAX_RESOURCES or encoded_size > OBSERVATORY_EXPORT_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "EXPORT_LIMIT_EXCEEDED",
                "resources": len(manifest),
                "resource_limit": OBSERVATORY_EXPORT_MAX_RESOURCES,
                "bytes": encoded_size,
                "byte_limit": OBSERVATORY_EXPORT_MAX_BYTES,
            },
        )
    return value
