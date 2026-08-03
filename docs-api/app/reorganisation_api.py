"""Low-friction structural changes through the same direct publication path."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg2.errors
import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.agent_auth import Principal, require_contributor
from app.db import get_conn
from app.event_store import append_event
from app.publication import evaluate_change, publish_change
from app.reorganisation_models import ReorganisationOperationCreate, ReorganisationPlanCreate

router = APIRouter(tags=["reorganisation-v1"])


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def _key(value: str | None) -> str:
    if not value or not value.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    return value.strip()[:256]


def _operations(conn, plan_id: str) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT operation_id::text, sequence, idempotency_key, operation_type,
               page_resource_id::text, expected_revision, payload,
               analysis_result, inverse_operation, created_at
          FROM docs.reorganisation_operations
         WHERE plan_id = %s
         ORDER BY sequence, operation_id
        """,
        (plan_id,),
    )
    keys = (
        "operation_id", "sequence", "idempotency_key", "operation_type",
        "page_resource_id", "expected_revision", "payload", "analysis_result",
        "inverse_operation", "created_at",
    )
    return [dict(zip(keys, row)) for row in cur.fetchall()]


def _plan(conn, plan_id: str, *, for_update: bool = False) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT plan_id::text, workspace_key, title, purpose,
               author_principal_id::text, status, version, idempotency_key,
               base_state_identity, analysis_receipt, validation_receipt,
               linked_change_id::text, publication_receipt, failure_receipt,
               created_at, updated_at, published_at
          FROM docs.reorganisation_plans
         WHERE plan_id = %s
        """ + (" FOR UPDATE" if for_update else ""),
        (plan_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "REORGANISATION_PLAN_NOT_FOUND"})
    keys = (
        "plan_id", "workspace_key", "title", "purpose", "author_principal_id",
        "status", "version", "idempotency_key", "base_state_identity",
        "analysis_receipt", "validation_receipt", "linked_change_id",
        "publication_receipt", "failure_receipt", "created_at", "updated_at", "published_at",
    )
    plan = dict(zip(keys, row))
    plan["operations"] = _operations(conn, plan_id)
    plan["permitted_actions"] = (
        ["read", "add_operation", "analyze", "validate", "publish"]
        if plan["status"] in {"DRAFT", "ANALYZED", "VALIDATED", "FAILED"}
        else ["read"]
    )
    return plan


def _as_change(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "change_id": plan["linked_change_id"] or plan["plan_id"],
        "workspace_key": plan["workspace_key"],
        "title": plan["title"],
        "purpose": plan["purpose"],
        "author_principal_id": plan["author_principal_id"],
        "idempotency_key": plan["idempotency_key"],
        "status": "DRAFT",
        "base_state_identity": plan["base_state_identity"],
    }


def _as_change_operations(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "operation_id": operation["operation_id"],
            "sequence": operation["sequence"],
            "idempotency_key": "reorganisation:" + operation["operation_id"],
            "operation_type": operation["operation_type"],
            "page_resource_id": operation["page_resource_id"],
            "expected_revision": operation["expected_revision"],
            "expected_section_hash": None,
            "payload": operation["payload"],
            "validation_result": None,
            "created_at": operation["created_at"],
        }
        for operation in plan["operations"]
    ]


@router.get("/api/v1/reorganisation/tree")
def tree(principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT resource_id::text, path, title, nav_path, nav_order, revision, version,
                   status, w.workspace_key
              FROM docs.pages p
              JOIN docplane.workspaces w ON w.workspace_id = p.workspace_id
             ORDER BY nav_path, path
            """
        )
        rows = cur.fetchall()
        cur.execute("SELECT from_path, to_path FROM docs.redirects ORDER BY from_path")
        redirects = [{"from_path": row[0], "to_path": row[1]} for row in cur.fetchall()]
    pages = [
        {
            "resource_id": row[0], "path": row[1], "title": row[2],
            "nav_path": row[3], "nav_order": row[4], "revision": row[5], "version": row[6],
            "status": row[7], "workspace_key": row[8],
        }
        for row in rows
    ]
    return {"contract_version": "reorganisation-tree-v1", "pages": pages, "redirects": redirects}


@router.get("/api/v1/reorganisation/plans")
def list_plans(
    status: str = Query(default="open", pattern="^(open|all|DRAFT|ANALYZED|VALIDATED|PUBLISHED|FAILED)$"),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    params: list[Any] = []
    if status == "open":
        where = " WHERE status NOT IN ('PUBLISHED', 'ABANDONED')"
    elif status == "all":
        where = ""
    else:
        where = " WHERE status = %s"
        params.append(status)
    params.append(limit)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT plan_id::text FROM docs.reorganisation_plans" + where + " ORDER BY updated_at DESC LIMIT %s", params)
        ids = [row[0] for row in cur.fetchall()]
        plans = [_plan(conn, plan_id) for plan_id in ids]
    return {"plans": plans, "count": len(plans)}


@router.get("/api/v1/reorganisation/plans/{plan_id}")
def get_plan(plan_id: UUID, principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        return _plan(conn, str(plan_id))


@router.post("/api/v1/reorganisation/plans", status_code=201)
def create_plan(
    request: ReorganisationPlanCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM docplane.workspaces WHERE workspace_key = %s", (request.workspace_key,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail={"code": "WORKSPACE_NOT_FOUND"})
        try:
            cur.execute(
                """
                INSERT INTO docs.reorganisation_plans
                    (workspace_key, title, purpose, author_principal_id,
                     idempotency_key, base_state_identity)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING plan_id::text
                """,
                (request.workspace_key, request.title.strip(), request.purpose.strip(), principal.principal_id, key, request.base_state_identity),
            )
            plan_id = cur.fetchone()[0]
            append_event(
                conn,
                event_type="REORGANISATION_PLAN_CREATED",
                channel="API",
                producer_id="docplane-reorganisation",
                idempotency_key=f"PLAN_CREATED:{principal.principal_id}:{key}",
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
                """
                SELECT plan_id::text, workspace_key, title, purpose, base_state_identity
                  FROM docs.reorganisation_plans
                 WHERE author_principal_id = %s AND idempotency_key = %s
                """,
                (principal.principal_id, key),
            )
            row = cur.fetchone()
            intended = (
                request.workspace_key, request.title.strip(), request.purpose.strip(),
                request.base_state_identity,
            )
            if row is None or tuple(row[1:]) != intended:
                raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED"})
            plan_id = row[0]
        return _plan(conn, plan_id)


@router.post("/api/v1/reorganisation/plans/{plan_id}/operations", status_code=201)
def add_operation(
    plan_id: UUID,
    request: ReorganisationOperationCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    with get_conn() as conn:
        plan = _plan(conn, str(plan_id), for_update=True)
        if plan["status"] not in {"DRAFT", "ANALYZED", "VALIDATED", "FAILED"}:
            raise HTTPException(status_code=409, detail={"code": "PLAN_NOT_MUTABLE", "status": plan["status"]})
        cur = conn.cursor()
        if request.sequence is None:
            cur.execute("SELECT coalesce(max(sequence), -1) + 1 FROM docs.reorganisation_operations WHERE plan_id = %s", (str(plan_id),))
            sequence = int(cur.fetchone()[0])
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
            cur.execute(
                """
                UPDATE docs.reorganisation_plans
                   SET status = 'DRAFT', version = version + 1,
                       analysis_receipt = NULL, validation_receipt = NULL,
                       failure_receipt = NULL, updated_at = now()
                 WHERE plan_id = %s
                """,
                (str(plan_id),),
            )
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            cur = conn.cursor()
            cur.execute("SELECT operation_type, page_resource_id::text, expected_revision, payload FROM docs.reorganisation_operations WHERE plan_id = %s AND idempotency_key = %s", (str(plan_id), key))
            existing = cur.fetchone()
            intended = (request.operation_type, str(request.page_resource_id) if request.page_resource_id else None, request.expected_revision, request.payload)
            if existing is None or tuple(existing) != intended:
                raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED"})
        return _plan(conn, str(plan_id))


def _evaluate_and_store(conn, plan: dict[str, Any], *, validate: bool) -> dict[str, Any]:
    evaluation = evaluate_change(conn, _as_change(plan), _as_change_operations(plan))
    summary = {
        key: evaluation[key]
        for key in (
            "passed", "errors", "warnings", "operations_checked",
            "base_state_identity", "candidate_identity", "validated_at", "contract_version",
        )
    }
    cur = conn.cursor()
    for result in evaluation["operations"]:
        cur.execute(
            "UPDATE docs.reorganisation_operations SET analysis_result = %s WHERE operation_id = %s",
            (_json(result), result["operation_id"]),
        )
    if validate:
        status = "VALIDATED" if evaluation["passed"] else "ANALYZED"
        cur.execute(
            "UPDATE docs.reorganisation_plans SET status = %s, validation_receipt = %s, version = version + 1, updated_at = now() WHERE plan_id = %s",
            (status, _json(summary), plan["plan_id"]),
        )
    else:
        cur.execute(
            "UPDATE docs.reorganisation_plans SET status = 'ANALYZED', analysis_receipt = %s, validation_receipt = NULL, version = version + 1, updated_at = now() WHERE plan_id = %s",
            (_json(summary), plan["plan_id"]),
        )
    return summary


@router.post("/api/v1/reorganisation/plans/{plan_id}/analyze")
def analyze_plan(plan_id: UUID, principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        plan = _plan(conn, str(plan_id), for_update=True)
        _evaluate_and_store(conn, plan, validate=False)
        conn.commit()
        return _plan(conn, str(plan_id))


@router.post("/api/v1/reorganisation/plans/{plan_id}/validate")
def validate_plan(plan_id: UUID, principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        plan = _plan(conn, str(plan_id), for_update=True)
        _evaluate_and_store(conn, plan, validate=True)
        conn.commit()
        return _plan(conn, str(plan_id))


@router.post("/api/v1/reorganisation/plans/{plan_id}/publish")
def publish_plan(
    plan_id: UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    with get_conn() as conn:
        plan = _plan(conn, str(plan_id), for_update=True)
        if plan["status"] == "PUBLISHED" and plan["linked_change_id"]:
            return plan
        evaluation = evaluate_change(conn, _as_change(plan), _as_change_operations(plan), lock=True, acting_principal_id=str(principal.principal_id))
        if not evaluation["passed"]:
            cur = conn.cursor()
            cur.execute(
                "UPDATE docs.reorganisation_plans SET status = 'FAILED', failure_receipt = %s, updated_at = now() WHERE plan_id = %s",
                (_json({"errors": evaluation["errors"], "failed_at": datetime.now(timezone.utc).isoformat()}), str(plan_id)),
            )
            conn.commit()
            raise HTTPException(status_code=409, detail={"code": "PLAN_VALIDATION_FAILED", "errors": evaluation["errors"]})
        cur = conn.cursor()
        if plan["linked_change_id"]:
            change_id = plan["linked_change_id"]
        else:
            cur.execute(
                """
                INSERT INTO docs.changes
                    (workspace_key, title, purpose, author_principal_id,
                     idempotency_key, base_state_identity, status,
                     validation_summary)
                VALUES (%s, %s, %s, %s, %s, %s, 'VALIDATED', %s)
                RETURNING change_id::text
                """,
                (
                    plan["workspace_key"], plan["title"], plan["purpose"],
                    principal.principal_id, f"reorganisation:{plan_id}:{key}",
                    evaluation["base_state_identity"],
                    _json({key: evaluation[key] for key in ("passed", "errors", "warnings", "operations_checked", "base_state_identity", "candidate_identity", "validated_at", "contract_version")}),
                ),
            )
            change_id = cur.fetchone()[0]
            for operation in _as_change_operations(plan):
                cur.execute(
                    """
                    INSERT INTO docs.change_operations
                        (change_id, sequence, idempotency_key, operation_type,
                         page_resource_id, expected_revision, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        change_id, operation["sequence"], operation["idempotency_key"],
                        operation["operation_type"], operation["page_resource_id"],
                        operation["expected_revision"], _json(operation["payload"]),
                    ),
                )
            cur.execute(
                "UPDATE docs.reorganisation_plans SET linked_change_id = %s, validation_receipt = %s, updated_at = now() WHERE plan_id = %s",
                (change_id, _json({"passed": True, "candidate_identity": evaluation["candidate_identity"]}), str(plan_id)),
            )
            conn.commit()
    change = publish_change(change_id, principal)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE docs.reorganisation_plans
               SET status = 'PUBLISHED', publication_receipt = %s,
                   failure_receipt = NULL, published_at = now(),
                   version = version + 1, updated_at = now()
             WHERE plan_id = %s
            """,
            (_json(change.get("publication_receipt") or {}), str(plan_id)),
        )
        conn.commit()
        return _plan(conn, str(plan_id))
