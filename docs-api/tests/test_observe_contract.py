"""Sprint 3 contract: observe is a push-only, append-only evidence ledger.

These tests are the enforcement pointer for the observe invariants: no
mutation surface, no pull, freshness derived not stored, pagination honest
from day one. All bound without a database.
"""
from __future__ import annotations

import re
import sys
import typing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from pydantic import ValidationError

from app.model_api import router as model_router
from app.observe_api import derive_freshness, plan_gap_reconciliation, router as observe_router
from app.observe_models import ObservationBatch, ObservationCreate

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "docs-api"))

import migrate  # noqa: E402

OBSERVE_SQL = (ROOT / "db" / "migrations" / "003_observe_genesis.sql").read_text(encoding="utf-8")


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(observe_router)
    app.include_router(model_router)
    return app


def test_observe_routes_exist_and_expose_no_mutation_surface():
    app = _app()
    observation_methods: set[str] = set()
    for route in app.routes:
        if getattr(route, "path", "") == "/api/v1/observations":
            observation_methods |= set(getattr(route, "methods", set()))
    assert "POST" in observation_methods and "GET" in observation_methods
    assert not observation_methods & {"PUT", "PATCH", "DELETE"}
    paths = {route.path for route in app.routes}
    assert "/api/v1/model/entities/{entity_id}/status" in paths
    assert "/api/v1/model/artifacts/{artifact_id}/status" in paths
    assert "/api/v1/observe/coverage/reconcile-work" in paths
    assert "/api/v1/observe/coverage/work-items" in paths


def test_gap_projection_is_bounded_convergent_resolving_and_reopening():
    candidates = [
        {"gap_kind": "MISSING_DESCRIPTION", "subject_entity_id": f"r{i}"}
        for i in range(12)
    ]
    first = plan_gap_reconciliation(candidates, [], 10)
    assert len(first["create"]) == 10
    existing = [
        {**item, "work_item_id": f"w{i}", "status": "OPEN"}
        for i, item in enumerate(first["create"])
    ]
    second = plan_gap_reconciliation(candidates, existing, 10)
    assert len(second["create"]) == 2
    known = existing + [
        {**item, "work_item_id": f"new{i}", "status": "OPEN"}
        for i, item in enumerate(second["create"])
    ]
    converged = plan_gap_reconciliation(candidates, known, 10)
    assert converged == {"create": [], "reopen": [], "refresh": [], "resolve": []}

    vanished = plan_gap_reconciliation(candidates[1:], known, 10)
    assert [item["subject_entity_id"] for item in vanished["resolve"]] == ["r0"]
    resolved = [{**known[0], "status": "RESOLVED"}] + known[1:]
    recurring = plan_gap_reconciliation(candidates, resolved, 10)
    assert [item["subject_entity_id"] for item in recurring["reopen"]] == ["r0"]


def test_coverage_reconcile_requires_idempotency_header():
    schema = _app().openapi()
    parameters = schema["paths"]["/api/v1/observe/coverage/reconcile-work"]["post"].get("parameters", [])
    assert any(item["name"] == "Idempotency-Key" for item in parameters)


def test_sprint6_scope_token_remains_additive_compatibility_contract():
    source = (ROOT / "docs-api/app/observe_api.py").read_text(encoding="utf-8")
    assert '"work_inbox_feed"' in source
    assert '"DELIVERED_AS_BOUNDED_WORK_PROJECTION"' in source


def test_observation_post_declares_idempotency_and_get_is_cursor_paginated():
    schema = _app().openapi()
    post_parameters = schema["paths"]["/api/v1/observations"]["post"].get("parameters", [])
    assert any(item["name"] == "Idempotency-Key" for item in post_parameters)
    get_parameters = {item["name"] for item in schema["paths"]["/api/v1/observations"]["get"].get("parameters", [])}
    assert {"after_seq", "limit"} <= get_parameters


def test_observation_requires_exactly_one_subject():
    entity = "11111111-1111-1111-1111-111111111111"
    artifact = "22222222-2222-2222-2222-222222222222"
    ok = ObservationCreate(subject_entity_id=entity, observation_kind="DEPLOYED_VERSION")
    assert ok.outcome == "NOMINAL"
    for kwargs in (
        {"observation_kind": "TEST"},
        {"subject_entity_id": entity, "subject_artifact_id": artifact, "observation_kind": "TEST"},
    ):
        try:
            ObservationCreate(**kwargs)
            raise AssertionError("exactly-one-subject must be enforced")
        except ValidationError:
            pass
    try:
        ObservationCreate(subject_entity_id=entity, observation_kind="TEST", source_fingerprint="NOT-HEX")
        raise AssertionError("non-hex fingerprint must be rejected")
    except ValidationError:
        pass
    try:
        ObservationBatch(observations=[])
        raise AssertionError("empty batch must be rejected")
    except ValidationError:
        pass


def test_freshness_is_derived_never_stored():
    now = datetime.now(timezone.utc)
    assert derive_freshness(None, "ab12cd34")["state"] == "NEVER_GENERATED"
    assert derive_freshness({"outcome": "FAILED", "source_fingerprint": "ab12cd34", "observed_at": now}, "ab12cd34")["state"] == "FAILED"
    assert derive_freshness({"outcome": "NOMINAL", "source_fingerprint": "ab12cd34", "observed_at": now}, "ab12cd34")["state"] == "FRESH"
    assert derive_freshness({"outcome": "NOMINAL", "source_fingerprint": "ab12cd34", "observed_at": now}, "ffffffff")["state"] == "DRIFTED"
    assert derive_freshness({"outcome": "NOMINAL", "source_fingerprint": "ab12cd34", "observed_at": now}, None)["state"] == "UNKNOWN"
    # The invariant in storage terms: no freshness/drift column exists — the
    # only occurrence of the word is the FRESHNESS_CHECK observation kind.
    stripped = OBSERVE_SQL.lower().replace("freshness_check", "")
    assert "freshness" not in stripped
    assert "drift" not in stripped


def test_append_only_is_a_database_invariant():
    assert "observations_append_only" in OBSERVE_SQL
    assert re.search(r"BEFORE UPDATE OR DELETE ON observe\.observations", OBSERVE_SQL)
    assert "RAISE EXCEPTION" in OBSERVE_SQL


def test_sql_and_api_vocabularies_cannot_drift():
    def sql_enum(column: str) -> set[str]:
        match = re.search(column + r" text NOT NULL(?: DEFAULT '[A-Z]+')? CHECK \(" + column + r" IN \(([^)]+)\)", OBSERVE_SQL, re.S)
        assert match, column
        return {item.strip().strip("'") for item in match.group(1).replace("\n", " ").split(",")}

    kinds = set(typing.get_args(ObservationCreate.model_fields["observation_kind"].annotation))
    outcomes = set(typing.get_args(ObservationCreate.model_fields["outcome"].annotation))
    assert sql_enum("observation_kind") == kinds
    assert sql_enum("outcome") == outcomes


def test_observe_migration_is_additive_and_gapless():
    migrations = migrate.discover(ROOT / "db" / "migrations")
    names = [migration.filename for migration in migrations]
    assert "003_observe_genesis.sql" in names
    ordinals = [migration.ordinal for migration in migrations]
    assert ordinals == list(range(ordinals[0], ordinals[-1] + 1))
    lowered = OBSERVE_SQL.lower()
    assert "create schema observe" in lowered
    for forbidden in ("alter table", "drop ", "update docs.", "update work.", "update model.", "delete from"):
        assert forbidden not in lowered, forbidden
