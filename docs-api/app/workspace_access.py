"""Workspace membership and page-access helpers.

Scopes answer what a principal may do in principle. Workspace roles answer where it may do it. Both are
required; a broad token scope never silently grants access to every workspace.
"""
from __future__ import annotations

from typing import Iterable

from fastapi import HTTPException

from app.agent_auth import Principal


ROLE_LEVEL = {
    "READER": 10,
    "PROPOSER": 20,
    "EDITOR": 30,
    "REVIEWER": 40,
    "MERGER": 50,
    "OWNER": 60,
    "ADMIN": 100,
}


def workspace_role(conn, principal: Principal, workspace_id: str) -> str | None:
    if "admin:workspaces" in principal.scopes:
        return "ADMIN"
    cur = conn.cursor()
    cur.execute(
        "SELECT role FROM docplane.workspace_memberships WHERE workspace_id = %s AND principal_id = %s",
        (workspace_id, principal.principal_id),
    )
    row = cur.fetchone()
    return row[0] if row else None


def require_workspace_role(
    conn,
    principal: Principal,
    workspace_id: str,
    allowed: Iterable[str],
) -> str:
    allowed_set = set(allowed)
    role = workspace_role(conn, principal, workspace_id)
    if role is None or role not in allowed_set:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "WORKSPACE_ROLE_REQUIRED",
                "workspace_id": workspace_id,
                "current_role": role,
                "allowed_roles": sorted(allowed_set),
            },
        )
    return role


def require_minimum_workspace_role(
    conn,
    principal: Principal,
    workspace_id: str,
    minimum_role: str,
) -> str:
    if minimum_role not in ROLE_LEVEL:
        raise RuntimeError(f"unknown minimum workspace role {minimum_role}")
    role = workspace_role(conn, principal, workspace_id)
    if role is None or ROLE_LEVEL.get(role, -1) < ROLE_LEVEL[minimum_role]:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "WORKSPACE_ROLE_REQUIRED",
                "workspace_id": workspace_id,
                "current_role": role,
                "minimum_role": minimum_role,
            },
        )
    return role


def page_workspace(conn, page_resource_id: str, *, for_update: bool = False) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.resource_id::text, p.path, p.title, p.revision, p.version,
               p.workspace_id::text, w.workspace_key, w.workspace_kind, w.visibility,
               p.publication_state, p.knowledge_class, p.verification_state,
               p.owner_principal_id::text, p.review_due_at, p.criticality,
               p.metadata_review_required, p.metadata_version
          FROM docs.pages p
          JOIN docplane.workspaces w ON w.workspace_id = p.workspace_id
         WHERE p.resource_id = %s
        """
        + (" FOR UPDATE OF p" if for_update else ""),
        (page_resource_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PAGE_NOT_FOUND", "resource_id": page_resource_id},
        )
    keys = (
        "resource_id",
        "path",
        "title",
        "revision",
        "version",
        "workspace_id",
        "workspace_key",
        "workspace_kind",
        "visibility",
        "publication_state",
        "knowledge_class",
        "verification_state",
        "owner_principal_id",
        "review_due_at",
        "criticality",
        "metadata_review_required",
        "metadata_version",
    )
    return dict(zip(keys, row))


def require_page_access(
    conn,
    principal: Principal,
    page_resource_id: str,
    *,
    minimum_role: str = "READER",
    for_update: bool = False,
) -> dict:
    page = page_workspace(conn, page_resource_id, for_update=for_update)
    if minimum_role == "READER" and page["visibility"] == "PUBLIC":
        return page
    require_minimum_workspace_role(conn, principal, page["workspace_id"], minimum_role)
    return page
