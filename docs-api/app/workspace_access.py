"""Workspace and page lookup helpers.

Workspaces are classification and lifecycle boundaries. Every authenticated
contributor may read and author in every workspace.
"""
from __future__ import annotations

from fastapi import HTTPException

from app.agent_auth import Principal


def workspace_role(conn, principal: Principal, workspace_id: str) -> str | None:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM docplane.workspaces WHERE workspace_id = %s", (workspace_id,))
    return "CONTRIBUTOR" if cur.fetchone() else None


def require_workspace_role(conn, principal: Principal, workspace_id: str, allowed) -> str:
    role = workspace_role(conn, principal, workspace_id)
    if role is None:
        raise HTTPException(status_code=404, detail={"code": "WORKSPACE_NOT_FOUND"})
    return role


def require_minimum_workspace_role(
    conn, principal: Principal, workspace_id: str, minimum_role: str
) -> str:
    return require_workspace_role(conn, principal, workspace_id, {"CONTRIBUTOR"})


def page_workspace(conn, page_resource_id: str, *, for_update: bool = False) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.resource_id::text, p.path, p.title, p.revision, p.version,
               p.workspace_id::text, w.workspace_key, w.workspace_kind, w.visibility,
               p.publication_state, p.knowledge_class, p.verification_state,
               p.owner_principal_id::text, p.review_due_at, p.criticality,
               p.metadata_review_required, p.metadata_version, p.provenance
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
        "resource_id", "path", "title", "revision", "version", "workspace_id",
        "workspace_key", "workspace_kind", "visibility", "publication_state",
        "knowledge_class", "verification_state", "owner_principal_id",
        "review_due_at", "criticality", "metadata_review_required", "metadata_version",
        "provenance",
    )
    return dict(zip(keys, row))


def require_page_access(
    conn,
    principal: Principal,
    page_resource_id: str,
    *,
    minimum_role: str = "CONTRIBUTOR",
    for_update: bool = False,
) -> dict:
    return page_workspace(conn, page_resource_id, for_update=for_update)
