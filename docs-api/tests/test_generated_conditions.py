from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "docs-api"))

from generated_conditions import (  # noqa: E402
    CONDITION_KINDS,
    CatalogueTarget,
    EntityCatalogueState,
    derive_generated_artifact_conditions,
    meter_catalogues_state,
    schema_catalogues_state,
    work_catalogues_state,
)
from app.observe_api import derive_freshness  # noqa: E402


def status(**freshness):
    return {
        "artifact": {"artifact_id": "artifact-1", "artifact_key": "family"},
        "execution_contract_status": "DECLARED",
        "execution_contract": {
            "observation_max_age_seconds": 1800,
            "observation_owner_principal_id": "observer-1",
            "generation_owner_principal_id": "generator-1",
        },
        "freshness": {
            "state": "FRESH",
            "reason": "FINGERPRINTS_MATCH",
            "projection_correspondence": "MATCH",
            "source_observation_status": "CURRENT",
            **freshness,
        },
    }


def kinds(value):
    return {item["condition_kind"] for item in value}


def observe_status(freshness):
    return {
        "artifact": {"artifact_id": "artifact-1", "artifact_key": "family"},
        "execution_contract_status": "DECLARED",
        "execution_contract": {"observation_max_age_seconds": 60},
        "freshness": freshness,
    }


def observation(observation_id, outcome, fingerprint=None, observed_at=None):
    return {
        "observation_id": observation_id,
        "outcome": outcome,
        "source_fingerprint": fingerprint,
        "observed_at": observed_at or datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
    }


def test_v1_vocabulary_is_exact():
    assert CONDITION_KINDS == (
        "DRIFTED", "GENERATION_FAILED", "SOURCE_OBSERVATION_MISSING",
        "SOURCE_OBSERVATION_FAILED", "SOURCE_OBSERVATION_EXPIRED",
        "EXECUTION_CONTRACT_MISSING", "EXECUTION_OWNER_INACTIVE",
        "CATALOGUES_MISSING", "CATALOGUES_DRIFTED",
    )


def test_observe_predicates_and_affirmative_drift_resolution():
    generation = {"observation_id": "g1", "outcome": "NOMINAL", "summary": "secret prose"}
    source = {"observation_id": "s1", "outcome": "NOMINAL", "payload": {"secret": "no"}}
    drifted = status(
        state="DRIFTED", reason="SOURCE_CHANGED", projection_correspondence="MISMATCH",
        generation=generation, source_observation=source,
        generated_fingerprint="aaa", source_fingerprint="bbb",
    )
    assert kinds(derive_generated_artifact_conditions(drifted, [])) == {"DRIFTED"}
    unknown = status(state="UNKNOWN", reason="SOURCE_OBSERVATION_FAILED", projection_correspondence="MISMATCH")
    assert "DRIFTED" in kinds(derive_generated_artifact_conditions(unknown, []))
    unknown = status(state="UNKNOWN", reason="SOURCE_CHANGED", projection_correspondence="MATCH")
    assert "DRIFTED" not in kinds(derive_generated_artifact_conditions(unknown, []))
    assert not derive_generated_artifact_conditions(status(), [])


def test_real_freshness_successful_a_then_successful_b_is_drifted():
    generation = observation("generation-a", "NOMINAL", "A")
    source_b = observation("source-b", "NOMINAL", "B")
    freshness = derive_freshness(generation, generation, source_b)
    assert (freshness["state"], freshness["reason"], freshness["projection_correspondence"]) == (
        "DRIFTED", "SOURCE_CHANGED", "MISMATCH"
    )
    assert kinds(derive_generated_artifact_conditions(observe_status(freshness), [])) == {"DRIFTED"}


def test_real_freshness_expired_mismatch_remains_authoritative_drift():
    observed_at = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    generation = observation("generation-a", "NOMINAL", "A", observed_at)
    source_b = observation("source-b", "NOMINAL", "B", observed_at)
    contract = {"observation_max_age_seconds": 60}
    freshness = derive_freshness(
        generation, generation, source_b,
        latest_successful_source_observation=source_b,
        execution_contract=contract,
        now=observed_at + timedelta(seconds=61),
    )
    assert freshness["state"] == "DRIFTED"
    assert freshness["source_observation_status"] == "EXPIRED"
    projected = observe_status(freshness)
    projected["execution_contract"] = contract
    assert kinds(derive_generated_artifact_conditions(projected, [])) == {
        "DRIFTED", "SOURCE_OBSERVATION_EXPIRED"
    }


def test_real_freshness_failed_check_does_not_erase_prior_proven_mismatch():
    generation = observation("generation-a", "NOMINAL", "A")
    successful_b = observation("source-b", "NOMINAL", "B")
    failed = observation("source-failed", "FAILED")
    freshness = derive_freshness(
        generation, generation, failed,
        latest_successful_source_observation=successful_b,
    )
    assert (freshness["state"], freshness["reason"], freshness["projection_correspondence"]) == (
        "UNKNOWN", "SOURCE_OBSERVATION_FAILED", "MISMATCH"
    )
    assert kinds(derive_generated_artifact_conditions(observe_status(freshness), [])) == {
        "DRIFTED", "SOURCE_OBSERVATION_FAILED"
    }


def test_real_freshness_unknown_and_degraded_do_not_erase_prior_proven_mismatch():
    generation = observation("generation-a", "NOMINAL", "A")
    successful_b = observation("source-b", "NOMINAL", "B")
    for outcome in ("UNKNOWN", "DEGRADED"):
        latest = observation(f"source-{outcome.lower()}", outcome)
        freshness = derive_freshness(
            generation, generation, latest,
            latest_successful_source_observation=successful_b,
        )
        assert freshness["state"] == "UNKNOWN"
        assert freshness["reason"] == f"SOURCE_OBSERVATION_{outcome}"
        assert freshness["projection_correspondence"] == "MISMATCH"
        # UNKNOWN/DEGRADED has no v1 failure condition, but it does not erase
        # the separate, previously proven projection mismatch.
        assert kinds(derive_generated_artifact_conditions(observe_status(freshness), [])) == {"DRIFTED"}


def test_real_freshness_never_infers_drift_without_successful_source_evidence():
    generation = observation("generation-a", "NOMINAL", "A")
    for outcome in ("FAILED", "UNKNOWN", "DEGRADED"):
        latest = observation(f"source-{outcome.lower()}", outcome)
        freshness = derive_freshness(generation, generation, latest)
        assert freshness["projection_correspondence"] == "UNKNOWN"
        conditions = kinds(derive_generated_artifact_conditions(observe_status(freshness), []))
        assert "DRIFTED" not in conditions
        assert conditions == ({"SOURCE_OBSERVATION_FAILED"} if outcome == "FAILED" else set())


def test_generation_failed_requires_latest_durable_failed_attempt():
    failed = status(
        state="FAILED", reason="LATEST_GENERATION_FAILED",
        generation={"observation_id": "g2", "outcome": "FAILED", "summary": "not copied"},
    )
    result = derive_generated_artifact_conditions(failed, [])
    assert kinds(result) == {"GENERATION_FAILED"}
    assert "summary" not in json.dumps(result)
    assert "GENERATION_FAILED" not in kinds(derive_generated_artifact_conditions(status(), []))


def test_missing_failed_and_current_source_transitions_are_distinct():
    missing = status(
        state="UNKNOWN", reason="SOURCE_UNOBSERVED", source_observation=None,
        source_observation_status="ABSENT",
    )
    assert kinds(derive_generated_artifact_conditions(missing, [])) == {"SOURCE_OBSERVATION_MISSING"}
    failed = status(
        state="UNKNOWN", reason="SOURCE_OBSERVATION_FAILED",
        source_observation={"observation_id": "s2", "outcome": "FAILED"},
        source_observation_status="UNUSABLE",
    )
    assert kinds(derive_generated_artifact_conditions(failed, [])) == {"SOURCE_OBSERVATION_FAILED"}
    degraded = status(
        state="UNKNOWN", reason="SOURCE_OBSERVATION_DEGRADED",
        source_observation={"observation_id": "s3", "outcome": "DEGRADED"},
    )
    assert not derive_generated_artifact_conditions(degraded, [])
    assert not derive_generated_artifact_conditions(status(), [])


def test_expiry_requires_contract_and_resolves_on_current_observation():
    expired = status(
        state="UNKNOWN", reason="SOURCE_OBSERVATION_EXPIRED",
        source_observation_status="EXPIRED", observation_expires_at="2026-08-27T10:00:00Z",
        source_observation={"observation_id": "s4", "outcome": "NOMINAL"},
    )
    assert kinds(derive_generated_artifact_conditions(expired, [])) == {"SOURCE_OBSERVATION_EXPIRED"}
    expired["execution_contract"] = None
    assert not derive_generated_artifact_conditions(expired, [])
    assert not derive_generated_artifact_conditions(status(), [])


def test_execution_contract_transitions():
    missing = status()
    missing["execution_contract"] = None
    missing["execution_contract_status"] = "UNDECLARED_TRANSITIONAL"
    assert kinds(derive_generated_artifact_conditions(missing, [])) == {"EXECUTION_CONTRACT_MISSING"}
    inactive = status()
    inactive["execution_contract_status"] = "OWNER_INACTIVE"
    result = derive_generated_artifact_conditions(
        inactive, [], execution_owner_statuses={"observer-1": "REVOKED", "generator-1": "ACTIVE"}
    )
    assert kinds(result) == {"EXECUTION_OWNER_INACTIVE"}
    assert result[0]["briefing"]["inactive_owners"] == [
        {"role": "OBSERVATION", "principal_id": "observer-1", "status": "REVOKED"}
    ]


def test_catalogues_missing_and_drifted_can_coexist_and_are_bounded():
    entities = [
        EntityCatalogueState(
            "entity-missing", "SYSTEM", "ACTIVE",
            (CatalogueTarget("page-wanted", "work/index.md"),), (),
        ),
        EntityCatalogueState(
            "entity-drifted", "SCHEMA", "ACTIVE",
            (CatalogueTarget("page-right", "model/schema/right.md"),), ("page-wrong",),
        ),
    ]
    result = derive_generated_artifact_conditions(status(), entities)
    assert kinds(result) == {"CATALOGUES_MISSING", "CATALOGUES_DRIFTED"}
    assert all(len(item["briefing"]["samples"]) <= 10 for item in result)


def test_unresolved_or_inactive_desired_target_is_missing():
    for target in (CatalogueTarget(None, "missing.md", None), CatalogueTarget("p1", "archived.md", "archived")):
        entity = EntityCatalogueState("e1", "SYSTEM", "ACTIVE", (target,), (target.page_resource_id,) if target.page_resource_id else ())
        assert "CATALOGUES_MISSING" in kinds(derive_generated_artifact_conditions(status(), [entity]))


def test_meter_retired_link_is_drift_and_reactivation_restores_desired_mapping():
    pages = {"observe/meter/rule.md": CatalogueTarget("rule-page", "observe/meter/rule.md")}
    retired = meter_catalogues_state(
        service_entity_id="service", index_target=CatalogueTarget("index", "observe/meter/index.md"),
        rules=[{"entity_id": "rule", "status": "RETIRED", "source_page_path": "observe/meter/rule.md"}],
        pages_by_path=pages,
        actual_by_entity={"service": ["index"], "rule": ["rule-page"]},
    )
    result = derive_generated_artifact_conditions(status(), retired)
    assert kinds(result) == {"CATALOGUES_DRIFTED"}
    assert result[0]["briefing"]["retired_entity_count"] == 1
    active = meter_catalogues_state(
        service_entity_id="service", index_target=CatalogueTarget("index", "observe/meter/index.md"),
        rules=[{"entity_id": "rule", "status": "ACTIVE", "source_page_path": "observe/meter/rule.md"}],
        pages_by_path=pages,
        actual_by_entity={"service": ["index"], "rule": ["rule-page"]},
    )
    assert not derive_generated_artifact_conditions(status(), active)


def test_family_adapters_use_structured_dynamic_state_only():
    work = work_catalogues_state(
        system_entity_id="system", index_target=CatalogueTarget("work-page", "work/index.md"),
        actual_page_resource_ids=["work-page"],
    )
    assert len(work) == 1
    schema = schema_catalogues_state(
        database_entity_id="database", index_target=CatalogueTarget("db-page", "model/schema/index.md"),
        schemas=[
            {"entity_id": "active-schema", "status": "ACTIVE", "catalogue_target": CatalogueTarget("schema-page", "model/schema/docs.md")},
            {"entity_id": "retired-schema", "status": "RETIRED", "catalogue_target": CatalogueTarget("old", "model/schema/old.md")},
        ],
        actual_by_entity={"database": ["db-page"], "active-schema": ["schema-page"]},
    )
    assert [item.entity_kind for item in schema] == ["DATABASE", "SCHEMA", "SCHEMA"]
    assert schema[-1].desired_targets == ()
    meter = meter_catalogues_state(
        service_entity_id="service", index_target=CatalogueTarget("meter-index", "observe/meter/index.md"),
        rules=[
            {"entity_id": f"rule-{index}", "status": "ACTIVE", "source_page_path": "observe/meter/shared.md"}
            for index in range(13)
        ],
        pages_by_path={"observe/meter/shared.md": CatalogueTarget("shared", "observe/meter/shared.md")},
        actual_by_entity={"service": ["meter-index"], **{f"rule-{index}": ["shared"] for index in range(13)}},
    )
    assert len(meter) == 14
    assert not derive_generated_artifact_conditions(status(), meter)


def test_briefing_is_deterministic_secret_safe_and_sample_bounded():
    entities = [
        EntityCatalogueState(
            f"entity-{index:02}", "MONITOR_RULE", "ACTIVE",
            (CatalogueTarget(f"page-{index:02}", f"observe/page-{index:02}.md"),), (),
        )
        for index in reversed(range(15))
    ]
    unsafe = status(
        state="FAILED", reason="LATEST_GENERATION_FAILED",
        generation={
            "observation_id": "g-secret", "outcome": "FAILED",
            "summary": "Bearer should-not-appear", "payload": {"dsn": "postgres://secret"},
        },
    )
    first = derive_generated_artifact_conditions(unsafe, entities)
    second = derive_generated_artifact_conditions(unsafe, list(reversed(entities)))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    encoded = json.dumps(first)
    assert "should-not-appear" not in encoded
    assert "postgres://" not in encoded
    missing = next(item for item in first if item["condition_kind"] == "CATALOGUES_MISSING")
    assert len(missing["briefing"]["samples"]) == 10


def test_current_production_shaped_state():
    exact = [EntityCatalogueState("entity", "SYSTEM", "ACTIVE", (CatalogueTarget("page", "index.md"),), ("page",))]
    work = status()
    assert not derive_generated_artifact_conditions(work, exact)
    for artifact_key in ("schema-catalogue-docplane", "meter-list-hub2.prometheus"):
        current = status(
            state="UNKNOWN", reason="SOURCE_UNOBSERVED", source_observation=None,
            source_observation_status="ABSENT",
        )
        current["artifact"]["artifact_key"] = artifact_key
        current["execution_contract"] = None
        current["execution_contract_status"] = "UNDECLARED_TRANSITIONAL"
        assert kinds(derive_generated_artifact_conditions(current, exact)) == {
            "SOURCE_OBSERVATION_MISSING", "EXECUTION_CONTRACT_MISSING"
        }
