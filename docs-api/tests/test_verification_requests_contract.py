"""Sprint 7 contract: verification-on-demand, ripple, and no cron — ever.

The staleness loop is on-demand and event-driven: requests mint with
briefings, agents execute on the fabric, results return through the
revision-bound verification contract. Expiry is a prompt only; these tests
are the enforcement pointer for "no scheduled re-verification exists".
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

from fastapi import FastAPI
from pydantic import ValidationError

import app.model_api as model_api
import app.verification_api as verification_api
from app.verification_api import (
    VerificationRequestComplete,
    VerificationRequestCreate,
    correction_policy,
    router as verification_router,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "docs-api"))

import migrate  # noqa: E402

SPRINT7_SQL = (ROOT / "db" / "migrations" / "005_verification_requests.sql").read_text(encoding="utf-8")


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(verification_router)
    return app


def test_verification_routes_exist_with_idempotent_mutations():
    app = _app()
    paths = {route.path for route in app.routes}
    for path in (
        "/api/v1/verification-requests",
        "/api/v1/verification-requests/{request_id}",
        "/api/v1/verification-requests/{request_id}/claim",
        "/api/v1/verification-requests/{request_id}/complete",
        "/api/v1/verification-requests/{request_id}/cancel",
        "/api/v1/maintenance/freshness",
    ):
        assert path in paths, path
    schema = app.openapi()
    for path in ("/api/v1/verification-requests", "/api/v1/verification-requests/{request_id}/complete"):
        parameters = schema["paths"][path]["post"].get("parameters", [])
        assert any(item["name"] == "Idempotency-Key" for item in parameters), path


def test_request_scope_is_exactly_one():
    page = "11111111-1111-1111-1111-111111111111"
    entity = "22222222-2222-2222-2222-222222222222"
    assert VerificationRequestCreate(page_resource_id=page).path_prefix is None
    assert VerificationRequestCreate(path_prefix="operations/proxmox").entity_id is None
    for kwargs in ({}, {"page_resource_id": page, "entity_id": entity}, {"page_resource_id": page, "path_prefix": "a"}):
        try:
            VerificationRequestCreate(**kwargs)
            raise AssertionError("exactly-one-scope must be enforced")
        except ValidationError:
            pass
    try:
        VerificationRequestCreate(path_prefix="Not A Path!")
        raise AssertionError("invalid prefix must be rejected")
    except ValidationError:
        pass


def test_completion_binds_outcome_and_evidence_references():
    complete = VerificationRequestComplete(outcome="VERIFIED", summary="Checked px5 quick-reference against qm config; all values hold.")
    assert complete.verification_ids == [] and complete.change_ids == []
    try:
        VerificationRequestComplete(outcome="PROBABLY_FINE", summary="x")
        raise AssertionError("unknown outcome must be rejected")
    except ValidationError:
        pass


def test_correction_policy_is_criticality_gated():
    assert correction_policy("NORMAL") == "DIRECT_PUBLISH"
    assert correction_policy("IMPORTANT") == "DIRECT_PUBLISH"
    assert correction_policy("OPERATIONAL_CRITICAL") == "PENDING_CHANGE"
    assert correction_policy("POLICY_REQUIRED") == "PENDING_CHANGE"


def test_model_entity_updates_ripple_into_verification_candidates():
    source = inspect.getsource(model_api.update_entity)
    assert "mint_ripple_request" in source
    # Ripple only fires when attributes actually changed.
    assert "request.attributes != entity[\"attributes\"]" in source
    ripple = inspect.getsource(verification_api.mint_ripple_request)
    assert "'DESCRIBES', 'OPERATES'" in ripple


def test_no_scheduler_exists_and_expiry_stays_a_prompt():
    source = inspect.getsource(verification_api)
    for banned in ("cron", "apscheduler", "sleep(", "timer(", "add_job", "background_tasks", "repeat_every"):
        assert banned not in source.lower(), banned
    # One live ripple request per entity, enforced by partial unique index.
    assert "verification_requests_ripple_open_idx" in SPRINT7_SQL
    assert "WHERE status = 'OPEN' AND reason = 'RIPPLE'" in SPRINT7_SQL


def test_sprint7_migration_is_additive_and_gapless():
    migrations = migrate.discover(ROOT / "db" / "migrations")
    names = [migration.filename for migration in migrations]
    assert "005_verification_requests.sql" in names
    ordinals = [migration.ordinal for migration in migrations]
    assert ordinals == list(range(ordinals[0], ordinals[-1] + 1))
    lowered = SPRINT7_SQL.lower()
    assert "create table docs.verification_requests" in lowered
    assert "num_nonnulls(page_resource_id, path_prefix, entity_id) = 1" in lowered
    for forbidden in ("alter table", "drop ", "update docs.", "update work.", "update model.", "delete from"):
        assert forbidden not in lowered, forbidden
