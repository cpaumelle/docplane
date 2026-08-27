"""Cross-layer contract: pure derivation must satisfy the #162 WORK receiver."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from generated_conditions import (  # noqa: E402
    CatalogueTarget,
    EntityCatalogueState,
    derive_generated_artifact_conditions,
)
from app.work_conditions_api import (  # noqa: E402
    GeneratedArtifactConditionSet,
    _validate_briefings,
)


def _status(**freshness):
    return {
        "artifact": {"artifact_id": "artifact-1", "artifact_key": "family"},
        "execution_contract_status": "DECLARED",
        "execution_contract": {"observation_max_age_seconds": 1800},
        "freshness": {
            "state": "FRESH",
            "reason": "FINGERPRINTS_MATCH",
            "projection_correspondence": "MATCH",
            "source_observation_status": "CURRENT",
            **freshness,
        },
    }


def _request(derived):
    request = GeneratedArtifactConditionSet(
        conditions=[
            {"condition_kind": item["condition_kind"], "briefing": item["briefing"]}
            for item in derived
        ]
    )
    _validate_briefings(request)
    return request


def test_reproduces_prefix_insensitive_hex_secret_rejection_before_fix():
    digest = "a" * 64
    for value in (digest, f"sha256:{digest}"):
        request = GeneratedArtifactConditionSet(
            conditions=[{"condition_kind": "DRIFTED", "briefing": {"fingerprint": value}}]
        )
        with pytest.raises(HTTPException) as rejected:
            _validate_briefings(request)
        assert rejected.value.status_code == 422
        assert rejected.value.detail["errors"][0]["findings"][0]["classes"] == ["HEX_SECRET"]


def test_drifted_realistic_fingerprints_are_omitted_and_receiver_accepted():
    digest_a = "0123456789abcdef" * 4
    digest_b = "fedcba9876543210" * 4
    derived = derive_generated_artifact_conditions(
        _status(
            state="DRIFTED",
            reason="SOURCE_CHANGED",
            projection_correspondence="MISMATCH",
            generated_fingerprint=digest_a,
            source_fingerprint=digest_b,
            generation={"observation_id": "generation-1", "outcome": "NOMINAL", "source_fingerprint": digest_a},
            source_observation={"observation_id": "source-1", "outcome": "NOMINAL", "source_fingerprint": digest_b},
        ),
        [],
    )
    request = _request(derived)
    encoded = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    assert digest_a not in encoded and digest_b not in encoded
    assert request.conditions[0].briefing == {
        "generation": {"observation_id": "generation-1", "outcome": "NOMINAL"},
        "projection_correspondence": "MISMATCH",
        "reason": "SOURCE_CHANGED",
        "source_observation": {"observation_id": "source-1", "outcome": "NOMINAL"},
        "state": "DRIFTED",
    }


def test_catalogues_missing_and_drifted_simultaneously_pass_receiver():
    digest_shaped_path = "observe/" + "a" * 64 + "/index.md"
    entities = [
        EntityCatalogueState(
            "entity-missing", "SYSTEM", "ACTIVE",
            (CatalogueTarget(None, digest_shaped_path, None),), (),
        ),
        EntityCatalogueState(
            "entity-drifted", "SCHEMA", "ACTIVE",
            (CatalogueTarget("page-right", "model/schema/right.md"),), ("page-wrong",),
        ),
    ]
    request = _request(derive_generated_artifact_conditions(_status(), entities))
    assert {item.condition_kind for item in request.conditions} == {
        "CATALOGUES_MISSING", "CATALOGUES_DRIFTED"
    }
    encoded = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    assert "desired_set_digest" not in encoded
    assert "actual_set_digest" not in encoded
    assert "a" * 64 not in encoded


def test_complete_exact_or_empty_condition_set_passes_receiver():
    exact = EntityCatalogueState(
        "entity", "SYSTEM", "ACTIVE", (CatalogueTarget("page", "work/index.md"),), ("page",)
    )
    request = _request(derive_generated_artifact_conditions(_status(), [exact]))
    assert request.conditions == []


def test_multiple_simultaneous_non_catalogue_conditions_pass_receiver():
    current = _status(
        state="UNKNOWN",
        reason="SOURCE_UNOBSERVED",
        source_observation=None,
        source_observation_status="ABSENT",
    )
    current["execution_contract"] = None
    current["execution_contract_status"] = "UNDECLARED_TRANSITIONAL"
    request = _request(derive_generated_artifact_conditions(current, []))
    assert {item.condition_kind for item in request.conditions} == {
        "SOURCE_OBSERVATION_MISSING", "EXECUTION_CONTRACT_MISSING"
    }


def test_briefing_is_deterministically_byte_bounded_with_explicit_truncation():
    long_identity = "entity-" + "x" * 10_000
    entities = [
        EntityCatalogueState(
            f"{long_identity}-{index:02}", "MONITOR_RULE", "ACTIVE",
            (CatalogueTarget(f"page-{index}", "observe/rule.md"),), (),
        )
        for index in range(10)
    ]
    first = derive_generated_artifact_conditions(_status(), entities)
    second = derive_generated_artifact_conditions(_status(), list(reversed(entities)))
    request = _request(first)
    assert first == second
    briefing = request.conditions[0].briefing
    assert len(json.dumps(briefing, sort_keys=True, default=str).encode("utf-8")) <= 8192
    assert briefing["briefing_truncation"]["long_field_count"] == 10
    assert briefing["sample_count_total"] == 10
    assert all(sample["entity_id"]["omitted"] is True for sample in briefing["samples"])
