"""Sprint 1 contract: the GTD capture inbox is additive and zero-decision.

These tests bind the capture/triage surface without a database: route
inventory, OpenAPI additivity, idempotency requirements, model contracts and
migration-file discipline.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from pydantic import ValidationError

from app.work_api import router as work_router
from app.work_models import CaptureAttach, CaptureCreate, CaptureDiscard, CapturePromote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "docs-api"))

import migrate  # noqa: E402


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(work_router)
    return app


def test_capture_routes_exist_and_prior_work_routes_survive():
    paths = {route.path for route in _app().routes}
    # New Sprint 1 surface.
    assert "/api/v1/work/captures" in paths
    assert "/api/v1/work/captures/{capture_id}/promote" in paths
    assert "/api/v1/work/captures/{capture_id}/attach" in paths
    assert "/api/v1/work/captures/{capture_id}/discard" in paths
    # Prior work-domain surface is untouched (additive-only compatibility gate).
    for prior in (
        "/api/v1/workspaces",
        "/api/v1/initiatives",
        "/api/v1/initiatives/{initiative_id}",
        "/api/v1/initiatives/{initiative_id}/transition",
        "/api/v1/initiatives/{initiative_id}/activities",
        "/api/v1/initiatives/{initiative_id}/links",
        "/api/v1/initiatives/{initiative_id}/dependencies",
        "/api/v1/initiatives/{initiative_id}/promotion",
        "/api/v1/work/queues",
    ):
        assert prior in paths, prior


def test_capture_mutations_declare_required_idempotency_header():
    schema = _app().openapi()
    for path, verb in (
        ("/api/v1/work/captures", "post"),
        ("/api/v1/work/captures/{capture_id}/promote", "post"),
        ("/api/v1/work/captures/{capture_id}/attach", "post"),
        ("/api/v1/work/captures/{capture_id}/discard", "post"),
    ):
        parameters = schema["paths"][path][verb].get("parameters", [])
        header = next(item for item in parameters if item["name"] == "Idempotency-Key")
        assert header["in"] == "header"


def test_capture_create_is_zero_decision():
    capture = CaptureCreate(body="tbls importer should redact enum comments too")
    assert capture.kind == "IDEA"
    assert capture.origin == {}


def test_capture_create_contracts():
    try:
        CaptureCreate(body="")
        raise AssertionError("empty body must be rejected")
    except ValidationError:
        pass
    try:
        CaptureCreate(body="x", kind="TODO")
        raise AssertionError("unknown kind must be rejected")
    except ValidationError:
        pass
    try:
        CaptureCreate(body="x", origin={"blob": "y" * 9000})
        raise AssertionError("oversized origin must be rejected")
    except ValidationError:
        pass


def test_triage_model_contracts():
    promote = CapturePromote()
    assert promote.priority == "NORMAL"
    try:
        CapturePromote(initiative_key="Invalid Key!")
        raise AssertionError("invalid initiative_key must be rejected")
    except ValidationError:
        pass
    try:
        CaptureAttach()
        raise AssertionError("attach without initiative_id must be rejected")
    except ValidationError:
        pass
    assert CaptureDiscard().note is None


def test_migration_ledger_is_additive_and_gapless():
    migrations = migrate.discover(ROOT / "db" / "migrations")
    names = [migration.filename for migration in migrations]
    assert names[0] == "000_docplane_genesis.sql"
    assert "001_work_captures.sql" in names
    ordinals = [migration.ordinal for migration in migrations]
    assert ordinals == list(range(ordinals[0], ordinals[-1] + 1))


def test_capture_migration_touches_no_existing_object():
    sql = (ROOT / "db" / "migrations" / "001_work_captures.sql").read_text(encoding="utf-8")
    lowered = sql.lower()
    assert "create table work.captures" in lowered
    for forbidden in ("alter table", "drop ", "update docs.", "update work.initiatives", "delete "):
        assert forbidden not in lowered, forbidden
