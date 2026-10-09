"""Authorization helpers for optional artifact-scoped automation principals."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.agent_auth import Principal


def require_artifact_scope(
    principal: Principal,
    artifact_key: str,
    operation: str,
    *,
    observation_kind: str | None = None,
    page_paths: list[str] | None = None,
) -> None:
    if principal.artifact_scopes is None:
        return
    scope = next(
        (
            item for item in principal.artifact_scopes
            if item.artifact_key == artifact_key and item.operation == operation
        ),
        None,
    )
    if scope is None:
        raise HTTPException(status_code=403, detail={"code": "PRINCIPAL_ARTIFACT_SCOPE_DENIED"})
    if operation == "OBSERVE" and scope.observation_kind != observation_kind:
        raise HTTPException(status_code=403, detail={"code": "PRINCIPAL_OBSERVATION_SCOPE_DENIED"})
    if operation == "GENERATE" and page_paths:
        prefix = scope.page_path_prefix or ""
        if not prefix.endswith("/") or any(not path.startswith(prefix) for path in page_paths):
            raise HTTPException(status_code=403, detail={"code": "PRINCIPAL_PAGE_PATH_SCOPE_DENIED"})


def require_source_entity_scope(
    conn, principal: Principal, artifact_key: str, source_entity_id: str,
) -> None:
    if principal.artifact_scopes is None:
        return
    scope = next(
        (
            item for item in principal.artifact_scopes
            if item.artifact_key == artifact_key and item.operation == "GENERATE"
        ),
        None,
    )
    if scope is None:
        raise HTTPException(status_code=403, detail={"code": "PRINCIPAL_ARTIFACT_SCOPE_DENIED"})
    cur = conn.cursor()
    cur.execute(
        "SELECT entity_kind, entity_key FROM model.entities WHERE entity_id = %s",
        (source_entity_id,),
    )
    row = cur.fetchone()
    if row is None or row[0] != "DATABASE" or row[1] != scope.source_entity_key:
        raise HTTPException(status_code=403, detail={"code": "PRINCIPAL_SOURCE_ENTITY_SCOPE_DENIED"})


def require_artifact_id_scope(
    conn, principal: Principal, artifact_id: str, operation: str,
    *, observation_kind: str | None = None,
) -> str:
    cur = conn.cursor()
    cur.execute(
        "SELECT artifact_key, status FROM model.generated_artifacts WHERE artifact_id = %s",
        (artifact_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "MODEL_ARTIFACT_NOT_FOUND"})
    if principal.artifact_scopes is not None and row[1] != "DECLARED":
        raise HTTPException(status_code=403, detail={"code": "PRINCIPAL_ARTIFACT_SCOPE_DENIED"})
    require_artifact_scope(
        principal, row[0], operation, observation_kind=observation_kind
    )
    return row[0]


def require_change_scope(conn, principal: Principal, change_id: str) -> str | None:
    if principal.artifact_scopes is None:
        return None
    cur = conn.cursor()
    cur.execute(
        "SELECT author_principal_id::text, generated_ownership_plan FROM docs.changes WHERE change_id = %s",
        (change_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "CHANGE_NOT_FOUND"})
    author_id, plan = row
    if author_id != principal.principal_id or not isinstance(plan, dict):
        raise HTTPException(status_code=403, detail={"code": "PRINCIPAL_CHANGE_SCOPE_DENIED"})
    return require_ownership_plan_scope(conn, principal, plan)


def require_ownership_plan_scope(conn, principal: Principal, plan: dict[str, Any]) -> str | None:
    if principal.artifact_scopes is None:
        return None
    artifact_id = plan.get("artifact_id") or plan.get("predecessor_id")
    if not artifact_id:
        raise HTTPException(status_code=403, detail={"code": "PRINCIPAL_CHANGE_SCOPE_DENIED"})
    artifact_key = require_artifact_id_scope(conn, principal, artifact_id, "GENERATE")
    paths = plan.get("target_page_paths") or []
    if plan.get("successor"):
        paths = [*paths, *(plan["successor"].get("target_page_paths") or [])]
    require_artifact_scope(principal, artifact_key, "GENERATE", page_paths=paths)
    if plan.get("successor"):
        successor = plan["successor"]
        require_artifact_scope(
            principal, successor.get("artifact_key", ""), "GENERATE",
            page_paths=successor.get("target_page_paths") or [],
        )
    return artifact_key


def require_page_operation_scope(
    conn, principal: Principal, change_id: str, operation_type: str,
    page_resource_id: str | None, payload: dict[str, Any],
) -> None:
    if principal.artifact_scopes is None:
        return
    if operation_type not in {"CREATE_PAGE", "REPLACE_DOCUMENT", "ARCHIVE_PAGE", "RESTORE_PAGE"}:
        raise HTTPException(status_code=403, detail={"code": "PRINCIPAL_CHANGE_OPERATION_DENIED"})
    artifact_key = require_change_scope(conn, principal, change_id)
    scope = next(
        item for item in principal.artifact_scopes
        if item.artifact_key == artifact_key and item.operation == "GENERATE"
    )
    path = payload.get("path")
    if page_resource_id is not None:
        cur = conn.cursor()
        cur.execute("SELECT path FROM docs.pages WHERE resource_id = %s", (page_resource_id,))
        row = cur.fetchone()
        existing_path = row[0] if row else None
        if path is not None and path != existing_path:
            raise HTTPException(status_code=403, detail={"code": "PRINCIPAL_PAGE_PATH_SCOPE_DENIED"})
        path = existing_path
    if not path or not scope.page_path_prefix or not path.startswith(scope.page_path_prefix):
        raise HTTPException(status_code=403, detail={"code": "PRINCIPAL_PAGE_PATH_SCOPE_DENIED"})


def require_observation_scopes(conn, principal: Principal, observations: list[Any]) -> None:
    if principal.artifact_scopes is None:
        return
    for item in observations:
        if item.subject_artifact_id is not None:
            require_artifact_id_scope(
                conn, principal, str(item.subject_artifact_id), "OBSERVE",
                observation_kind=item.observation_kind,
            )
            cur = conn.cursor()
            cur.execute(
                "SELECT artifact_key FROM model.generated_artifacts WHERE artifact_id = %s",
                (str(item.subject_artifact_id),),
            )
            artifact_key = cur.fetchone()[0]
        elif item.subject_entity_id is not None:
            cur = conn.cursor()
            allowed_keys = [
                scope.artifact_key for scope in principal.artifact_scopes
                if scope.operation == "OBSERVE" and scope.observation_kind == item.observation_kind
            ]
            cur.execute(
                """
                SELECT artifact_key FROM model.generated_artifacts
                 WHERE source_entity_id = %s AND artifact_key = ANY(%s::text[])
                   AND status = 'DECLARED'
                """,
                (str(item.subject_entity_id), allowed_keys),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=403, detail={"code": "PRINCIPAL_OBSERVATION_SCOPE_DENIED"})
            artifact_key = row[0]
        else:
            raise HTTPException(status_code=403, detail={"code": "PRINCIPAL_OBSERVATION_SCOPE_DENIED"})
        require_artifact_scope(
            principal, artifact_key, "OBSERVE", observation_kind=item.observation_kind
        )
