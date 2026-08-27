from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.application import app
from app.work_conditions_api import GeneratedArtifactConditionSet


ROUTE = "/api/v1/work/generated-artifacts/{artifact_id}/conditions"


def test_generated_condition_routes_are_additive_and_idempotency_bound():
    schema = app.openapi()
    assert ROUTE in schema["paths"]
    assert "get" in schema["paths"][ROUTE]
    operation = schema["paths"][ROUTE]["put"]
    assert any(item["name"] == "Idempotency-Key" for item in operation.get("parameters", []))


def test_generated_condition_set_requires_unique_stable_kinds():
    request = GeneratedArtifactConditionSet(
        conditions=[
            {"condition_kind": "DRIFTED", "briefing": {"reason": "SOURCE_CHANGED"}},
            {"condition_kind": "GENERATION_FAILED", "briefing": {"reason": "LATEST_GENERATION_FAILED"}},
        ]
    )
    assert [item.condition_kind for item in request.conditions] == ["DRIFTED", "GENERATION_FAILED"]
    with pytest.raises(ValidationError):
        GeneratedArtifactConditionSet(
            conditions=[
                {"condition_kind": "DRIFTED"},
                {"condition_kind": "DRIFTED"},
            ]
        )
    with pytest.raises(ValidationError):
        GeneratedArtifactConditionSet(conditions=[{"condition_kind": "drifted"}])


def test_generated_condition_surface_is_separate_from_human_initiatives_and_coverage_gaps():
    source = __import__("inspect").getsource(
        __import__("app.work_conditions_api", fromlist=["reconcile_generated_artifact_conditions"])
    )
    assert "work.initiatives" not in source
    assert "work.coverage_gap_items" not in source
    assert "work.generated_artifact_conditions" in source
