from __future__ import annotations

import pathlib
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .control_plane import ControlPlaneClient, ControlPlaneError


STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
client = ControlPlaneClient()

app = FastAPI(
    title="DocPlane Control Plane",
    version="0.2.0",
    description=(
        "First-class human control surface for corpus management, reorganisation, "
        "changes, certification, active work, trust, usage, schema catalog and operations."
    ),
)


def _raise_control_plane(exc: ControlPlaneError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "upstream": exc.detail},
    ) from exc


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


def _module(
    name: str,
    path: str,
    *,
    authorization: str | None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return {
            "name": name,
            "available": True,
            "data": client.get(path, authorization=authorization, params=params),
        }
    except ControlPlaneError as exc:
        return {
            "name": name,
            "available": False,
            "status_code": exc.status_code,
            "error": exc.code,
        }


@app.get("/healthz")
def health() -> dict[str, Any]:
    try:
        discovery = client.discovery()
    except ControlPlaneError as exc:
        raise HTTPException(
            status_code=503,
            detail={"status": "unavailable", "code": exc.code},
        ) from exc
    return {
        "status": "ok",
        "control_plane": discovery.get("product", "DocPlane"),
        "api_contract": discovery.get("api_contract"),
        "dashboard_authority": "human-control-surface",
        "database_access": False,
    }


@app.get("/api/control-plane/discovery")
def discovery() -> dict[str, Any]:
    try:
        value = client.discovery()
    except ControlPlaneError as exc:
        _raise_control_plane(exc)
    return {
        **value,
        "dashboard": {
            "role": "first-class human control surface",
            "authority": "versioned DocPlane APIs",
            "direct_database_access": False,
            "normal_shell_access": False,
            "domains": [
                "corpus",
                "reorganisation",
                "changes",
                "certification",
                "work",
                "trust",
                "usage",
                "schema_catalog",
                "operations",
            ],
        },
    }


@app.get("/api/control-plane/overview")
def overview(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    auth = _auth(authorization)
    modules = [
        _module("corpus", "/api/v1/dashboard/structure", authorization=auth),
        _module(
            "reorganisation",
            "/api/v1/reorganisation/plans",
            authorization=auth,
            params={"status": "open", "limit": 20},
        ),
        _module(
            "changes",
            "/api/v1/changes",
            authorization=auth,
            params={"status": "SUBMITTED", "limit": 20},
        ),
        _module("certification", "/api/v1/certification/status", authorization=auth),
        _module("work", "/api/v1/work/queues", authorization=auth),
        _module(
            "trust",
            "/api/v1/maintenance/pages",
            authorization=auth,
            params={"queue": "all", "limit": 20},
        ),
        _module("usage", "/api/v1/dashboard/usage/overview", authorization=auth),
        _module("schema_catalog", "/api/v1/catalog/sources", authorization=auth),
        _module("operations", "/api/v1/operations/status", authorization=auth),
    ]
    return {
        "contract_version": "dashboard-control-plane-v1",
        "modules": modules,
        "available": [item["name"] for item in modules if item["available"]],
        "pending": [item["name"] for item in modules if not item["available"]],
    }


@app.get("/api/structure", deprecated=True)
def legacy_structure_proxy(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Compatibility proxy; the dashboard no longer reads PostgreSQL directly."""
    try:
        return client.get(
            "/api/v1/dashboard/structure",
            authorization=_auth(authorization),
        )
    except ControlPlaneError as exc:
        _raise_control_plane(exc)


@app.get("/api/control-plane/reorganisation")
def reorganisation_workspace(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    auth = _auth(authorization)
    modules = {
        "tree": _module(
            "tree",
            "/api/v1/reorganisation/tree",
            authorization=auth,
        ),
        "plans": _module(
            "plans",
            "/api/v1/reorganisation/plans",
            authorization=auth,
            params={"status": "open", "limit": 100},
        ),
        "certification": _module(
            "certification",
            "/api/v1/certification/status",
            authorization=auth,
        ),
        "deployment_attempts": _module(
            "deployment_attempts",
            "/api/v1/deployments/attempts",
            authorization=auth,
            params={"limit": 20},
        ),
    }
    return {
        "contract_version": "reorganisation-control-v1",
        "workflow": [
            "PLAN",
            "ANALYZE_IMPACT",
            "VALIDATE",
            "SUBMIT",
            "APPROVE",
            "EXECUTE",
            "CERTIFY",
            "STABILIZE",
            "CLOSE_OR_COMPENSATE",
        ],
        "modules": modules,
        "execution_authority": "WP8 guarded APIs only",
        "direct_filesystem_mutation": False,
    }


@app.get("/api/control-plane/reorganisation/tree")
def reorganisation_tree(authorization: str | None = Header(default=None)) -> Any:
    try:
        return client.get(
            "/api/v1/reorganisation/tree",
            authorization=_auth(authorization),
        )
    except ControlPlaneError as exc:
        _raise_control_plane(exc)


@app.get("/api/control-plane/reorganisation/plans/{plan_id}")
def reorganisation_plan(
    plan_id: UUID,
    authorization: str | None = Header(default=None),
) -> Any:
    try:
        return client.get(
            f"/api/v1/reorganisation/plans/{plan_id}",
            authorization=_auth(authorization),
        )
    except ControlPlaneError as exc:
        _raise_control_plane(exc)


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
        _raise_control_plane(exc)


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
        _raise_control_plane(exc)


def _forward_plan_action(
    plan_id: UUID,
    action: str,
    body: dict[str, Any],
    authorization: str | None,
    idempotency_key: str | None,
) -> Any:
    try:
        return client.post(
            f"/api/v1/reorganisation/plans/{plan_id}/{action}",
            authorization=_auth(authorization),
            idempotency_key=idempotency_key,
            json_body=body,
        )
    except ControlPlaneError as exc:
        _raise_control_plane(exc)


@app.post("/api/control-plane/reorganisation/plans/{plan_id}/analyze")
def analyze_reorganisation_plan(
    plan_id: UUID,
    body: dict[str, Any] | None = None,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    return _forward_plan_action(
        plan_id, "analyze", body or {}, authorization, idempotency_key
    )


@app.post("/api/control-plane/reorganisation/plans/{plan_id}/validate")
def validate_reorganisation_plan(
    plan_id: UUID,
    body: dict[str, Any] | None = None,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    return _forward_plan_action(
        plan_id, "validate", body or {}, authorization, idempotency_key
    )


@app.post("/api/control-plane/reorganisation/plans/{plan_id}/submit")
def submit_reorganisation_plan(
    plan_id: UUID,
    body: dict[str, Any] | None = None,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    return _forward_plan_action(
        plan_id, "submit", body or {}, authorization, idempotency_key
    )


@app.post("/api/control-plane/reorganisation/plans/{plan_id}/execute")
def execute_reorganisation_plan(
    plan_id: UUID,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    return _forward_plan_action(
        plan_id, "execute", body, authorization, idempotency_key
    )


@app.post("/api/control-plane/reorganisation/plans/{plan_id}/compensate")
def compensate_reorganisation_plan(
    plan_id: UUID,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    return _forward_plan_action(
        plan_id, "compensate", body, authorization, idempotency_key
    )


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")
