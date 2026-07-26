"""Endpoint-first active-work API.

Initiatives, soaks and parking records are coordination state, not certified durable knowledge. Durable
conclusions are promoted through linked change proposals rather than reclassifying the work log.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg2.errors
import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.agent_auth import Principal, require_scopes
from app.db import get_conn
from app.event_store import append_event
from app.work_models import (
    ActivityCreate,
    InitiativeCreate,
    InitiativeLinkCreate,
    MembershipSet,
    PromotionUpdate,
    WorkspaceCreate,
    WorkTransition,
)


router = APIRouter(tags=["work-v1"])

_ALLOWED_TRANSITIONS = {
    "BACKLOG": {"ACTIVE", "PAUSED", "PARKED", "ABANDONED"},
    "ACTIVE": {"BLOCKED", "SOAKING", "PAUSED", "PARKED", "COMPLETE", "ABANDONED"},
    "BLOCKED": {"ACTIVE", "PAUSED", "PARKED", "ABANDONED"},
    "SOAKING": {"ACTIVE", "BLOCKED", "PAUSED", "COMPLETE", "ABANDONED"},
    "PAUSED": {"ACTIVE", "PARKED", "ABANDONED"},
    "PARKED": {"BACKLOG", "ACTIVE", "ABANDONED"},
    "COMPLETE": set(),
    "ABANDONED": set(),
}


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def _request_hash(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_idempotency(value: str | None) -> str:
    if not value or not value.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    return value.strip()[:256]


def _existing_receipt(conn, principal: Principal, key: str, operation: str, request_hash: str):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT operation_type, request_hash, response
          FROM work.mutation_receipts
         WHERE principal_id = %s AND idempotency_key = %s
        """,
        (principal.principal_id, key),
    )
    row = cur.fetchone()
    if row is None:
        return None
    if row[0] != operation or row[1] != request_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "IDEMPOTENCY_KEY_REUSED",
                "message": "the key already identifies a different work mutation",
            },
        )
    return row[2]


def _save_receipt(
    conn,
    principal: Principal,
    key: str,
    operation: str,
    resource_id: str | None,
    request_hash: str,
    response: dict[str, Any],
) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO work.mutation_receipts
            (principal_id, idempotency_key, operation_type, resource_id, request_hash, response)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            principal.principal_id,
            key,
            operation,
            resource_id,
            request_hash,
            _json(response),
        ),
    )


def _workspace_dict(row) -> dict[str, Any]:
    keys = (
        "workspace_id",
        "workspace_key",
        "name",
        "workspace_kind",
        "visibility",
        "mutation_policy",
        "default_verification_days",
        "retention_policy",
        "system_workspace",
        "created_at",
        "updated_at",
    )
    value = dict(zip(keys, row))
    value["workspace_id"] = str(value["workspace_id"])
    return value


def _initiative_dict(row) -> dict[str, Any]:
    keys = (
        "initiative_id",
        "initiative_key",
        "workspace_id",
        "workspace_key",
        "title",
        "objective",
        "scope",
        "owner_principal_id",
        "work_state",
        "priority",
        "version",
        "target_date",
        "review_due_at",
        "blocker_summary",
        "soak_started_at",
        "soak_review_at",
        "soak_success_criteria",
        "soak_failure_conditions",
        "parked_reason",
        "parked_review_at",
        "parked_indefinitely",
        "promotion_state",
        "promotion_change_id",
        "completed_at",
        "created_at",
        "updated_at",
    )
    value = dict(zip(keys, row))
    for key in ("initiative_id", "workspace_id", "owner_principal_id", "promotion_change_id"):
        if value[key] is not None:
            value[key] = str(value[key])
    value["uri"] = f"docplane://initiatives/{value['initiative_id']}"
    return value


def _initiative_select() -> str:
    return """
        SELECT i.initiative_id, i.initiative_key, i.workspace_id, w.workspace_key,
               i.title, i.objective, i.scope, i.owner_principal_id,
               i.work_state, i.priority, i.version, i.target_date, i.review_due_at,
               i.blocker_summary, i.soak_started_at, i.soak_review_at,
               i.soak_success_criteria, i.soak_failure_conditions,
               i.parked_reason, i.parked_review_at, i.parked_indefinitely,
               i.promotion_state, i.promotion_change_id, i.completed_at,
               i.created_at, i.updated_at
          FROM work.initiatives i
          JOIN docplane.workspaces w ON w.workspace_id = i.workspace_id
    """


def _workspace_role(conn, principal: Principal, workspace_id: str, allowed: set[str]) -> str:
    if "admin:workspaces" in principal.scopes:
        return "ADMIN"
    cur = conn.cursor()
    cur.execute(
        "SELECT role FROM docplane.workspace_memberships WHERE workspace_id = %s AND principal_id = %s",
        (workspace_id, principal.principal_id),
    )
    row = cur.fetchone()
    if row is None or row[0] not in allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "WORKSPACE_ROLE_REQUIRED",
                "workspace_id": workspace_id,
                "allowed_roles": sorted(allowed),
            },
        )
    return row[0]


def _load_initiative(conn, initiative_id: UUID, *, for_update: bool = False) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        _initiative_select()
        + " WHERE i.initiative_id = %s"
        + (" FOR UPDATE OF i" if for_update else ""),
        (str(initiative_id),),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "INITIATIVE_NOT_FOUND"})
    return _initiative_dict(row)


@router.get("/api/v1/workspaces")
def list_workspaces(
    principal: Principal = Depends(require_scopes("work:read")),
) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        if "admin:workspaces" in principal.scopes:
            cur.execute(
                """
                SELECT workspace_id, workspace_key, name, workspace_kind, visibility,
                       mutation_policy, default_verification_days, retention_policy,
                       system_workspace, created_at, updated_at
                  FROM docplane.workspaces
                 ORDER BY system_workspace DESC, workspace_key
                """
            )
        else:
            cur.execute(
                """
                SELECT w.workspace_id, w.workspace_key, w.name, w.workspace_kind, w.visibility,
                       w.mutation_policy, w.default_verification_days, w.retention_policy,
                       w.system_workspace, w.created_at, w.updated_at
                  FROM docplane.workspaces w
                  JOIN docplane.workspace_memberships m ON m.workspace_id = w.workspace_id
                 WHERE m.principal_id = %s
                 ORDER BY w.system_workspace DESC, w.workspace_key
                """,
                (principal.principal_id,),
            )
        rows = cur.fetchall()
    return {"workspaces": [_workspace_dict(row) for row in rows], "count": len(rows)}


@router.post("/api/v1/workspaces", status_code=201)
def create_workspace(
    request: WorkspaceCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_scopes("admin:workspaces")),
) -> dict[str, Any]:
    key = _require_idempotency(idempotency_key)
    request_value = request.model_dump(mode="json")
    digest = _request_hash(request_value)
    with get_conn() as conn:
        existing = _existing_receipt(conn, principal, key, "CREATE_WORKSPACE", digest)
        if existing is not None:
            return existing
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO docplane.workspaces
                    (workspace_key, name, workspace_kind, visibility, mutation_policy,
                     default_verification_days, retention_policy)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING workspace_id, workspace_key, name, workspace_kind, visibility,
                          mutation_policy, default_verification_days, retention_policy,
                          system_workspace, created_at, updated_at
                """,
                (
                    request.workspace_key,
                    request.name.strip(),
                    request.workspace_kind,
                    request.visibility,
                    request.mutation_policy,
                    request.default_verification_days,
                    _json(request.retention_policy),
                ),
            )
            response = _workspace_dict(cur.fetchone())
            cur.execute(
                """
                INSERT INTO docplane.workspace_memberships
                    (workspace_id, principal_id, role, created_by)
                VALUES (%s, %s, 'OWNER', %s)
                """,
                (response["workspace_id"], principal.principal_id, principal.principal_id),
            )
            _save_receipt(
                conn,
                principal,
                key,
                "CREATE_WORKSPACE",
                response["workspace_id"],
                digest,
                response,
            )
            append_event(
                conn,
                event_type="WORKSPACE_CREATED",
                channel="API",
                producer_id="docplane-work-api",
                idempotency_key="CREATE_WORKSPACE:" + key,
                principal=principal,
                resource_type="WORKSPACE",
                resource_id=response["workspace_id"],
                workspace_key=response["workspace_key"],
                metadata={"workspace_kind": response["workspace_kind"]},
            )
            conn.commit()
            return response
        except psycopg2.errors.UniqueViolation as exc:
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail={"code": "WORKSPACE_KEY_EXISTS", "workspace_key": request.workspace_key},
            ) from exc


@router.put("/api/v1/workspaces/{workspace_id}/members/{member_principal_id}")
def set_workspace_membership(
    workspace_id: UUID,
    member_principal_id: UUID,
    request: MembershipSet,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_scopes("admin:workspaces")),
) -> dict[str, Any]:
    if request.principal_id != member_principal_id:
        raise HTTPException(status_code=422, detail={"code": "MEMBERSHIP_PRINCIPAL_MISMATCH"})
    key = _require_idempotency(idempotency_key)
    request_value = {
        "workspace_id": str(workspace_id),
        "principal_id": str(member_principal_id),
        "role": request.role,
    }
    digest = _request_hash(request_value)
    with get_conn() as conn:
        existing = _existing_receipt(conn, principal, key, "SET_WORKSPACE_MEMBERSHIP", digest)
        if existing is not None:
            return existing
        cur = conn.cursor()
        cur.execute("SELECT workspace_key FROM docplane.workspaces WHERE workspace_id = %s", (str(workspace_id),))
        workspace = cur.fetchone()
        if workspace is None:
            raise HTTPException(status_code=404, detail={"code": "WORKSPACE_NOT_FOUND"})
        cur.execute("SELECT 1 FROM docplane.principals WHERE principal_id = %s", (str(member_principal_id),))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail={"code": "PRINCIPAL_NOT_FOUND"})
        cur.execute(
            """
            INSERT INTO docplane.workspace_memberships
                (workspace_id, principal_id, role, created_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (workspace_id, principal_id)
            DO UPDATE SET role = EXCLUDED.role, created_by = EXCLUDED.created_by, created_at = now()
            """,
            (str(workspace_id), str(member_principal_id), request.role, principal.principal_id),
        )
        response = request_value
        _save_receipt(
            conn,
            principal,
            key,
            "SET_WORKSPACE_MEMBERSHIP",
            str(workspace_id),
            digest,
            response,
        )
        append_event(
            conn,
            event_type="WORKSPACE_MEMBERSHIP_UPDATED",
            channel="API",
            producer_id="docplane-work-api",
            idempotency_key="SET_WORKSPACE_MEMBERSHIP:" + key,
            principal=principal,
            workspace_key=workspace[0],
            resource_type="WORKSPACE",
            resource_id=str(workspace_id),
            metadata={"member_principal_id": str(member_principal_id), "role": request.role},
        )
        conn.commit()
    return response


@router.post("/api/v1/initiatives", status_code=201)
def create_initiative(
    request: InitiativeCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_scopes("work:update")),
) -> dict[str, Any]:
    key = _require_idempotency(idempotency_key)
    request_value = request.model_dump(mode="json")
    digest = _request_hash(request_value)
    with get_conn() as conn:
        existing = _existing_receipt(conn, principal, key, "CREATE_INITIATIVE", digest)
        if existing is not None:
            return existing
        _workspace_role(conn, principal, str(request.workspace_id), {"EDITOR", "OWNER"})
        cur = conn.cursor()
        cur.execute(
            "SELECT workspace_kind, workspace_key FROM docplane.workspaces WHERE workspace_id = %s",
            (str(request.workspace_id),),
        )
        workspace = cur.fetchone()
        if workspace is None:
            raise HTTPException(status_code=404, detail={"code": "WORKSPACE_NOT_FOUND"})
        if workspace[0] != "WORK":
            raise HTTPException(
                status_code=422,
                detail={"code": "INITIATIVE_REQUIRES_WORK_WORKSPACE", "workspace_kind": workspace[0]},
            )
        cur.execute(
            """
            INSERT INTO work.initiatives
                (initiative_key, workspace_id, title, objective, scope,
                 owner_principal_id, idempotency_key, work_state, priority,
                 target_date, review_due_at, blocker_summary,
                 soak_started_at, soak_review_at, soak_success_criteria,
                 soak_failure_conditions, parked_reason, parked_review_at,
                 parked_indefinitely)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING initiative_id
            """,
            (
                request.initiative_key,
                str(request.workspace_id),
                request.title.strip(),
                request.objective.strip(),
                request.scope,
                principal.principal_id,
                key,
                request.work_state,
                request.priority,
                request.target_date,
                request.review_due_at,
                request.blocker_summary,
                request.soak_started_at,
                request.soak_review_at,
                request.soak_success_criteria,
                request.soak_failure_conditions,
                request.parked_reason,
                request.parked_review_at,
                request.parked_indefinitely,
            ),
        )
        initiative_id = cur.fetchone()[0]
        response = _load_initiative(conn, initiative_id)
        _save_receipt(
            conn,
            principal,
            key,
            "CREATE_INITIATIVE",
            str(initiative_id),
            digest,
            response,
        )
        append_event(
            conn,
            event_type="INITIATIVE_CREATED",
            channel="API",
            producer_id="docplane-work-api",
            idempotency_key="CREATE_INITIATIVE:" + key,
            principal=principal,
            workspace_key=workspace[1],
            resource_type="INITIATIVE",
            resource_id=str(initiative_id),
            metadata={"work_state": request.work_state, "priority": request.priority},
        )
        conn.commit()
    return response


@router.get("/api/v1/initiatives")
def list_initiatives(
    workspace_id: UUID | None = None,
    work_state: str | None = Query(default=None),
    owner_principal_id: UUID | None = None,
    include_closed: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_scopes("work:read")),
) -> dict[str, Any]:
    predicates: list[str] = []
    params: list[Any] = []
    if "admin:workspaces" not in principal.scopes:
        predicates.append(
            "EXISTS (SELECT 1 FROM docplane.workspace_memberships m "
            "WHERE m.workspace_id = i.workspace_id AND m.principal_id = %s)"
        )
        params.append(principal.principal_id)
    if workspace_id:
        predicates.append("i.workspace_id = %s")
        params.append(str(workspace_id))
    if work_state:
        if work_state not in _ALLOWED_TRANSITIONS:
            raise HTTPException(status_code=422, detail={"code": "WORK_STATE_INVALID"})
        predicates.append("i.work_state = %s")
        params.append(work_state)
    elif not include_closed:
        predicates.append("i.work_state NOT IN ('COMPLETE', 'ABANDONED')")
    if owner_principal_id:
        predicates.append("i.owner_principal_id = %s")
        params.append(str(owner_principal_id))
    params.append(limit)
    where = " WHERE " + " AND ".join(predicates) if predicates else ""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _initiative_select() + where + " ORDER BY i.updated_at DESC, i.initiative_key LIMIT %s",
            params,
        )
        rows = cur.fetchall()
    initiatives = [_initiative_dict(row) for row in rows]
    return {"initiatives": initiatives, "count": len(initiatives)}


@router.get("/api/v1/initiatives/{initiative_id}")
def get_initiative(
    initiative_id: UUID,
    principal: Principal = Depends(require_scopes("work:read")),
) -> dict[str, Any]:
    with get_conn() as conn:
        initiative = _load_initiative(conn, initiative_id)
        _workspace_role(
            conn,
            principal,
            initiative["workspace_id"],
            {"READER", "PROPOSER", "EDITOR", "REVIEWER", "MERGER", "OWNER"},
        )
        cur = conn.cursor()
        cur.execute(
            """
            SELECT activity_id::text, author_principal_id::text, activity_type, body, metadata, created_at
              FROM work.initiative_activities
             WHERE initiative_id = %s
             ORDER BY created_at, activity_id
            """,
            (str(initiative_id),),
        )
        activities = [
            {
                "activity_id": row[0],
                "author_principal_id": row[1],
                "activity_type": row[2],
                "body": row[3],
                "metadata": row[4],
                "created_at": row[5],
            }
            for row in cur.fetchall()
        ]
        cur.execute(
            """
            SELECT relation, resource_type, resource_id, created_by::text, created_at
              FROM work.initiative_links
             WHERE initiative_id = %s
             ORDER BY relation, resource_type, resource_id
            """,
            (str(initiative_id),),
        )
        links = [
            {
                "relation": row[0],
                "resource_type": row[1],
                "resource_id": row[2],
                "created_by": row[3],
                "created_at": row[4],
            }
            for row in cur.fetchall()
        ]
        cur.execute(
            """
            SELECT depends_on_initiative_id::text, dependency_kind
              FROM work.initiative_dependencies
             WHERE initiative_id = %s
             ORDER BY depends_on_initiative_id
            """,
            (str(initiative_id),),
        )
        dependencies = [
            {"initiative_id": row[0], "dependency_kind": row[1]} for row in cur.fetchall()
        ]
    return {**initiative, "activities": activities, "links": links, "dependencies": dependencies}


@router.post("/api/v1/initiatives/{initiative_id}/transition")
def transition_initiative(
    initiative_id: UUID,
    request: WorkTransition,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_scopes("work:update")),
) -> dict[str, Any]:
    key = _require_idempotency(idempotency_key)
    request_value = {"initiative_id": str(initiative_id), **request.model_dump(mode="json")}
    digest = _request_hash(request_value)
    with get_conn() as conn:
        existing = _existing_receipt(conn, principal, key, "TRANSITION_INITIATIVE", digest)
        if existing is not None:
            return existing
        initiative = _load_initiative(conn, initiative_id, for_update=True)
        _workspace_role(conn, principal, initiative["workspace_id"], {"EDITOR", "OWNER"})
        if initiative["version"] != request.expected_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "INITIATIVE_VERSION_STALE",
                    "expected": request.expected_version,
                    "current": initiative["version"],
                },
            )
        current = initiative["work_state"]
        if request.work_state not in _ALLOWED_TRANSITIONS[current]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "WORK_TRANSITION_INVALID",
                    "current": current,
                    "requested": request.work_state,
                    "allowed": sorted(_ALLOWED_TRANSITIONS[current]),
                },
            )
        completed_at = (
            datetime.now(timezone.utc)
            if request.work_state in {"COMPLETE", "ABANDONED"}
            else None
        )
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE work.initiatives
               SET work_state = %s,
                   version = version + 1,
                   review_due_at = %s,
                   blocker_summary = %s,
                   soak_started_at = %s,
                   soak_review_at = %s,
                   soak_success_criteria = %s,
                   soak_failure_conditions = %s,
                   parked_reason = %s,
                   parked_review_at = %s,
                   parked_indefinitely = %s,
                   completed_at = %s
             WHERE initiative_id = %s AND version = %s
            """,
            (
                request.work_state,
                request.review_due_at,
                request.blocker_summary if request.work_state == "BLOCKED" else None,
                request.soak_started_at if request.work_state == "SOAKING" else initiative["soak_started_at"],
                request.soak_review_at if request.work_state == "SOAKING" else initiative["soak_review_at"],
                request.soak_success_criteria if request.work_state == "SOAKING" else initiative["soak_success_criteria"],
                request.soak_failure_conditions if request.work_state == "SOAKING" else initiative["soak_failure_conditions"],
                request.parked_reason if request.work_state == "PARKED" else initiative["parked_reason"],
                request.parked_review_at if request.work_state == "PARKED" else initiative["parked_review_at"],
                request.parked_indefinitely if request.work_state == "PARKED" else initiative["parked_indefinitely"],
                completed_at,
                str(initiative_id),
                request.expected_version,
            ),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail={"code": "INITIATIVE_TRANSITION_RACE"})
        if request.note:
            cur.execute(
                """
                INSERT INTO work.initiative_activities
                    (initiative_id, author_principal_id, idempotency_key, activity_type, body, metadata)
                VALUES (%s, %s, %s, 'NOTE', %s, %s)
                """,
                (
                    str(initiative_id),
                    principal.principal_id,
                    key + ":transition-note",
                    request.note,
                    _json({"from_state": current, "to_state": request.work_state}),
                ),
            )
        response = _load_initiative(conn, initiative_id)
        _save_receipt(
            conn,
            principal,
            key,
            "TRANSITION_INITIATIVE",
            str(initiative_id),
            digest,
            response,
        )
        append_event(
            conn,
            event_type="INITIATIVE_UPDATED",
            channel="API",
            producer_id="docplane-work-api",
            idempotency_key="TRANSITION_INITIATIVE:" + key,
            principal=principal,
            workspace_key=response["workspace_key"],
            resource_type="INITIATIVE",
            resource_id=str(initiative_id),
            metadata={"from_state": current, "to_state": request.work_state, "version": response["version"]},
        )
        conn.commit()
    return response


@router.post("/api/v1/initiatives/{initiative_id}/activities", status_code=201)
def add_initiative_activity(
    initiative_id: UUID,
    request: ActivityCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_scopes("work:update")),
) -> dict[str, Any]:
    key = _require_idempotency(idempotency_key)
    request_value = {"initiative_id": str(initiative_id), **request.model_dump(mode="json")}
    digest = _request_hash(request_value)
    with get_conn() as conn:
        existing = _existing_receipt(conn, principal, key, "ADD_INITIATIVE_ACTIVITY", digest)
        if existing is not None:
            return existing
        initiative = _load_initiative(conn, initiative_id)
        _workspace_role(conn, principal, initiative["workspace_id"], {"EDITOR", "OWNER"})
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO work.initiative_activities
                (initiative_id, author_principal_id, idempotency_key, activity_type, body, metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING activity_id::text, created_at
            """,
            (
                str(initiative_id),
                principal.principal_id,
                key,
                request.activity_type,
                request.body.strip(),
                _json(request.metadata),
            ),
        )
        activity_id, created_at = cur.fetchone()
        cur.execute(
            "UPDATE work.initiatives SET version = version + 1 WHERE initiative_id = %s RETURNING version",
            (str(initiative_id),),
        )
        version = cur.fetchone()[0]
        response = {
            "activity_id": activity_id,
            "initiative_id": str(initiative_id),
            "activity_type": request.activity_type,
            "body": request.body.strip(),
            "metadata": request.metadata,
            "created_at": created_at,
            "initiative_version": version,
        }
        _save_receipt(
            conn,
            principal,
            key,
            "ADD_INITIATIVE_ACTIVITY",
            str(initiative_id),
            digest,
            response,
        )
        append_event(
            conn,
            event_type="INITIATIVE_ACTIVITY_ADDED",
            channel="API",
            producer_id="docplane-work-api",
            idempotency_key="ADD_INITIATIVE_ACTIVITY:" + key,
            principal=principal,
            workspace_key=initiative["workspace_key"],
            resource_type="INITIATIVE",
            resource_id=str(initiative_id),
            metadata={"activity_type": request.activity_type, "version": version},
        )
        conn.commit()
    return response


@router.post("/api/v1/initiatives/{initiative_id}/links", status_code=201)
def add_initiative_link(
    initiative_id: UUID,
    request: InitiativeLinkCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_scopes("work:update")),
) -> dict[str, Any]:
    key = _require_idempotency(idempotency_key)
    request_value = {"initiative_id": str(initiative_id), **request.model_dump(mode="json")}
    digest = _request_hash(request_value)
    with get_conn() as conn:
        existing = _existing_receipt(conn, principal, key, "ADD_INITIATIVE_LINK", digest)
        if existing is not None:
            return existing
        initiative = _load_initiative(conn, initiative_id)
        _workspace_role(conn, principal, initiative["workspace_id"], {"EDITOR", "OWNER"})
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO work.initiative_links
                (initiative_id, relation, resource_type, resource_id, created_by)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (initiative_id, relation, resource_type, resource_id) DO NOTHING
            """,
            (
                str(initiative_id),
                request.relation,
                request.resource_type,
                request.resource_id,
                principal.principal_id,
            ),
        )
        response = {
            "initiative_id": str(initiative_id),
            "relation": request.relation,
            "resource_type": request.resource_type,
            "resource_id": request.resource_id,
            "created": cur.rowcount == 1,
        }
        _save_receipt(
            conn,
            principal,
            key,
            "ADD_INITIATIVE_LINK",
            str(initiative_id),
            digest,
            response,
        )
        append_event(
            conn,
            event_type="INITIATIVE_LINKED",
            channel="API",
            producer_id="docplane-work-api",
            idempotency_key="ADD_INITIATIVE_LINK:" + key,
            principal=principal,
            workspace_key=initiative["workspace_key"],
            resource_type="INITIATIVE",
            resource_id=str(initiative_id),
            metadata={"relation": request.relation, "linked_type": request.resource_type},
        )
        conn.commit()
    return response


@router.post("/api/v1/initiatives/{initiative_id}/promotion")
def update_promotion(
    initiative_id: UUID,
    request: PromotionUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_scopes("work:update")),
) -> dict[str, Any]:
    key = _require_idempotency(idempotency_key)
    request_value = {"initiative_id": str(initiative_id), **request.model_dump(mode="json")}
    digest = _request_hash(request_value)
    with get_conn() as conn:
        existing = _existing_receipt(conn, principal, key, "UPDATE_PROMOTION", digest)
        if existing is not None:
            return existing
        initiative = _load_initiative(conn, initiative_id, for_update=True)
        _workspace_role(conn, principal, initiative["workspace_id"], {"EDITOR", "OWNER"})
        if initiative["version"] != request.expected_version:
            raise HTTPException(
                status_code=409,
                detail={"code": "INITIATIVE_VERSION_STALE", "current": initiative["version"]},
            )
        if request.promotion_change_id:
            cur = conn.cursor()
            cur.execute(
                "SELECT status FROM docs.change_proposals WHERE change_id = %s",
                (str(request.promotion_change_id),),
            )
            change = cur.fetchone()
            if change is None:
                raise HTTPException(status_code=404, detail={"code": "PROMOTION_CHANGE_NOT_FOUND"})
            if request.promotion_state == "PROMOTED" and change[0] != "MERGED":
                raise HTTPException(
                    status_code=409,
                    detail={"code": "PROMOTION_CHANGE_NOT_MERGED", "change_status": change[0]},
                )
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE work.initiatives
               SET promotion_state = %s, promotion_change_id = %s, version = version + 1
             WHERE initiative_id = %s AND version = %s
            """,
            (
                request.promotion_state,
                str(request.promotion_change_id) if request.promotion_change_id else None,
                str(initiative_id),
                request.expected_version,
            ),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail={"code": "INITIATIVE_PROMOTION_RACE"})
        response = _load_initiative(conn, initiative_id)
        _save_receipt(
            conn,
            principal,
            key,
            "UPDATE_PROMOTION",
            str(initiative_id),
            digest,
            response,
        )
        append_event(
            conn,
            event_type="INITIATIVE_PROMOTION_UPDATED",
            channel="API",
            producer_id="docplane-work-api",
            idempotency_key="UPDATE_PROMOTION:" + key,
            principal=principal,
            workspace_key=response["workspace_key"],
            resource_type="INITIATIVE",
            resource_id=str(initiative_id),
            metadata={"promotion_state": request.promotion_state},
        )
        conn.commit()
    return response


@router.get("/api/v1/work/queues")
def work_queues(
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_scopes("work:read")),
) -> dict[str, Any]:
    membership = ""
    params: list[Any] = []
    if "admin:workspaces" not in principal.scopes:
        membership = (
            " AND EXISTS (SELECT 1 FROM docplane.workspace_memberships m "
            "WHERE m.workspace_id = i.workspace_id AND m.principal_id = %s)"
        )
        params.append(principal.principal_id)
    now = datetime.now(timezone.utc)
    queries = {
        "active": "i.work_state = 'ACTIVE'",
        "blocked": "i.work_state = 'BLOCKED'",
        "soaking_due": "i.work_state = 'SOAKING' AND i.soak_review_at <= %s",
        "parked_due": "i.work_state = 'PARKED' AND i.parked_indefinitely = FALSE AND i.parked_review_at <= %s",
        "ready_to_promote": "i.promotion_state = 'READY'",
    }
    result: dict[str, Any] = {}
    with get_conn() as conn:
        cur = conn.cursor()
        for name, predicate in queries.items():
            local_params: list[Any] = []
            if "%s" in predicate:
                local_params.append(now)
            local_params.extend(params)
            local_params.append(limit)
            cur.execute(
                _initiative_select()
                + f" WHERE {predicate}{membership} ORDER BY i.updated_at DESC LIMIT %s",
                local_params,
            )
            rows = cur.fetchall()
            result[name] = [_initiative_dict(row) for row in rows]
    return {
        "generated_at": now,
        "queues": result,
        "counts": {name: len(items) for name, items in result.items()},
        "interpretation": "Queues are actionable coordination views; they do not alter knowledge authority.",
    }
