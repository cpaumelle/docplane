"""Human-authoring facade over the same DocPlane APIs used by agents.

This router owns no document state. It forwards named-human requests to bounded read and
change-proposal endpoints and deliberately exposes no direct merge, filesystem or SQL path.
"""
from __future__ import annotations

import difflib
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query

from .control_plane import ControlPlaneClient, ControlPlaneError

router = APIRouter(prefix="/api/control-plane/authoring", tags=["authoring"])
client = ControlPlaneClient()


def _auth(value: str | None) -> str:
    if not value:
        raise HTTPException(
            status_code=401,
            detail={"code": "DASHBOARD_AUTH_REQUIRED", "message": "A named DocPlane human principal is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return value


def _raise(exc: ControlPlaneError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "upstream": exc.detail},
    ) from exc


@router.get("/capabilities")
def capabilities() -> dict[str, Any]:
    return {
        "contract_version": "human-authoring-v1",
        "source_authority": "markdown",
        "generated_release_editable": False,
        "direct_database_access": False,
        "direct_merge": False,
        "workflow": ["SEARCH", "READ_EDIT_CONTEXT", "EDIT", "DIFF", "PROPOSE", "VALIDATE", "SUBMIT"],
        "future": {"codemirror": True, "visual_editor": "round-trip proof required", "certified_preview": True},
    }


@router.get("/search")
def search(
    q: str = Query(min_length=2, max_length=500),
    include_archived: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    authorization: str | None = Header(default=None),
) -> Any:
    try:
        return client.get(
            "/api/v1/search",
            authorization=_auth(authorization),
            params={"q": q, "include_archived": include_archived, "limit": limit},
        )
    except ControlPlaneError as exc:
        _raise(exc)


@router.get("/pages/{resource_id}")
def page_edit_context(
    resource_id: UUID,
    authorization: str | None = Header(default=None),
) -> Any:
    try:
        return client.get(
            f"/api/v1/pages/{resource_id}",
            authorization=_auth(authorization),
            params={"view": "edit_context"},
        )
    except ControlPlaneError as exc:
        _raise(exc)


@router.post("/diff")
def diff(body: dict[str, Any]) -> dict[str, Any]:
    before = str(body.get("before", ""))
    after = str(body.get("after", ""))
    path = str(body.get("path", "page.md"))
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    return {
        "contract_version": "human-authoring-diff-v1",
        "changed": before != after,
        "line_count": len(lines),
        "diff": "\n".join(lines),
    }


@router.post("/changes", status_code=201)
def create_change(
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    try:
        return client.post(
            "/api/v1/changes",
            authorization=_auth(authorization),
            idempotency_key=idempotency_key,
            json_body=body,
        )
    except ControlPlaneError as exc:
        _raise(exc)


@router.post("/changes/{change_id}/operations", status_code=201)
def add_operation(
    change_id: UUID,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    try:
        return client.post(
            f"/api/v1/changes/{change_id}/operations",
            authorization=_auth(authorization),
            idempotency_key=idempotency_key,
            json_body=body,
        )
    except ControlPlaneError as exc:
        _raise(exc)


def _change_action(
    change_id: UUID,
    action: str,
    body: dict[str, Any],
    authorization: str | None,
    idempotency_key: str | None,
) -> Any:
    try:
        return client.post(
            f"/api/v1/changes/{change_id}/{action}",
            authorization=_auth(authorization),
            idempotency_key=idempotency_key,
            json_body=body,
        )
    except ControlPlaneError as exc:
        _raise(exc)


@router.post("/changes/{change_id}/validate")
def validate_change(
    change_id: UUID,
    body: dict[str, Any] | None = None,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    return _change_action(change_id, "validate", body or {}, authorization, idempotency_key)


@router.post("/changes/{change_id}/submit")
def submit_change(
    change_id: UUID,
    body: dict[str, Any] | None = None,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    return _change_action(change_id, "submit", body or {}, authorization, idempotency_key)
