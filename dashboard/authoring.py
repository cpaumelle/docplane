"""Browser-facing facade for first-class human authoring.

The dashboard never interprets an edit as authority. It forwards the signed-in human principal to the
same page, preview and change-proposal endpoints used by agents and SDKs.
"""
from __future__ import annotations

import importlib
import pathlib
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse

from .control_plane import ControlPlaneError


STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
router = APIRouter(tags=["dashboard-authoring"])


def _dashboard_app():
    # Resolve dynamically so dashboard tests can replace dashboard.app.client.
    return importlib.import_module("dashboard.app")


def _auth(value: str | None) -> str:
    if not value:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "DASHBOARD_AUTH_REQUIRED",
                "message": "A named DocPlane human principal is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    return value


def _raise(exc: ControlPlaneError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "upstream": exc.detail},
    ) from exc


@router.get("/authoring", include_in_schema=False)
def authoring_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "authoring.html")


@router.get("/api/control-plane/authoring/search")
def search_pages(
    q: str = Query(min_length=2, max_length=500),
    limit: int = Query(default=20, ge=1, le=100),
    authorization: str | None = Header(default=None),
) -> Any:
    try:
        return _dashboard_app().client.get(
            "/api/v1/search",
            authorization=_auth(authorization),
            params={"q": q, "limit": limit},
        )
    except ControlPlaneError as exc:
        _raise(exc)


@router.get("/api/control-plane/authoring/pages/{resource_id}")
def read_page(
    resource_id: UUID,
    authorization: str | None = Header(default=None),
) -> Any:
    try:
        return _dashboard_app().client.get(
            f"/api/v1/pages/{resource_id}",
            authorization=_auth(authorization),
            params={"view": "edit_context"},
        )
    except ControlPlaneError as exc:
        _raise(exc)


@router.post("/api/control-plane/authoring/preview")
def preview(
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> Any:
    try:
        return _dashboard_app().client.post(
            "/api/v1/authoring/preview",
            authorization=_auth(authorization),
            json_body=body,
        )
    except ControlPlaneError as exc:
        _raise(exc)


@router.post("/api/control-plane/authoring/changes", status_code=201)
def create_change(
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    try:
        return _dashboard_app().client.post(
            "/api/v1/changes",
            authorization=_auth(authorization),
            idempotency_key=idempotency_key,
            json_body=body,
        )
    except ControlPlaneError as exc:
        _raise(exc)


@router.post("/api/control-plane/authoring/changes/{change_id}/operations", status_code=201)
def add_operation(
    change_id: UUID,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    try:
        return _dashboard_app().client.post(
            f"/api/v1/changes/{change_id}/operations",
            authorization=_auth(authorization),
            idempotency_key=idempotency_key,
            json_body=body,
        )
    except ControlPlaneError as exc:
        _raise(exc)


@router.post("/api/control-plane/authoring/changes/{change_id}/validate")
def validate_change(
    change_id: UUID,
    authorization: str | None = Header(default=None),
) -> Any:
    try:
        return _dashboard_app().client.post(
            f"/api/v1/changes/{change_id}/validate",
            authorization=_auth(authorization),
            json_body=None,
        )
    except ControlPlaneError as exc:
        _raise(exc)


@router.post("/api/control-plane/authoring/changes/{change_id}/submit")
def submit_change(
    change_id: UUID,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> Any:
    try:
        return _dashboard_app().client.post(
            f"/api/v1/changes/{change_id}/submit",
            authorization=_auth(authorization),
            json_body=body,
        )
    except ControlPlaneError as exc:
        _raise(exc)
