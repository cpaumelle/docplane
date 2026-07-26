"""Governed documentation reorganisation planning and impact analysis.

This router never mutates authored pages. A validated plan is converted into the
ordinary change-proposal contract; WP8 remains the only future execution path.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg2.errors
import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app import generator
from app.agent_auth import Principal, require_scopes
from app.db import get_conn
from app.event_store import append_event
from app.reorganisation_models import (
    ReorganisationOperationCreate,
    ReorganisationPlanCreate,
    ReorganisationPlanView,
    ReorganisationReviewCreate,
    ReorganisationSubmitRequest,
)
from app.workspace_access import require_minimum_workspace_role


router = APIRouter(tags=["reorganisation-v1"])
_PATH_RE = re.compile(r"^[a-z0-9/_-]+\.md$")
_MUTABLE = {"DRAFT", "ANALYZED", "VALIDATED"}
_CHANGE_OPERATION_TYPES = {"MOVE_PAGE", "REPARENT_NAV", "ARCHIVE_PAGE", "RESTORE_PAGE"}


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _workspace(conn, workspace_key: str, principal: Principal, minimum_role: str) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        "SELECT workspace_id::text, workspace_key, name, workspace_kind, visibility "
        "FROM docplane.workspaces WHERE workspace_key = %s",
        (workspace_key,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "WORKSPACE_NOT_FOUND"})
    require_minimum_workspace_role(conn, principal, row[0], minimum_role)
    return {
        "workspace_id": row[0],
        "workspace_key": row[1],
        "name": row[2],
        "workspace_kind": row[3],
        "visibility": row[4],
    }


def _operations(conn, plan_id: str) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT operation_id::text, sequence, operation_type,
               page_resource_id::text, expected_revision, payload,
               analysis_result, inverse_operation, created_at
          FROM docs.reorganisation_operations
         WHERE plan_id = %s
         ORDER BY sequence, operation_id
        """,
        (plan_id,),
    )
    keys = (
        "operation_id", "sequence", "operation_type", "page_resource_id",
        "expected_revision", "payload", "analysis_result", "inverse_operation", "created_at",
    )
    return [dict(zip(keys, row)) for row in cur.fetchall()]


def _reviews(conn, plan_id: str) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT review_id::text, reviewer_principal_id::text, decision, note,
               reviewed_plan_version, created_at
          FROM docs.reorganisation_reviews
         WHERE plan_id = %s
         ORDER BY created_at DESC, review_id DESC
        """,
        (plan_id,),
    )
    keys = ("review_id", "reviewer_principal_id", "decision", "note", "reviewed_plan_version", "created_at")
    return [dict(zip(keys, row)) for row in cur.fetchall()]


def _permitted(plan: dict[str, Any], principal: Principal) -> list[str]:
    actions = ["read"]
    is_author = plan["author_principal_id"] == principal.principal_id
    if is_author and plan["status"] in _MUTABLE and "docs:propose" in principal.scopes:
        actions += ["add_operation", "analyze"]
    if is_author and plan["status"] == "ANALYZED" and "docs:propose" in principal.scopes:
        actions.append("validate")
    if is_author and plan["status"] == "VALIDATED" and "docs:propose" in principal.scopes:
        actions.append("submit")
    if "docs:review" in principal.scopes and plan["status"] in {"SUBMITTED", "APPROVED"}:
        actions.append("review")
    return actions


def _plan(conn, plan_id: UUID | str, principal: Principal, *, for_update: bool = False) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT plan_id::text, workspace_key, title, purpose,
               author_principal_id::text, status, version, base_state_identity,
               analysis_receipt, validation_receipt, linked_change_id::text,
               execution_receipt, certification_receipt, stabilization_receipt,
               compensating_plan_id::text, created_at, updated_at
          FROM docs.reorganisation_plans
         WHERE plan_id = %s
        """ + (" FOR UPDATE" if for_update else ""),
        (str(plan_id),),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "REORGANISATION_PLAN_NOT_FOUND"})
    keys = (
        "plan_id", "workspace_key", "title", "purpose", "author_principal_id",
        "status", "version", "base_state_identity", "analysis_receipt",
        "validation_receipt", "linked_change_id", "execution_receipt",
        "certification_receipt", "stabilization_receipt", "compensating_plan_id",
        "created_at", "updated_at",
    )
    plan = dict(zip(keys, row))
    workspace = _workspace(conn, plan["workspace_key"], principal, "READER")
    plan["workspace_id"] = workspace["workspace_id"]
    return plan


def _view(conn, plan: dict[str, Any], principal: Principal) -> ReorganisationPlanView:
    return ReorganisationPlanView(
        **{key: value for key, value in plan.items() if key != "workspace_id"},
        operations=_operations(conn, plan["plan_id"]),
        reviews=_reviews(conn, plan["plan_id"]),
        permitted_actions=_permitted(plan, principal),
    )


def _validate_payload(request: ReorganisationOperationCreate) -> None:
    payload = request.payload
    required = {
        "MOVE_PAGE": ("to_path",),
        "REPARENT_NAV": ("to_nav_path",),
        "REORDER_SECTIONS": ("order",),
        "ADD_REDIRECT": ("from_path", "to_path"),
        "REMOVE_REDIRECT": ("from_path",),
    }
    missing = [field for field in required.get(request.operation_type, ()) if field not in payload]
    if missing:
        raise HTTPException(status_code=422, detail={"code": "REORGANISATION_PAYLOAD_INVALID", "missing": missing})
    for field in ("to_path", "from_path"):
        if field in payload and not _PATH_RE.fullmatch(str(payload[field])):
            raise HTTPException(status_code=422, detail={"code": "REORGANISATION_PATH_INVALID", "field": field})
    if request.operation_type == "REORDER_SECTIONS":
        order = payload.get("order")
        if not isinstance(order, list) or not order or len(order) != len(set(order)):
            raise HTTPException(status_code=422, detail={"code": "SECTION_ORDER_INVALID"})


def _load_state(conn) -> tuple[list[dict[str, Any]], dict[str, str]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT resource_id::text, path, title, nav_path, content, revision,
               status, workspace_id::text
          FROM docs.pages
         ORDER BY path
        """
    )
    pages = [
        {
            "resource_id": row[0], "path": row[1], "title": row[2],
            "nav_path": row[3], "content": row[4], "revision": row[5],
            "status": row[6], "workspace_id": row[7],
        }
        for row in cur.fetchall()
    ]
    cur.execute("SELECT from_path, to_path FROM docs.redirects ORDER BY from_path")
    redirects = {row[0]: row[1] for row in cur.fetchall()}
    return pages, redirects


def _state_identity(pages: list[dict[str, Any]], redirects: dict[str, str]) -> str:
    return _canonical_hash({
        "pages": [
            {key: page[key] for key in ("resource_id", "path", "nav_path", "revision", "status")}
            for page in pages
        ],
        "redirects": redirects,
    })


def _analyse(conn, plan: dict[str, Any], operations: list[dict[str, Any]]) -> dict[str, Any]:
    pages, redirects = _load_state(conn)
    base_identity = _state_identity(pages, redirects)
    by_id = {page["resource_id"]: page for page in pages}
    by_path = {page["path"]: page for page in pages if page["status"] != "archived"}
    simulated = [dict(page) for page in pages]
    simulated_by_id = {page["resource_id"]: page for page in simulated}
    simulated_redirects = dict(redirects)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    inverse: list[dict[str, Any]] = []

    if plan["base_state_identity"] and plan["base_state_identity"] != base_identity:
        errors.append({
            "code": "BASE_STATE_IDENTITY_STALE",
            "expected": plan["base_state_identity"],
            "current": base_identity,
        })

    for operation in operations:
        op_errors: list[dict[str, Any]] = []
        op_warnings: list[dict[str, Any]] = []
        op_type = operation["operation_type"]
        payload = operation["payload"]
        page = simulated_by_id.get(operation["page_resource_id"]) if operation["page_resource_id"] else None
        result: dict[str, Any] = {
            "operation_id": operation["operation_id"],
            "sequence": operation["sequence"],
            "operation_type": op_type,
            "errors": op_errors,
            "warnings": op_warnings,
        }
        inverse_op: dict[str, Any] | None = None

        if page is not None and page["revision"] != operation["expected_revision"]:
            op_errors.append({
                "code": "PAGE_REVISION_STALE",
                "expected": operation["expected_revision"],
                "current": page["revision"],
            })
        elif operation["page_resource_id"] and page is None:
            op_errors.append({"code": "PAGE_NOT_FOUND", "resource_id": operation["page_resource_id"]})

        if not op_errors and op_type == "MOVE_PAGE":
            old_path = page["path"]
            new_path = str(payload["to_path"])
            collision = next(
                (candidate for candidate in simulated if candidate["path"] == new_path and candidate["resource_id"] != page["resource_id"] and candidate["status"] != "archived"),
                None,
            )
            if collision:
                op_errors.append({"code": "TARGET_PATH_COLLISION", "path": new_path, "resource_id": collision["resource_id"]})
            else:
                inbound = [
                    {"resource_id": source["resource_id"], "path": source["path"]}
                    for source in simulated
                    if source["resource_id"] != page["resource_id"] and old_path in source["content"]
                ]
                result["inbound_references"] = inbound[:200]
                result["inbound_reference_count"] = len(inbound)
                if inbound:
                    op_warnings.append({"code": "INBOUND_REFERENCES_REQUIRE_REWRITE", "count": len(inbound)})
                existing_redirect = simulated_redirects.get(old_path)
                if existing_redirect and existing_redirect != new_path:
                    op_errors.append({"code": "REDIRECT_SOURCE_CONFLICT", "from_path": old_path, "to_path": existing_redirect})
                else:
                    page["path"] = new_path
                    if payload.get("new_nav_path"):
                        page["nav_path"] = str(payload["new_nav_path"])
                    if payload.get("create_redirect", True):
                        simulated_redirects[old_path] = new_path
                    inverse_op = {
                        "operation_type": "MOVE_PAGE",
                        "page_resource_id": page["resource_id"],
                        "payload": {"to_path": old_path, "new_nav_path": by_id[page["resource_id"]]["nav_path"], "create_redirect": False},
                    }
                    result["path_diff"] = {"from": old_path, "to": new_path}
                    result["stable_resource_id"] = page["resource_id"]
                    result["search_reindex_required"] = True
                    result["mcp_reindex_required"] = True

        elif not op_errors and op_type == "REPARENT_NAV":
            old_nav = page["nav_path"]
            page["nav_path"] = str(payload["to_nav_path"])
            inverse_op = {
                "operation_type": "REPARENT_NAV",
                "page_resource_id": page["resource_id"],
                "payload": {"to_nav_path": old_nav},
            }
            result["nav_diff"] = {"from": old_nav, "to": page["nav_path"]}

        elif not op_errors and op_type == "ADD_REDIRECT":
            source = str(payload["from_path"])
            target = str(payload["to_path"])
            if source in by_path:
                op_errors.append({"code": "REDIRECT_SOURCE_IS_ACTIVE_PAGE", "from_path": source})
            elif source in simulated_redirects and simulated_redirects[source] != target:
                op_errors.append({"code": "REDIRECT_SOURCE_CONFLICT", "from_path": source, "to_path": simulated_redirects[source]})
            elif target not in {item["path"] for item in simulated if item["status"] != "archived"}:
                op_errors.append({"code": "REDIRECT_TARGET_MISSING", "to_path": target})
            else:
                simulated_redirects[source] = target
                inverse_op = {"operation_type": "REMOVE_REDIRECT", "payload": {"from_path": source}}

        elif not op_errors and op_type == "REMOVE_REDIRECT":
            source = str(payload["from_path"])
            target = simulated_redirects.get(source)
            if target is None:
                op_errors.append({"code": "REDIRECT_NOT_FOUND", "from_path": source})
            else:
                del simulated_redirects[source]
                inverse_op = {"operation_type": "ADD_REDIRECT", "payload": {"from_path": source, "to_path": target}}

        elif not op_errors and op_type == "ARCHIVE_PAGE":
            if page["status"] == "archived":
                op_warnings.append({"code": "PAGE_ALREADY_ARCHIVED"})
            page["status"] = "archived"
            inverse_op = {"operation_type": "RESTORE_PAGE", "page_resource_id": page["resource_id"], "payload": {}}

        elif not op_errors and op_type == "RESTORE_PAGE":
            if page["status"] != "archived":
                op_warnings.append({"code": "PAGE_NOT_ARCHIVED"})
            page["status"] = "active"
            inverse_op = {"operation_type": "ARCHIVE_PAGE", "page_resource_id": page["resource_id"], "payload": {}}

        elif not op_errors and op_type == "REORDER_SECTIONS":
            inverse_op = {"operation_type": "REORDER_SECTIONS", "payload": {"order": []}}
            result["requested_order"] = payload["order"]
            op_warnings.append({"code": "SECTION_ORDER_INVERSE_REQUIRES_CURRENT_ORDER_CAPTURE"})

        try:
            generator.build_nav(
                [{"path": item["path"], "nav_path": item["nav_path"]} for item in simulated if item["status"] != "archived"],
                strict=True,
            )
        except generator.NavConflict as exc:
            op_errors.append({
                "code": "NAVIGATION_CONFLICT",
                "kind": exc.kind,
                "segment": exc.segment,
                "message": exc.message,
            })

        errors.extend(op_errors)
        warnings.extend(op_warnings)
        result["inverse_operation"] = inverse_op
        results.append(result)
        if inverse_op and not op_errors:
            inverse.insert(0, inverse_op)

    candidate = {
        "pages": [
            {key: page[key] for key in ("resource_id", "path", "nav_path", "revision", "status")}
            for page in simulated
        ],
        "redirects": simulated_redirects,
    }
    return {
        "contract_version": "reorganisation-impact-v1",
        "analysed_at": datetime.now(timezone.utc).isoformat(),
        "base_state_identity": base_identity,
        "candidate_state_identity": _canonical_hash(candidate),
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "operations": results,
        "compensating_operations": inverse,
        "effects": {
            "pages_examined": len(pages),
            "operations_examined": len(operations),
            "redirect_count_before": len(redirects),
            "redirect_count_after": len(simulated_redirects),
            "search_reindex_required": any(item.get("search_reindex_required") for item in results),
            "mcp_reindex_required": any(item.get("mcp_reindex_required") for item in results),
        },
        "execution": {
            "supported": False,
            "reason": "WP8 proposal merge and certified publication are not wired in this slice",
        },
    }


@router.get("/api/v1/reorganisation/tree")
def reorganisation_tree(
    workspace_key: str = Query(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$"),
    principal: Principal = Depends(require_scopes("docs:read")),
) -> dict[str, Any]:
    with get_conn() as conn:
        workspace = _workspace(conn, workspace_key, principal, "READER")
        cur = conn.cursor()
        cur.execute(
            """
            SELECT resource_id::text, path, title, nav_path, revision, status,
                   publication_state, knowledge_class, verification_state, criticality
              FROM docs.pages
             WHERE workspace_id = %s
             ORDER BY nav_path, path
            """,
            (workspace["workspace_id"],),
        )
        pages = [
            {
                "resource_id": row[0], "path": row[1], "title": row[2], "nav_path": row[3],
                "revision": row[4], "status": row[5], "publication_state": row[6],
                "knowledge_class": row[7], "verification_state": row[8], "criticality": row[9],
            }
            for row in cur.fetchall()
        ]
    return {
        "workspace": workspace,
        "pages": pages,
        "count": len(pages),
        "contract_version": "reorganisation-tree-v1",
    }


@router.get("/api/v1/reorganisation/plans")
def list_plans(
    workspace_key: str = Query(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$"),
    status: str | None = None,
    principal: Principal = Depends(require_scopes("docs:read")),
) -> dict[str, Any]:
    with get_conn() as conn:
        _workspace(conn, workspace_key, principal, "READER")
        predicates = ["workspace_key = %s"]
        params: list[Any] = [workspace_key]
        if status:
            predicates.append("status = %s")
            params.append(status.upper())
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT plan_id::text, title, purpose, status, version,
                   author_principal_id::text, linked_change_id::text, updated_at
              FROM docs.reorganisation_plans
             WHERE {' AND '.join(predicates)}
             ORDER BY updated_at DESC, plan_id DESC
            """,
            params,
        )
        rows = cur.fetchall()
    return {
        "workspace_key": workspace_key,
        "plans": [
            {
                "plan_id": row[0], "title": row[1], "purpose": row[2], "status": row[3],
                "version": row[4], "author_principal_id": row[5], "linked_change_id": row[6],
                "updated_at": row[7],
            }
            for row in rows
        ],
    }


@router.post("/api/v1/reorganisation/plans", response_model=ReorganisationPlanView, status_code=201)
def create_plan(
    request: ReorganisationPlanCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_scopes("docs:propose")),
) -> ReorganisationPlanView:
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    key = idempotency_key.strip()[:256]
    with get_conn() as conn:
        _workspace(conn, request.workspace_key, principal, "PROPOSER")
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO docs.reorganisation_plans
                    (workspace_key, title, purpose, author_principal_id,
                     idempotency_key, base_state_identity)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING plan_id::text
                """,
                (
                    request.workspace_key, request.title.strip(), request.purpose.strip(),
                    principal.principal_id, key, request.base_state_identity,
                ),
            )
            plan_id = cur.fetchone()[0]
            append_event(
                conn,
                event_type="REORGANISATION_PLAN_CREATED",
                channel="API",
                producer_id="docplane-reorganisation-api",
                idempotency_key="PLAN_CREATED:" + key,
                principal=principal,
                workspace_key=request.workspace_key,
                resource_type="REORGANISATION_PLAN",
                resource_id=plan_id,
                metadata={"title": request.title.strip()},
            )
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            cur = conn.cursor()
            cur.execute(
                "SELECT plan_id::text, title, purpose, workspace_key, base_state_identity "
                "FROM docs.reorganisation_plans WHERE author_principal_id = %s AND idempotency_key = %s",
                (principal.principal_id, key),
            )
            row = cur.fetchone()
            if row is None:
                raise
            if (row[1], row[2], row[3], row[4]) != (
                request.title.strip(), request.purpose.strip(), request.workspace_key, request.base_state_identity,
            ):
                raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED"})
            plan_id = row[0]
        plan = _plan(conn, plan_id, principal)
        return _view(conn, plan, principal)


@router.get("/api/v1/reorganisation/plans/{plan_id}", response_model=ReorganisationPlanView)
def get_plan(
    plan_id: UUID,
    principal: Principal = Depends(require_scopes("docs:read")),
) -> ReorganisationPlanView:
    with get_conn() as conn:
        plan = _plan(conn, plan_id, principal)
        return _view(conn, plan, principal)


@router.post("/api/v1/reorganisation/plans/{plan_id}/operations", response_model=ReorganisationPlanView, status_code=201)
def add_operation(
    plan_id: UUID,
    request: ReorganisationOperationCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_scopes("docs:propose")),
) -> ReorganisationPlanView:
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    _validate_payload(request)
    key = idempotency_key.strip()[:256]
    with get_conn() as conn:
        plan = _plan(conn, plan_id, principal, for_update=True)
        require_minimum_workspace_role(conn, principal, plan["workspace_id"], "PROPOSER")
        if plan["author_principal_id"] != principal.principal_id:
            raise HTTPException(status_code=403, detail={"code": "PLAN_AUTHOR_REQUIRED"})
        if plan["status"] not in _MUTABLE:
            raise HTTPException(status_code=409, detail={"code": "PLAN_NOT_MUTABLE", "status": plan["status"]})
        cur = conn.cursor()
        cur.execute(
            "SELECT operation_type, page_resource_id::text, expected_revision, payload "
            "FROM docs.reorganisation_operations WHERE plan_id = %s AND idempotency_key = %s",
            (str(plan_id), key),
        )
        existing = cur.fetchone()
        intended = (
            request.operation_type,
            str(request.page_resource_id) if request.page_resource_id else None,
            request.expected_revision,
            request.payload,
        )
        if existing is not None and tuple(existing) != intended:
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED"})
        if existing is None:
            if request.page_resource_id:
                cur.execute(
                    "SELECT revision, workspace_id::text FROM docs.pages WHERE resource_id = %s",
                    (str(request.page_resource_id),),
                )
                page = cur.fetchone()
                if page is None:
                    raise HTTPException(status_code=404, detail={"code": "PAGE_NOT_FOUND"})
                if page[1] != plan["workspace_id"]:
                    raise HTTPException(status_code=422, detail={"code": "PAGE_OUTSIDE_PLAN_WORKSPACE"})
            if request.sequence is None:
                cur.execute(
                    "SELECT coalesce(max(sequence), -1) + 1 FROM docs.reorganisation_operations WHERE plan_id = %s",
                    (str(plan_id),),
                )
                sequence = cur.fetchone()[0]
            else:
                sequence = request.sequence
            try:
                cur.execute(
                    """
                    INSERT INTO docs.reorganisation_operations
                        (plan_id, sequence, idempotency_key, operation_type,
                         page_resource_id, expected_revision, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(plan_id), sequence, key, request.operation_type,
                        str(request.page_resource_id) if request.page_resource_id else None,
                        request.expected_revision, _json(request.payload),
                    ),
                )
            except psycopg2.errors.UniqueViolation as exc:
                raise HTTPException(status_code=409, detail={"code": "OPERATION_SEQUENCE_CONFLICT"}) from exc
            cur.execute(
                """
                UPDATE docs.reorganisation_plans
                   SET status = 'DRAFT', version = version + 1,
                       analysis_receipt = NULL, validation_receipt = NULL,
                       updated_at = now()
                 WHERE plan_id = %s
                """,
                (str(plan_id),),
            )
        conn.commit()
        refreshed = _plan(conn, plan_id, principal)
        return _view(conn, refreshed, principal)


@router.post("/api/v1/reorganisation/plans/{plan_id}/analyze", response_model=ReorganisationPlanView)
def analyze_plan(
    plan_id: UUID,
    principal: Principal = Depends(require_scopes("docs:propose")),
) -> ReorganisationPlanView:
    with get_conn() as conn:
        plan = _plan(conn, plan_id, principal, for_update=True)
        require_minimum_workspace_role(conn, principal, plan["workspace_id"], "PROPOSER")
        if plan["author_principal_id"] != principal.principal_id:
            raise HTTPException(status_code=403, detail={"code": "PLAN_AUTHOR_REQUIRED"})
        operations = _operations(conn, str(plan_id))
        analysis = _analyse(conn, plan, operations)
        cur = conn.cursor()
        for result in analysis["operations"]:
            cur.execute(
                """
                UPDATE docs.reorganisation_operations
                   SET analysis_result = %s, inverse_operation = %s
                 WHERE operation_id = %s
                """,
                (_json(result), _json(result.get("inverse_operation")), result["operation_id"]),
            )
        cur.execute(
            """
            UPDATE docs.reorganisation_plans
               SET status = 'ANALYZED', analysis_receipt = %s,
                   validation_receipt = NULL, version = version + 1, updated_at = now()
             WHERE plan_id = %s
            """,
            (_json(analysis), str(plan_id)),
        )
        append_event(
            conn,
            event_type="REORGANISATION_PLAN_ANALYZED",
            channel="API",
            producer_id="docplane-reorganisation-api",
            idempotency_key=f"PLAN_ANALYZED:{plan_id}:{plan['version'] + 1}",
            principal=principal,
            workspace_key=plan["workspace_key"],
            resource_type="REORGANISATION_PLAN",
            resource_id=str(plan_id),
            metadata={"passed": analysis["passed"], "errors": len(analysis["errors"]), "warnings": len(analysis["warnings"])},
        )
        conn.commit()
        refreshed = _plan(conn, plan_id, principal)
        return _view(conn, refreshed, principal)


@router.post("/api/v1/reorganisation/plans/{plan_id}/validate", response_model=ReorganisationPlanView)
def validate_plan(
    plan_id: UUID,
    principal: Principal = Depends(require_scopes("docs:propose")),
) -> ReorganisationPlanView:
    with get_conn() as conn:
        plan = _plan(conn, plan_id, principal, for_update=True)
        require_minimum_workspace_role(conn, principal, plan["workspace_id"], "PROPOSER")
        if plan["author_principal_id"] != principal.principal_id:
            raise HTTPException(status_code=403, detail={"code": "PLAN_AUTHOR_REQUIRED"})
        if plan["status"] != "ANALYZED" or not plan["analysis_receipt"]:
            raise HTTPException(status_code=409, detail={"code": "PLAN_ANALYSIS_REQUIRED"})
        current = _analyse(conn, plan, _operations(conn, str(plan_id)))
        errors = list(current["errors"])
        unsupported = [
            operation["operation_type"]
            for operation in _operations(conn, str(plan_id))
            if operation["operation_type"] not in _CHANGE_OPERATION_TYPES
        ]
        if unsupported:
            errors.append({
                "code": "CHANGE_PIPELINE_UNSUPPORTED_OPERATION",
                "operations": sorted(set(unsupported)),
                "message": "the plan can be analysed but these operations are not yet convertible to a WP8 change proposal",
            })
        if current["base_state_identity"] != plan["analysis_receipt"].get("base_state_identity"):
            errors.append({"code": "STATE_CHANGED_SINCE_ANALYSIS"})
        validation = {
            "contract_version": "reorganisation-validation-v1",
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "passed": not errors,
            "errors": errors,
            "warnings": current["warnings"],
            "candidate_state_identity": current["candidate_state_identity"],
            "operations_checked": len(current["operations"]),
            "submission_supported": not errors,
        }
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE docs.reorganisation_plans
               SET status = %s, validation_receipt = %s,
                   version = version + 1, updated_at = now()
             WHERE plan_id = %s
            """,
            ("VALIDATED" if not errors else "ANALYZED", _json(validation), str(plan_id)),
        )
        conn.commit()
        refreshed = _plan(conn, plan_id, principal)
        return _view(conn, refreshed, principal)


@router.post("/api/v1/reorganisation/plans/{plan_id}/submit", response_model=ReorganisationPlanView)
def submit_plan(
    plan_id: UUID,
    request: ReorganisationSubmitRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_scopes("docs:propose")),
) -> ReorganisationPlanView:
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    key = idempotency_key.strip()[:256]
    with get_conn() as conn:
        plan = _plan(conn, plan_id, principal, for_update=True)
        require_minimum_workspace_role(conn, principal, plan["workspace_id"], "PROPOSER")
        if plan["author_principal_id"] != principal.principal_id:
            raise HTTPException(status_code=403, detail={"code": "PLAN_AUTHOR_REQUIRED"})
        if plan["status"] == "SUBMITTED" and plan["linked_change_id"]:
            return _view(conn, plan, principal)
        if plan["status"] != "VALIDATED" or not plan["validation_receipt"] or not plan["validation_receipt"].get("passed"):
            raise HTTPException(status_code=409, detail={"code": "PLAN_VALIDATION_REQUIRED"})
        operations = _operations(conn, str(plan_id))
        unsupported = [operation["operation_type"] for operation in operations if operation["operation_type"] not in _CHANGE_OPERATION_TYPES]
        if unsupported:
            raise HTTPException(status_code=409, detail={"code": "CHANGE_PIPELINE_UNSUPPORTED_OPERATION", "operations": unsupported})
        cur = conn.cursor()
        change_key = "reorganisation:" + str(plan_id) + ":" + key
        cur.execute(
            """
            INSERT INTO docs.change_proposals
                (workspace_key, title, purpose, author_principal_id,
                 idempotency_key, base_state_identity)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (author_principal_id, idempotency_key)
            DO UPDATE SET updated_at = docs.change_proposals.updated_at
            RETURNING change_id::text
            """,
            (
                plan["workspace_key"], plan["title"],
                plan["purpose"] + (("\n\nSubmission note: " + request.note.strip()) if request.note else ""),
                principal.principal_id, change_key,
                plan["analysis_receipt"].get("base_state_identity"),
            ),
        )
        change_id = cur.fetchone()[0]
        for operation in operations:
            cur.execute(
                """
                INSERT INTO docs.change_operations
                    (change_id, sequence, idempotency_key, operation_type,
                     page_resource_id, expected_revision, expected_section_hash, payload)
                VALUES (%s, %s, %s, %s, %s, %s, NULL, %s)
                ON CONFLICT (change_id, idempotency_key) DO NOTHING
                """,
                (
                    change_id, operation["sequence"], "reorganisation:" + operation["operation_id"],
                    operation["operation_type"], operation["page_resource_id"],
                    operation["expected_revision"], _json(operation["payload"]),
                ),
            )
        cur.execute(
            """
            UPDATE docs.reorganisation_plans
               SET status = 'SUBMITTED', linked_change_id = %s,
                   version = version + 1, updated_at = now()
             WHERE plan_id = %s
            """,
            (change_id, str(plan_id)),
        )
        append_event(
            conn,
            event_type="REORGANISATION_PLAN_SUBMITTED",
            channel="API",
            producer_id="docplane-reorganisation-api",
            idempotency_key="PLAN_SUBMITTED:" + key,
            principal=principal,
            workspace_key=plan["workspace_key"],
            resource_type="REORGANISATION_PLAN",
            resource_id=str(plan_id),
            metadata={"change_id": change_id},
        )
        conn.commit()
        refreshed = _plan(conn, plan_id, principal)
        return _view(conn, refreshed, principal)


@router.post("/api/v1/reorganisation/plans/{plan_id}/reviews", response_model=ReorganisationPlanView, status_code=201)
def review_plan(
    plan_id: UUID,
    request: ReorganisationReviewCreate,
    principal: Principal = Depends(require_scopes("docs:review")),
) -> ReorganisationPlanView:
    with get_conn() as conn:
        plan = _plan(conn, plan_id, principal, for_update=True)
        require_minimum_workspace_role(conn, principal, plan["workspace_id"], "REVIEWER")
        if plan["status"] not in {"SUBMITTED", "APPROVED"}:
            raise HTTPException(status_code=409, detail={"code": "PLAN_NOT_REVIEWABLE", "status": plan["status"]})
        if request.reviewed_plan_version != plan["version"]:
            raise HTTPException(status_code=409, detail={"code": "PLAN_VERSION_STALE", "current": plan["version"]})
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO docs.reorganisation_reviews
                (plan_id, reviewer_principal_id, decision, note, reviewed_plan_version)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (str(plan_id), principal.principal_id, request.decision, request.note, request.reviewed_plan_version),
        )
        new_status = plan["status"]
        if request.decision == "APPROVE":
            new_status = "APPROVED"
        elif request.decision == "REQUEST_CHANGES":
            new_status = "DRAFT"
        cur.execute(
            "UPDATE docs.reorganisation_plans SET status = %s, version = version + 1, updated_at = now() WHERE plan_id = %s",
            (new_status, str(plan_id)),
        )
        conn.commit()
        refreshed = _plan(conn, plan_id, principal)
        return _view(conn, refreshed, principal)


@router.post("/api/v1/reorganisation/plans/{plan_id}/execute")
def execute_plan(
    plan_id: UUID,
    principal: Principal = Depends(require_scopes("docs:merge")),
) -> dict[str, Any]:
    with get_conn() as conn:
        plan = _plan(conn, plan_id, principal)
        require_minimum_workspace_role(conn, principal, plan["workspace_id"], "MERGER")
    raise HTTPException(
        status_code=409,
        detail={
            "code": "WP8_EXECUTION_NOT_AVAILABLE",
            "message": "the plan is approved but execution remains held until proposal merge and certified publication are wired",
            "plan_id": str(plan_id),
            "linked_change_id": plan["linked_change_id"],
        },
    )
