"""Active-work coordination for uniform DocPlane contributors."""
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
from app.work_models import (
    ActivityCreate,
    CaptureAttach,
    CaptureCreate,
    CaptureDiscard,
    CapturePromote,
    DispositionsUpdate,
    InitiativeCreate,
    InitiativeDependencyCreate,
    InitiativeLinkCreate,
    PromotionUpdate,
    WorkspaceCreate,
    WorkTransition,
    closure_gate_errors,
    soak_gate_errors,
)

router = APIRouter(tags=["work-v1"])

_TRANSITIONS = {
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


def _key(value: str | None) -> str:
    if not value or not value.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    return value.strip()[:256]



def _workspace(row) -> dict[str, Any]:
    keys = ("workspace_id", "workspace_key", "name", "workspace_kind", "visibility", "default_verification_days", "retention_policy", "system_workspace", "created_at", "updated_at")
    value = dict(zip(keys, row))
    value["workspace_id"] = str(value["workspace_id"])
    return value


def _workspace_select() -> str:
    return "SELECT workspace_id, workspace_key, name, workspace_kind, visibility, default_verification_days, retention_policy, system_workspace, created_at, updated_at FROM docplane.workspaces"


def _initiative_select() -> str:
    return """
        SELECT i.initiative_id, i.initiative_key, i.workspace_id, w.workspace_key,
               i.title, i.objective, i.scope, i.owner_principal_id,
               i.work_state, i.priority, i.version, i.target_date, i.review_due_at,
               i.blocker_summary, i.soak_started_at, i.soak_review_at,
               i.soak_success_criteria, i.soak_failure_conditions,
               i.soak_monitoring_ref,
               i.parked_reason, i.parked_review_at, i.parked_indefinitely,
               i.promotion_state, i.promotion_change_id,
               i.model_disposition, i.model_disposition_note,
               i.observe_disposition, i.observe_disposition_note,
               i.completed_at, i.created_at, i.updated_at
          FROM work.initiatives i
          JOIN docplane.workspaces w ON w.workspace_id = i.workspace_id
    """


def _initiative(row) -> dict[str, Any]:
    keys = (
        "initiative_id", "initiative_key", "workspace_id", "workspace_key", "title",
        "objective", "scope", "owner_principal_id", "work_state", "priority", "version",
        "target_date", "review_due_at", "blocker_summary", "soak_started_at", "soak_review_at",
        "soak_success_criteria", "soak_failure_conditions", "soak_monitoring_ref",
        "parked_reason", "parked_review_at", "parked_indefinitely",
        "promotion_state", "promotion_change_id",
        "model_disposition", "model_disposition_note",
        "observe_disposition", "observe_disposition_note",
        "completed_at", "created_at", "updated_at",
    )
    value = dict(zip(keys, row))
    for key in ("initiative_id", "workspace_id", "owner_principal_id", "promotion_change_id"):
        if value.get(key) is not None:
            value[key] = str(value[key])
    value["uri"] = f"docplane://initiatives/{value['initiative_id']}"
    return value


def _load(conn, initiative_id: UUID | str, *, for_update: bool = False) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(_initiative_select() + " WHERE i.initiative_id = %s" + (" FOR UPDATE OF i" if for_update else ""), (str(initiative_id),))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "INITIATIVE_NOT_FOUND"})
    return _initiative(row)


@router.get("/api/v1/workspaces")
def list_workspaces(principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_workspace_select() + " ORDER BY system_workspace DESC, workspace_key")
        rows = cur.fetchall()
    return {"workspaces": [_workspace(row) for row in rows], "count": len(rows)}


@router.post("/api/v1/workspaces", status_code=201)
def create_workspace(
    request: WorkspaceCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO docplane.workspaces
                    (workspace_key, name, workspace_kind, visibility,
                     default_verification_days, retention_policy)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING workspace_id, workspace_key, name, workspace_kind, visibility,
                          default_verification_days, retention_policy, system_workspace,
                          created_at, updated_at
                """,
                (request.workspace_key, request.name.strip(), request.workspace_kind, request.visibility, request.default_verification_days, _json(request.retention_policy)),
            )
            response = _workspace(cur.fetchone())
            append_event(
                conn,
                event_type="WORKSPACE_CREATED",
                channel="API",
                producer_id="docplane-work",
                idempotency_key=f"WORKSPACE_CREATED:{principal.principal_id}:{key}",
                principal=principal,
                workspace_key=request.workspace_key,
                resource_type="WORKSPACE",
                resource_id=response["workspace_id"],
                metadata={"workspace_kind": request.workspace_kind},
            )
            conn.commit()
            return response
        except psycopg2.errors.UniqueViolation as exc:
            conn.rollback()
            raise HTTPException(status_code=409, detail={"code": "WORKSPACE_KEY_EXISTS"}) from exc


@router.post("/api/v1/initiatives", status_code=201)
def create_initiative(
    request: InitiativeCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT workspace_kind, workspace_key FROM docplane.workspaces WHERE workspace_id = %s", (str(request.workspace_id),))
        workspace = cur.fetchone()
        if workspace is None:
            raise HTTPException(status_code=404, detail={"code": "WORKSPACE_NOT_FOUND"})
        if workspace[0] != "WORK":
            raise HTTPException(status_code=422, detail={"code": "INITIATIVE_REQUIRES_WORK_WORKSPACE"})
        try:
            cur.execute(
                """
                INSERT INTO work.initiatives
                    (initiative_key, workspace_id, title, objective, scope,
                     owner_principal_id, idempotency_key, work_state, priority,
                     target_date, review_due_at, blocker_summary,
                     soak_started_at, soak_review_at, soak_success_criteria,
                     soak_failure_conditions, parked_reason, parked_review_at,
                     parked_indefinitely)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING initiative_id
                """,
                (
                    request.initiative_key, str(request.workspace_id), request.title.strip(),
                    request.objective.strip(), request.scope, principal.principal_id, key,
                    request.work_state, request.priority, request.target_date, request.review_due_at,
                    request.blocker_summary, request.soak_started_at, request.soak_review_at,
                    request.soak_success_criteria, request.soak_failure_conditions,
                    request.parked_reason, request.parked_review_at, request.parked_indefinitely,
                ),
            )
            initiative_id = cur.fetchone()[0]
            response = _load(conn, initiative_id)
            append_event(
                conn,
                event_type="INITIATIVE_CREATED",
                channel="API",
                producer_id="docplane-work",
                idempotency_key=f"INITIATIVE_CREATED:{principal.principal_id}:{key}",
                principal=principal,
                workspace_key=workspace[1],
                resource_type="INITIATIVE",
                resource_id=str(initiative_id),
                metadata={"work_state": request.work_state, "priority": request.priority},
            )
            conn.commit()
            return response
        except psycopg2.errors.UniqueViolation as exc:
            conn.rollback()
            raise HTTPException(status_code=409, detail={"code": "INITIATIVE_KEY_EXISTS"}) from exc


@router.get("/api/v1/initiatives")
def list_initiatives(
    workspace_id: UUID | None = None,
    work_state: str | None = None,
    owner_principal_id: UUID | None = None,
    include_closed: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    predicates: list[str] = []
    params: list[Any] = []
    if workspace_id:
        predicates.append("i.workspace_id = %s")
        params.append(str(workspace_id))
    if work_state:
        if work_state not in _TRANSITIONS:
            raise HTTPException(status_code=422, detail={"code": "WORK_STATE_INVALID"})
        predicates.append("i.work_state = %s")
        params.append(work_state)
    elif not include_closed:
        predicates.append("i.work_state NOT IN ('COMPLETE', 'ABANDONED')")
    if owner_principal_id:
        predicates.append("i.owner_principal_id = %s")
        params.append(str(owner_principal_id))
    where = " WHERE " + " AND ".join(predicates) if predicates else ""
    params.append(limit)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_initiative_select() + where + " ORDER BY i.updated_at DESC LIMIT %s", params)
        rows = cur.fetchall()
    values = [_initiative(row) for row in rows]
    return {"initiatives": values, "count": len(values)}


@router.get("/api/v1/initiatives/{initiative_id}")
def get_initiative(initiative_id: UUID, principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        value = _load(conn, initiative_id)
        cur = conn.cursor()
        cur.execute("SELECT activity_id::text, author_principal_id::text, activity_type, body, metadata, created_at FROM work.initiative_activities WHERE initiative_id = %s ORDER BY created_at, activity_id", (str(initiative_id),))
        activities = [dict(zip(("activity_id", "author_principal_id", "activity_type", "body", "metadata", "created_at"), row)) for row in cur.fetchall()]
        cur.execute("SELECT relation, resource_type, resource_id, created_by::text, created_at FROM work.initiative_links WHERE initiative_id = %s ORDER BY relation, resource_type, resource_id", (str(initiative_id),))
        links = [dict(zip(("relation", "resource_type", "resource_id", "created_by", "created_at"), row)) for row in cur.fetchall()]
        cur.execute("SELECT depends_on_initiative_id::text, dependency_kind FROM work.initiative_dependencies WHERE initiative_id = %s ORDER BY depends_on_initiative_id", (str(initiative_id),))
        dependencies = [{"initiative_id": row[0], "dependency_kind": row[1]} for row in cur.fetchall()]
    return {**value, "activities": activities, "links": links, "dependencies": dependencies}


@router.post("/api/v1/initiatives/{initiative_id}/transition")
def transition_initiative(
    initiative_id: UUID,
    request: WorkTransition,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    with get_conn() as conn:
        initiative = _load(conn, initiative_id, for_update=True)
        if initiative["version"] != request.expected_version:
            raise HTTPException(status_code=409, detail={"code": "INITIATIVE_VERSION_STALE", "current": initiative["version"]})
        if request.work_state not in _TRANSITIONS[initiative["work_state"]]:
            raise HTTPException(status_code=409, detail={"code": "WORK_TRANSITION_INVALID", "from": initiative["work_state"], "to": request.work_state})
        cur = conn.cursor()
        # Effective dispositions: values on this request win, otherwise what
        # was already recorded via the dispositions endpoint persists.
        model_disposition = request.model_disposition or initiative["model_disposition"]
        model_note = request.model_disposition_note or initiative["model_disposition_note"]
        observe_disposition = request.observe_disposition or initiative["observe_disposition"]
        observe_note = request.observe_disposition_note or initiative["observe_disposition_note"]
        soak_monitoring_ref = request.soak_monitoring_ref or initiative["soak_monitoring_ref"]
        if request.work_state == "SOAKING":
            cur.execute(
                "SELECT 1 FROM work.initiative_links WHERE initiative_id = %s AND resource_type IN ('MODEL_ENTITY', 'ARTIFACT', 'OBSERVATION') LIMIT 1",
                (str(initiative_id),),
            )
            gate = soak_gate_errors(soak_monitoring_ref, cur.fetchone() is not None)
            if gate:
                raise HTTPException(status_code=409, detail={"code": "WORK_SOAK_REQUIRES_OBSERVABILITY", "missing": gate})
        if request.work_state == "COMPLETE":
            gate = closure_gate_errors(initiative["promotion_state"], model_disposition, model_note, observe_disposition, observe_note)
            if gate:
                raise HTTPException(status_code=409, detail={"code": "WORK_CLOSURE_DISPOSITIONS_INCOMPLETE", "missing": gate})
        completed_at = datetime.now(timezone.utc) if request.work_state in {"COMPLETE", "ABANDONED"} else None
        cur.execute(
            """
            UPDATE work.initiatives
               SET work_state = %s, review_due_at = %s, blocker_summary = %s,
                   soak_started_at = %s, soak_review_at = %s,
                   soak_success_criteria = %s, soak_failure_conditions = %s,
                   soak_monitoring_ref = %s,
                   parked_reason = %s, parked_review_at = %s,
                   parked_indefinitely = %s,
                   model_disposition = %s, model_disposition_note = %s,
                   observe_disposition = %s, observe_disposition_note = %s,
                   completed_at = %s,
                   version = version + 1, updated_at = now()
             WHERE initiative_id = %s AND version = %s
            """,
            (
                request.work_state, request.review_due_at, request.blocker_summary,
                request.soak_started_at, request.soak_review_at, request.soak_success_criteria,
                request.soak_failure_conditions, soak_monitoring_ref,
                request.parked_reason, request.parked_review_at,
                request.parked_indefinitely,
                model_disposition, model_note, observe_disposition, observe_note,
                completed_at, str(initiative_id), request.expected_version,
            ),
        )
        if request.note:
            cur.execute("INSERT INTO work.initiative_activities (initiative_id, author_principal_id, idempotency_key, activity_type, body) VALUES (%s, %s, %s, 'NOTE', %s)", (str(initiative_id), principal.principal_id, f"transition:{key}", request.note.strip()))
        if request.work_state == "COMPLETE":
            # Honest deferral is allowed — and it mints a visible inbox gap.
            for domain, disposition, deferred_note in (("model", model_disposition, model_note), ("observe", observe_disposition, observe_note)):
                if disposition != "DEFERRED":
                    continue
                cur.execute(
                    """
                    INSERT INTO work.captures (body, kind, origin, author_principal_id, idempotency_key)
                    VALUES (%s, 'FINDING', %s, %s, %s)
                    ON CONFLICT (author_principal_id, idempotency_key) DO NOTHING
                    """,
                    (
                        f"Deferred {domain} disposition from completed initiative '{initiative['title']}' ({initiative['initiative_key']}): {(deferred_note or '').strip()}",
                        _json({"source": "closure-deferral", "initiative_id": str(initiative_id), "domain": domain}),
                        principal.principal_id, f"closure-deferred:{domain}:{key}",
                    ),
                )
                append_event(
                    conn,
                    event_type="WORK_CLOSURE_DEFERRED",
                    channel="API",
                    producer_id="docplane-work",
                    idempotency_key=f"WORK_CLOSURE_DEFERRED:{domain}:{principal.principal_id}:{key}",
                    principal=principal,
                    workspace_key=initiative["workspace_key"],
                    resource_type="INITIATIVE",
                    resource_id=str(initiative_id),
                    metadata={"domain": domain, "note": deferred_note},
                )
        append_event(
            conn,
            event_type="INITIATIVE_TRANSITIONED",
            channel="API",
            producer_id="docplane-work",
            idempotency_key=f"INITIATIVE_TRANSITIONED:{principal.principal_id}:{key}",
            principal=principal,
            workspace_key=initiative["workspace_key"],
            resource_type="INITIATIVE",
            resource_id=str(initiative_id),
            metadata={"from": initiative["work_state"], "to": request.work_state},
        )
        conn.commit()
        return _load(conn, initiative_id)


@router.post("/api/v1/initiatives/{initiative_id}/activities", status_code=201)
def add_activity(
    initiative_id: UUID,
    request: ActivityCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    with get_conn() as conn:
        initiative = _load(conn, initiative_id)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO work.initiative_activities
                (initiative_id, author_principal_id, idempotency_key,
                 activity_type, body, metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (initiative_id, author_principal_id, idempotency_key)
            DO UPDATE SET body = work.initiative_activities.body
            RETURNING activity_id::text, created_at
            """,
            (str(initiative_id), principal.principal_id, key, request.activity_type, request.body.strip(), _json(request.metadata)),
        )
        row = cur.fetchone()
        conn.commit()
    return {"activity_id": row[0], "initiative_id": str(initiative_id), "created_at": row[1], **request.model_dump(mode="json")}


@router.post("/api/v1/initiatives/{initiative_id}/links", status_code=201)
def add_link(
    initiative_id: UUID,
    request: InitiativeLinkCreate,
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    with get_conn() as conn:
        _load(conn, initiative_id)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO work.initiative_links
                (initiative_id, relation, resource_type, resource_id, created_by)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (str(initiative_id), request.relation, request.resource_type, request.resource_id, principal.principal_id),
        )
        conn.commit()
    return {"initiative_id": str(initiative_id), **request.model_dump(mode="json")}


@router.post("/api/v1/initiatives/{initiative_id}/dependencies", status_code=201)
def add_dependency(
    initiative_id: UUID,
    request: InitiativeDependencyCreate,
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    if request.depends_on_initiative_id == initiative_id:
        raise HTTPException(status_code=422, detail={"code": "INITIATIVE_DEPENDENCY_SELF"})
    with get_conn() as conn:
        _load(conn, initiative_id)
        _load(conn, request.depends_on_initiative_id)
        cur = conn.cursor()
        cur.execute("INSERT INTO work.initiative_dependencies (initiative_id, depends_on_initiative_id, dependency_kind) VALUES (%s, %s, %s) ON CONFLICT DO UPDATE SET dependency_kind = EXCLUDED.dependency_kind", (str(initiative_id), str(request.depends_on_initiative_id), request.dependency_kind))
        conn.commit()
    return {"initiative_id": str(initiative_id), **request.model_dump(mode="json")}


@router.put("/api/v1/initiatives/{initiative_id}/promotion")
def update_promotion(
    initiative_id: UUID,
    request: PromotionUpdate,
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    with get_conn() as conn:
        initiative = _load(conn, initiative_id, for_update=True)
        if initiative["version"] != request.expected_version:
            raise HTTPException(status_code=409, detail={"code": "INITIATIVE_VERSION_STALE", "current": initiative["version"]})
        cur = conn.cursor()
        cur.execute("UPDATE work.initiatives SET promotion_state = %s, promotion_change_id = %s, version = version + 1, updated_at = now() WHERE initiative_id = %s AND version = %s", (request.promotion_state, str(request.promotion_change_id) if request.promotion_change_id else None, str(initiative_id), request.expected_version))
        conn.commit()
        return _load(conn, initiative_id)


@router.put("/api/v1/initiatives/{initiative_id}/dispositions")
def update_dispositions(
    initiative_id: UUID,
    request: DispositionsUpdate,
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    with get_conn() as conn:
        initiative = _load(conn, initiative_id, for_update=True)
        if initiative["version"] != request.expected_version:
            raise HTTPException(status_code=409, detail={"code": "INITIATIVE_VERSION_STALE", "current": initiative["version"]})
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE work.initiatives
               SET model_disposition = %s, model_disposition_note = %s,
                   observe_disposition = %s, observe_disposition_note = %s,
                   soak_monitoring_ref = %s,
                   version = version + 1, updated_at = now()
             WHERE initiative_id = %s AND version = %s
            """,
            (
                request.model_disposition or initiative["model_disposition"],
                request.model_disposition_note or initiative["model_disposition_note"],
                request.observe_disposition or initiative["observe_disposition"],
                request.observe_disposition_note or initiative["observe_disposition_note"],
                request.soak_monitoring_ref or initiative["soak_monitoring_ref"],
                str(initiative_id), request.expected_version,
            ),
        )
        conn.commit()
        return _load(conn, initiative_id)


@router.get("/api/v1/work/queues")
def work_queues(principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT work_state, count(*) FROM work.initiatives GROUP BY work_state ORDER BY work_state")
        by_state = {row[0]: int(row[1]) for row in cur.fetchall()}
        cur.execute("SELECT priority, count(*) FROM work.initiatives WHERE work_state NOT IN ('COMPLETE', 'ABANDONED') GROUP BY priority ORDER BY priority")
        by_priority = {row[0]: int(row[1]) for row in cur.fetchall()}
        cur.execute("SELECT count(*) FROM work.initiatives WHERE work_state = 'PARKED' AND parked_review_at IS NOT NULL AND parked_review_at <= now()")
        parked_due = int(cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM work.initiatives WHERE work_state = 'SOAKING' AND soak_review_at <= now()")
        soak_due = int(cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM work.captures WHERE status = 'INBOX'")
        inbox = int(cur.fetchone()[0])
    return {"by_state": by_state, "by_priority": by_priority, "parked_review_due": parked_due, "soak_review_due": soak_due, "inbox": inbox}


_CAPTURE_COLUMNS = (
    "capture_id", "body", "kind", "origin", "status", "author_principal_id",
    "initiative_id", "activity_id", "triaged_by_principal_id", "triaged_at",
    "triage_note", "created_at",
)


def _capture_select() -> str:
    return (
        "SELECT capture_id, body, kind, origin, status, author_principal_id, "
        "initiative_id, activity_id, triaged_by_principal_id, triaged_at, "
        "triage_note, created_at FROM work.captures"
    )


def _capture(row) -> dict[str, Any]:
    value = dict(zip(_CAPTURE_COLUMNS, row))
    for key in ("capture_id", "author_principal_id", "initiative_id", "activity_id", "triaged_by_principal_id"):
        if value.get(key) is not None:
            value[key] = str(value[key])
    value["uri"] = f"docplane://work/captures/{value['capture_id']}"
    return value


def _load_capture(conn, capture_id: UUID, *, for_update: bool = False) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(_capture_select() + " WHERE capture_id = %s" + (" FOR UPDATE" if for_update else ""), (str(capture_id),))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "CAPTURE_NOT_FOUND"})
    return _capture(row)


def _require_inbox(capture: dict[str, Any]) -> None:
    if capture["status"] != "INBOX":
        raise HTTPException(status_code=409, detail={"code": "CAPTURE_ALREADY_TRIAGED", "status": capture["status"], "initiative_id": capture["initiative_id"]})


@router.post("/api/v1/work/captures", status_code=201)
def create_capture(
    request: CaptureCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO work.captures (body, kind, origin, author_principal_id, idempotency_key)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (author_principal_id, idempotency_key)
            DO UPDATE SET body = work.captures.body
            RETURNING capture_id
            """,
            (request.body.strip(), request.kind, _json(request.origin), principal.principal_id, key),
        )
        capture_id = cur.fetchone()[0]
        response = _load_capture(conn, capture_id)
        append_event(
            conn,
            event_type="CAPTURE_CREATED",
            channel="API",
            producer_id="docplane-work",
            idempotency_key=f"CAPTURE_CREATED:{principal.principal_id}:{key}",
            principal=principal,
            workspace_key="work",
            resource_type="CAPTURE",
            resource_id=str(capture_id),
            metadata={"kind": request.kind},
        )
        conn.commit()
        return response


@router.get("/api/v1/work/captures")
def list_captures(
    status: str = Query(default="INBOX"),
    limit: int = Query(default=200, ge=1, le=1000),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    statuses = {"INBOX", "PROMOTED", "ATTACHED", "DISCARDED", "all"}
    if status not in statuses:
        raise HTTPException(status_code=422, detail={"code": "CAPTURE_STATUS_INVALID", "allowed": sorted(statuses)})
    predicate = "" if status == "all" else " WHERE status = %s"
    params: list[Any] = [] if status == "all" else [status]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT count(*) FROM work.captures{predicate}", params)
        total = int(cur.fetchone()[0])
        cur.execute(_capture_select() + predicate + " ORDER BY created_at DESC LIMIT %s", [*params, limit])
        rows = cur.fetchall()
    values = [_capture(row) for row in rows]
    return {"captures": values, "count": len(values), "total": total, "truncated": len(values) < total}


@router.post("/api/v1/work/captures/{capture_id}/promote")
def promote_capture(
    capture_id: UUID,
    request: CapturePromote,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    with get_conn() as conn:
        capture = _load_capture(conn, capture_id, for_update=True)
        _require_inbox(capture)
        cur = conn.cursor()
        if request.workspace_id is not None:
            cur.execute("SELECT workspace_id, workspace_kind, workspace_key FROM docplane.workspaces WHERE workspace_id = %s", (str(request.workspace_id),))
        else:
            cur.execute("SELECT workspace_id, workspace_kind, workspace_key FROM docplane.workspaces WHERE workspace_key = 'work'")
        workspace = cur.fetchone()
        if workspace is None:
            raise HTTPException(status_code=404, detail={"code": "WORKSPACE_NOT_FOUND"})
        if workspace[1] != "WORK":
            raise HTTPException(status_code=422, detail={"code": "INITIATIVE_REQUIRES_WORK_WORKSPACE"})
        first_line = capture["body"].strip().splitlines()[0].strip()
        title = (request.title or first_line)[:300]
        initiative_key = request.initiative_key or f"cap-{capture['capture_id'][:8]}"
        objective = (request.objective or capture["body"].strip())[:5000]
        try:
            cur.execute(
                """
                INSERT INTO work.initiatives
                    (initiative_key, workspace_id, title, objective, owner_principal_id, idempotency_key, work_state, priority)
                VALUES (%s, %s, %s, %s, %s, %s, 'BACKLOG', %s)
                RETURNING initiative_id
                """,
                (initiative_key, str(workspace[0]), title, objective, principal.principal_id, f"capture-promote:{key}", request.priority),
            )
        except psycopg2.errors.UniqueViolation as exc:
            conn.rollback()
            raise HTTPException(status_code=409, detail={"code": "INITIATIVE_KEY_EXISTS"}) from exc
        initiative_id = cur.fetchone()[0]
        cur.execute("INSERT INTO work.initiative_links (initiative_id, relation, resource_type, resource_id, created_by) VALUES (%s, 'CONTEXT', 'EXTERNAL', %s, %s) ON CONFLICT DO NOTHING", (str(initiative_id), f"docplane://work/captures/{capture_id}", principal.principal_id))
        cur.execute(
            "UPDATE work.captures SET status = 'PROMOTED', initiative_id = %s, triaged_by_principal_id = %s, triaged_at = now(), triage_note = %s WHERE capture_id = %s",
            (str(initiative_id), principal.principal_id, request.note, str(capture_id)),
        )
        append_event(
            conn,
            event_type="CAPTURE_TRIAGED",
            channel="API",
            producer_id="docplane-work",
            idempotency_key=f"CAPTURE_TRIAGED:{principal.principal_id}:{key}",
            principal=principal,
            workspace_key=workspace[2],
            resource_type="CAPTURE",
            resource_id=str(capture_id),
            metadata={"disposition": "PROMOTED", "initiative_id": str(initiative_id)},
        )
        conn.commit()
        return {"capture": _load_capture(conn, capture_id), "initiative": _load(conn, initiative_id)}


@router.post("/api/v1/work/captures/{capture_id}/attach")
def attach_capture(
    capture_id: UUID,
    request: CaptureAttach,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    with get_conn() as conn:
        capture = _load_capture(conn, capture_id, for_update=True)
        _require_inbox(capture)
        initiative = _load(conn, request.initiative_id)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO work.initiative_activities
                (initiative_id, author_principal_id, idempotency_key, activity_type, body, metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (initiative_id, author_principal_id, idempotency_key)
            DO UPDATE SET body = work.initiative_activities.body
            RETURNING activity_id
            """,
            (str(request.initiative_id), principal.principal_id, f"capture-attach:{key}", request.activity_type, capture["body"], _json({"capture_id": str(capture_id), "capture_kind": capture["kind"], "capture_origin": capture["origin"]})),
        )
        activity_id = cur.fetchone()[0]
        cur.execute(
            "UPDATE work.captures SET status = 'ATTACHED', initiative_id = %s, activity_id = %s, triaged_by_principal_id = %s, triaged_at = now(), triage_note = %s WHERE capture_id = %s",
            (str(request.initiative_id), str(activity_id), principal.principal_id, request.note, str(capture_id)),
        )
        append_event(
            conn,
            event_type="CAPTURE_TRIAGED",
            channel="API",
            producer_id="docplane-work",
            idempotency_key=f"CAPTURE_TRIAGED:{principal.principal_id}:{key}",
            principal=principal,
            workspace_key=initiative["workspace_key"],
            resource_type="CAPTURE",
            resource_id=str(capture_id),
            metadata={"disposition": "ATTACHED", "initiative_id": str(request.initiative_id)},
        )
        conn.commit()
        return {"capture": _load_capture(conn, capture_id), "activity_id": str(activity_id)}


@router.post("/api/v1/work/captures/{capture_id}/discard")
def discard_capture(
    capture_id: UUID,
    request: CaptureDiscard,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    with get_conn() as conn:
        capture = _load_capture(conn, capture_id, for_update=True)
        _require_inbox(capture)
        cur = conn.cursor()
        cur.execute(
            "UPDATE work.captures SET status = 'DISCARDED', triaged_by_principal_id = %s, triaged_at = now(), triage_note = %s WHERE capture_id = %s",
            (principal.principal_id, request.note, str(capture_id)),
        )
        append_event(
            conn,
            event_type="CAPTURE_TRIAGED",
            channel="API",
            producer_id="docplane-work",
            idempotency_key=f"CAPTURE_TRIAGED:{principal.principal_id}:{key}",
            principal=principal,
            workspace_key="work",
            resource_type="CAPTURE",
            resource_id=str(capture_id),
            metadata={"disposition": "DISCARDED"},
        )
        conn.commit()
        return {"capture": _load_capture(conn, capture_id)}
