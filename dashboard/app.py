"""DocPlane human control surface; all authority remains in the API."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .control_plane import ControlPlaneClient, ControlPlaneError

app = FastAPI(title="DocPlane Dashboard", version="1.0.0")
client = ControlPlaneClient()
_STATIC = Path(__file__).resolve().parent / "static"
app.mount("/assets", StaticFiles(directory=_STATIC), name="assets")


def _auth(value: str | None) -> str:
    if not value:
        raise HTTPException(status_code=401, detail={"code": "DASHBOARD_AUTH_REQUIRED"})
    return value


def _raise(exc: ControlPlaneError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "upstream": exc.detail}) from exc


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "docplane-dashboard"}


@app.get("/api/control-plane/discovery")
def discovery() -> Any:
    try:
        return client.discovery()
    except ControlPlaneError as exc:
        _raise(exc)


@app.get("/api/control-plane/capabilities")
def capabilities(authorization: str | None = Header(default=None)) -> Any:
    try:
        return client.get("/api/v1/capabilities", authorization=_auth(authorization))
    except ControlPlaneError as exc:
        _raise(exc)


@app.get("/api/control-plane/overview")
def overview(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    auth = _auth(authorization)
    endpoints = {
        "structure": "/api/v1/dashboard/structure",
        "changes": "/api/v1/changes?limit=20",
        "certification": "/api/v1/certification/status",
        "work": "/api/v1/work/queues",
        "maintenance": "/api/v1/maintenance/pages?queue=all&limit=20",
        "deployments": "/api/v1/deployments/attempts?limit=10",
    }
    result: dict[str, Any] = {"modules": {}}
    for name, path in endpoints.items():
        try:
            result["modules"][name] = {"available": True, "data": client.get(path, authorization=auth)}
        except ControlPlaneError as exc:
            result["modules"][name] = {"available": False, "error": exc.code}
    return result


@app.get("/api/control-plane/structure")
def structure(authorization: str | None = Header(default=None)) -> Any:
    try:
        return client.get("/api/v1/dashboard/structure", authorization=_auth(authorization))
    except ControlPlaneError as exc:
        _raise(exc)


@app.get("/api/control-plane/changes")
def changes(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    authorization: str | None = Header(default=None),
) -> Any:
    params = {"limit": limit}
    if status:
        params["status"] = status
    try:
        return client.get("/api/v1/changes", authorization=_auth(authorization), params=params)
    except ControlPlaneError as exc:
        _raise(exc)


@app.get("/api/control-plane/certification")
def certification(authorization: str | None = Header(default=None)) -> Any:
    try:
        return client.get("/api/v1/certification/status", authorization=_auth(authorization))
    except ControlPlaneError as exc:
        _raise(exc)


@app.post("/api/control-plane/publication/retry")
def retry_publication(
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    try:
        return client.post(
            "/api/v1/publication/retry",
            authorization=_auth(authorization),
            idempotency_key=idempotency_key,
            json_body={},
        )
    except ControlPlaneError as exc:
        _raise(exc)


@app.get("/api/control-plane/reorganisation/tree")
def reorganisation_tree(authorization: str | None = Header(default=None)) -> Any:
    try:
        return client.get("/api/v1/reorganisation/tree", authorization=_auth(authorization))
    except ControlPlaneError as exc:
        _raise(exc)


@app.get("/api/control-plane/reorganisation/plans")
def reorganisation_plans(
    status: str = "open",
    authorization: str | None = Header(default=None),
) -> Any:
    try:
        return client.get(
            "/api/v1/reorganisation/plans",
            authorization=_auth(authorization),
            params={"status": status},
        )
    except ControlPlaneError as exc:
        _raise(exc)


@app.post("/api/control-plane/reorganisation/plans", status_code=201)
def create_reorganisation_plan(
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    try:
        return client.post(
            "/api/v1/reorganisation/plans",
            authorization=_auth(authorization),
            idempotency_key=idempotency_key,
            json_body=body,
        )
    except ControlPlaneError as exc:
        _raise(exc)


@app.post("/api/control-plane/reorganisation/plans/{plan_id}/operations", status_code=201)
def add_reorganisation_operation(
    plan_id: UUID,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    try:
        return client.post(
            f"/api/v1/reorganisation/plans/{plan_id}/operations",
            authorization=_auth(authorization),
            idempotency_key=idempotency_key,
            json_body=body,
        )
    except ControlPlaneError as exc:
        _raise(exc)


@app.post("/api/control-plane/reorganisation/plans/{plan_id}/{action}")
def reorganisation_action(
    plan_id: UUID,
    action: str,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    if action not in {"analyze", "validate", "publish"}:
        raise HTTPException(status_code=404, detail={"code": "ACTION_NOT_FOUND"})
    try:
        return client.post(
            f"/api/v1/reorganisation/plans/{plan_id}/{action}",
            authorization=_auth(authorization),
            idempotency_key=idempotency_key,
            json_body={},
        )
    except ControlPlaneError as exc:
        _raise(exc)
