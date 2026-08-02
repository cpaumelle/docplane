"""Sprint 4 contract: closure gates, cross-domain links, provenance.

Binds the keystone rule — finishing an initiative answers one disposition
per durable domain — plus the soak observability gate, the additive
vocabulary extension of work.initiative_links, and the generated-page
guard. All without a database.
"""
from __future__ import annotations

import inspect
import re
import sys
import typing
from pathlib import Path

from fastapi import FastAPI
from pydantic import ValidationError

from app.publication import generated_guard_error
from app.runtime import state_identity
from app.work_api import router as work_router
from app.work_models import (
    DispositionsUpdate,
    InitiativeLinkCreate,
    WorkTransition,
    closure_gate_errors,
    soak_gate_errors,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "docs-api"))

import migrate  # noqa: E402

GENESIS_SQL = (ROOT / "db" / "migrations" / "000_docplane_genesis.sql").read_text(encoding="utf-8")
SPRINT4_SQL = (ROOT / "db" / "migrations" / "004_closure_gates_links_provenance.sql").read_text(encoding="utf-8")


def test_dispositions_route_exists_and_prior_surfaces_survive():
    app = FastAPI()
    app.include_router(work_router)
    paths = {route.path for route in app.routes}
    assert "/api/v1/initiatives/{initiative_id}/dispositions" in paths
    for prior in (
        "/api/v1/initiatives/{initiative_id}/promotion",
        "/api/v1/initiatives/{initiative_id}/transition",
        "/api/v1/work/captures",
    ):
        assert prior in paths, prior


def test_closure_gate_requires_one_disposition_per_durable_domain():
    # Fully answered: promoted knowledge, UPDATED carries resolvable evidence,
    # opt-outs carry notes.
    assert closure_gate_errors("PROMOTED", "UPDATED", None, "NOT_REQUIRED", "no runtime surface", has_model_evidence=True) == []
    assert closure_gate_errors("NOT_REQUIRED", "DEFERRED", "cards next sprint", "UPDATED", None, has_observe_evidence=True) == []
    # Nothing answered: one structured refusal per domain, machine-readable.
    missing = closure_gate_errors("NOT_READY", None, None, None, None)
    assert {item["domain"] for item in missing} == {"know", "model", "observe"}
    assert all("remedy" in item for item in missing)
    # UPDATED is evidence-bound, never honor-system.
    hollow = closure_gate_errors("PROMOTED", "UPDATED", None, "UPDATED", None)
    assert {(item["domain"], item["code"]) for item in hollow} == {
        ("model", "DISPOSITION_EVIDENCE_REQUIRED"),
        ("observe", "DISPOSITION_EVIDENCE_REQUIRED"),
    }
    # NOT_REQUIRED and DEFERRED demand a note — honest opt-outs carry reasons.
    noteless = closure_gate_errors("PROMOTED", "NOT_REQUIRED", "  ", "DEFERRED", None)
    assert {(item["domain"], item["code"]) for item in noteless} == {
        ("model", "DISPOSITION_NOTE_REQUIRED"),
        ("observe", "DISPOSITION_NOTE_REQUIRED"),
    }


def test_soak_gate_requires_observability():
    assert soak_gate_errors("alert:disk-full stays quiet 14d", False) == []
    assert soak_gate_errors(None, True) == []
    blocked = soak_gate_errors("   ", False)
    assert blocked and blocked[0]["code"] == "SOAK_OBSERVABILITY_MISSING"


def test_transition_and_dispositions_models_accept_the_new_fields():
    transition = WorkTransition(expected_version=3, work_state="COMPLETE", model_disposition="UPDATED", observe_disposition="NOT_REQUIRED", observe_disposition_note="no runtime surface")
    assert transition.model_disposition == "UPDATED"
    try:
        WorkTransition(expected_version=1, work_state="COMPLETE", model_disposition="MAYBE")
        raise AssertionError("unknown disposition must be rejected")
    except ValidationError:
        pass
    update = DispositionsUpdate(expected_version=2, soak_monitoring_ref="grafana:cluster-overview")
    assert update.model_disposition is None


def test_initiative_link_vocabulary_extended_additively():
    relations = set(typing.get_args(InitiativeLinkCreate.model_fields["relation"].annotation))
    resource_types = set(typing.get_args(InitiativeLinkCreate.model_fields["resource_type"].annotation))
    assert {"IMPLEMENTS", "CONCERNS", "PRODUCES", "UPDATES"} <= relations
    assert {"CONTEXT", "EVIDENCE", "DECISION", "RUNBOOK", "BLOCKED_BY", "PROMOTES_TO", "CHANGE", "CATALOG_SNAPSHOT"} <= relations
    assert {"MODEL_ENTITY", "ARTIFACT", "OBSERVATION"} <= resource_types
    assert {"PAGE", "INITIATIVE", "CHANGE", "CATALOG", "EXTERNAL"} <= resource_types

    def sql_values(sql: str, column: str) -> set[str]:
        match = re.search(r"CHECK \(" + column + r" IN \(([^)]+)\)", sql, re.S)
        assert match, column
        return {item.strip().strip("'") for item in match.group(1).replace("\n", " ").split(",") if item.strip()}

    old_relations = sql_values(GENESIS_SQL, "relation")
    new_relations = sql_values(SPRINT4_SQL, "relation")
    old_types = sql_values(GENESIS_SQL, "resource_type")
    new_types = sql_values(SPRINT4_SQL, "resource_type")
    # Extension is a strict superset — nothing existing is removed or renamed.
    assert new_relations > old_relations
    assert new_types > old_types
    assert new_relations == relations
    assert new_types == resource_types


def test_generated_page_guard_is_fail_closed_and_retirable():
    assert generated_guard_error("AUTHORED", "principal-a", "principal-b") is None
    assert generated_guard_error("GENERATED", None, "principal-b") is None  # declaration retired
    assert generated_guard_error("GENERATED", "principal-a", "principal-a") is None
    blocked = generated_guard_error("GENERATED", "principal-a", "principal-b")
    assert blocked and blocked["code"] == "PROVENANCE_GENERATED_PAGE_PROTECTED"
    assert "retire" in blocked["remedy"]


def test_provenance_is_excluded_from_state_identity():
    assert "provenance" not in inspect.getsource(state_identity)


def test_sprint4_migration_is_additive_and_gapless():
    migrations = migrate.discover(ROOT / "db" / "migrations")
    names = [migration.filename for migration in migrations]
    assert "004_closure_gates_links_provenance.sql" in names
    ordinals = [migration.ordinal for migration in migrations]
    assert ordinals == list(range(ordinals[0], ordinals[-1] + 1))
    lowered = SPRINT4_SQL.lower()
    # Only column additions and same-name constraint swaps; never destruction.
    for forbidden in ("drop table", "drop schema", "drop column", "delete from", "update docs.", "update work.", "update model."):
        assert forbidden not in lowered, forbidden
    dropped = re.findall(r"drop constraint (\w+)", lowered)
    added = re.findall(r"add constraint (\w+)", lowered)
    assert sorted(dropped) == sorted(added)  # every dropped CHECK is re-added under its own name
