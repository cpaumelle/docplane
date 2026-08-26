"""Sprint 2 contract: the model card index is additive, contract-bound and fail-closed.

Route inventory, OpenAPI additivity, harvested checklist enforcement,
two-layer secret safety, vocabulary closure between SQL and API, and
migration-file discipline — all without a database.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.model_api import router as model_router
from app.model_contracts import (
    CARD_CONTRACTS,
    checklist_errors,
    contracts_document,
    secret_findings,
)
from app.model_models import (
    ArtifactDeclare,
    ArtifactExecutionContract,
    ArtifactExecutionContractUpdate,
    ArtifactTargetSet,
    GeneratedOwnershipPlan,
    EntityCreate,
    EntityLinkCreate,
)
from app.work_api import router as work_router

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "docs-api"))

import migrate  # noqa: E402

MODEL_SQL = (ROOT / "db" / "migrations" / "002_model_genesis.sql").read_text(encoding="utf-8")


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(model_router)
    app.include_router(work_router)
    return app


def test_model_routes_exist_and_prior_surfaces_survive():
    paths = {route.path for route in _app().routes}
    for new in (
        "/api/v1/model/contracts",
        "/api/v1/model/entities",
        "/api/v1/model/entities/{entity_id}",
        "/api/v1/model/entities/{entity_id}/update",
        "/api/v1/model/entities/{entity_id}/retire",
        "/api/v1/model/entities/{entity_id}/links",
        "/api/v1/model/entities/{entity_id}/page-links",
        "/api/v1/model/artifacts",
        "/api/v1/model/artifacts/{artifact_id}",
        "/api/v1/model/artifacts/{artifact_id}/execution-contract",
        "/api/v1/model/artifacts/{artifact_id}/targets",
        "/api/v1/model/artifacts/{predecessor_id}/handoff",
        "/api/v1/model/artifacts/{artifact_id}/retire",
        "/api/v1/bootstrap/model/artifacts/{artifact_id}/reassign-custody",
    ):
        assert new in paths, new
    for prior in ("/api/v1/initiatives", "/api/v1/work/queues", "/api/v1/work/captures"):
        assert prior in paths, prior


def test_model_mutations_declare_required_idempotency_header():
    schema = _app().openapi()
    for path in (
        "/api/v1/model/entities",
        "/api/v1/model/entities/{entity_id}/update",
        "/api/v1/model/entities/{entity_id}/links",
        "/api/v1/model/artifacts",
    ):
        parameters = schema["paths"][path]["post"].get("parameters", [])
        assert any(item["name"] == "Idempotency-Key" for item in parameters), path

    update = schema["paths"]["/api/v1/model/artifacts/{artifact_id}/execution-contract"]["put"]
    assert any(item["name"] == "Idempotency-Key" for item in update.get("parameters", []))

    custody = schema["paths"]["/api/v1/bootstrap/model/artifacts/{artifact_id}/reassign-custody"]["post"]
    names = {item["name"] for item in custody.get("parameters", [])}
    assert {"Idempotency-Key", "X-DocPlane-Bootstrap-Token"} <= names


def test_projection_contract_and_exact_set_models_are_explicit():
    artifact = ArtifactDeclare(
        artifact_key="example", generator_name="example", generator_version="build-7",
        source_entity_id="00000000-0000-0000-0000-000000000001",
    )
    assert artifact.projection_contract_version == 1
    exact = ArtifactTargetSet(
        expected_version=1, target_page_resource_ids=[], target_page_paths=[], generator_version="build-8",
    )
    assert exact.target_page_resource_ids == []
    with pytest.raises(ValidationError):
        ArtifactTargetSet(
            expected_version=1,
            target_page_resource_ids=["00000000-0000-0000-0000-000000000002"],
            target_page_paths=[], generator_version="build-8",
        )


def test_generated_ownership_plan_is_narrow_and_exact_set_bound():
    plan = GeneratedOwnershipPlan(
        mode="IN_PLACE", artifact_id="00000000-0000-0000-0000-000000000003",
        expected_version=2,
        target_page_resource_ids=["00000000-0000-0000-0000-000000000004"],
        target_page_paths=["work/index.md"], generator_version="1.0.1",
    )
    assert plan.mode == "IN_PLACE"
    with pytest.raises(ValidationError):
        GeneratedOwnershipPlan(
            mode="IN_PLACE", expected_version=2,
            target_page_resource_ids=[], target_page_paths=[], generator_version="1.0.1",
        )


def test_atomic_ownership_migration_is_additive_and_backfills_contract_v1():
    sql = (ROOT / "db" / "migrations" / "016_atomic_generated_artifact_ownership.sql").read_text()
    assert "projection_contract_version integer NOT NULL DEFAULT 1" in sql
    assert "generated_ownership_plan jsonb" in sql
    assert "UPDATE docs.pages SET provenance = 'AUTHORED'" not in sql


def test_contracts_document_publishes_every_kind_with_schema():
    document = contracts_document()
    assert document["contract_version"] == "model-contracts-v1"
    assert set(document["kinds"]) == set(CARD_CONTRACTS)
    vm = document["kinds"]["VM"]
    assert vm["ratified"] is True
    assert set(vm["attributes_schema"]["required"]) == {"vmid", "host_node", "ip", "hostname"}
    node = document["kinds"]["NODE"]
    assert set(node["attributes_schema"]["required"]) == {"cpu", "memory", "os"}


def test_sql_and_api_vocabularies_cannot_drift():
    def sql_enum(column: str) -> set[str]:
        match = re.search(column + r" text NOT NULL(?: DEFAULT '[A-Z]+')? CHECK \(" + column + r" IN \(([^)]+)\)", MODEL_SQL, re.S)
        assert match, column
        return {item.strip().strip("'") for item in match.group(1).replace("\n", " ").split(",")}

    def effective_sql_enum(column: str) -> set[str]:
        """Vocabularies are extended by LATER migrations re-adding the CHECK
        (the additive contract's vocabulary-extension case, first used by
        008 for MONITOR_RULE). The effective enum is the last definition
        across the ordered migration sequence — genesis or ALTER alike."""
        # Matches both the genesis column definition and a later
        # ALTER ... ADD CONSTRAINT re-definition: the CHECK expression is
        # the shared shape.
        pattern = re.compile(r"CHECK \(" + column + r" IN \(([^)]+)\)", re.S)
        values: set[str] | None = None
        for path in sorted((ROOT / "db" / "migrations").glob("*.sql")):
            for match in pattern.finditer(path.read_text(encoding="utf-8")):
                values = {item.strip().strip("'") for item in match.group(1).replace("\n", " ").split(",")}
        assert values is not None, column
        return values

    assert effective_sql_enum("entity_kind") == set(CARD_CONTRACTS)
    from app.model_models import EntityLinkCreate as Link, EntityPageLinkCreate as PageLink
    import typing

    link_relations = set(typing.get_args(Link.model_fields["relation"].annotation))
    page_relations = set(typing.get_args(PageLink.model_fields["relation"].annotation))
    assert sql_enum("relation") >= link_relations  # first relation CHECK in file is entity_links
    assert {"WIRED_TO", "RUNS_ON", "MEMBER_OF", "DEPENDS_ON", "EXPOSES", "STORES_IN", "GENERATED_FROM", "WATCHES"} == link_relations
    assert {"DESCRIBES", "OPERATES", "DECIDES", "CATALOGUES"} == page_relations
    for relation in page_relations:
        assert f"'{relation}'" in MODEL_SQL


def test_event_channels_used_by_api_are_allowed_by_schema():
    genesis = (ROOT / "db" / "migrations" / "000_docplane_genesis.sql").read_text(encoding="utf-8")
    match = re.search(r"channel text NOT NULL CHECK \(channel IN \(([^)]+)\)\)", genesis)
    assert match
    allowed = {item.strip().strip("'") for item in match.group(1).split(",")}
    used = set()
    for path in (ROOT / "docs-api" / "app").glob("*.py"):
        used.update(re.findall(r'channel="([A-Z]+)"', path.read_text(encoding="utf-8")))
    assert used <= allowed


def test_harvested_checklists_enforce_ratified_kinds_only():
    vm_ok = {"vmid": 5513, "host_node": "px5-lemans", "ip": "10.35.1.113", "hostname": "browan-fw-dev"}
    assert checklist_errors("VM", vm_ok) == []
    errors = checklist_errors("VM", {"vmid": 5513})
    missing = {item["field"] for item in errors if item["error"] == "REQUIRED_FIELD_MISSING"}
    assert missing == {"host_node", "ip", "hostname"}
    assert checklist_errors("NODE", {"cpu": "i5-7600", "memory": "32 GB", "os": "Proxmox VE 9"}) == []
    shape = checklist_errors("SITE", {"lan_subnets": "10.75.1.0/24"})
    assert any(item["error"] == "FIELD_SHAPE_INVALID" for item in shape)
    assert checklist_errors("SITE", {"lan_subnets": ["10.75.1.0/24"]}) == []
    # Unratified kinds accept anything until they earn a checklist.
    assert checklist_errors("DEVICE_MODEL", {}) == []
    assert checklist_errors("SYSTEM", {"whatever": True}) == []


def test_secret_scan_fails_closed_on_shapes_and_key_names():
    clean = {"vmid": 5513, "mac": "BC:24:11:28:9B:42", "ip": "10.35.1.113"}
    assert secret_findings(clean) == []
    shaped = secret_findings({"note": "token ghp_" + "a" * 36})
    assert any(item["code"] == "SECRET_SHAPED_VALUE" for item in shaped)
    named = secret_findings({"db_password": "hunter2-real-value"})
    assert any(item["code"] == "SECRET_NAMED_KEY_WITH_VALUE" for item in named)
    # Field NAMES with placeholder values are the harvest contract and pass.
    assert secret_findings({"db_password": "{{password}}"}) == []
    assert secret_findings({"api_key": "<set in env>"}) == []
    nested = secret_findings({"env": [{"connect_token": "real-looking-value"}]})
    assert any(item["path"] == "env[0].connect_token" for item in nested)


def test_entity_models_bind_identity_contract():
    entity = EntityCreate(entity_kind="VM", entity_key="browan-fw-dev", display_name="Browan firmware dev VM")
    assert entity.attributes == {}
    for bad_key in ("Invalid Key", "-leading", "UPPER"):
        try:
            EntityCreate(entity_kind="VM", entity_key=bad_key, display_name="x")
            raise AssertionError(bad_key)
        except ValidationError:
            pass
    try:
        EntityCreate(entity_kind="TOASTER", entity_key="t1", display_name="x")
        raise AssertionError("unknown kind must be rejected")
    except ValidationError:
        pass


def test_execution_contract_is_typed_bounded_and_optional_for_compatibility():
    owner = "11111111-1111-1111-1111-111111111111"
    contract = ArtifactExecutionContract(
        observation_owner_principal_id=owner,
        observation_trigger="SCHEDULED",
        observation_max_age_seconds=300,
        generation_owner_principal_id=owner,
        generation_trigger="MANUAL",
        exclusion_domain="work-catalogue",
    )
    assert contract.contract_schema_version == 1
    legacy = ArtifactDeclare(
        artifact_key="legacy", generator_name="g", generator_version="1",
        source_entity_id=owner,
    )
    assert legacy.execution_contract is None
    updated = ArtifactExecutionContractUpdate(expected_version=2, **contract.model_dump())
    assert updated.expected_version == 2
    for field, value in (
        ("observation_max_age_seconds", 0),
        ("observation_max_age_seconds", -1),
        ("observation_max_age_seconds", 31536001),
        ("observation_trigger", "CRON"),
        ("generation_trigger", "TIMER"),
        ("exclusion_domain", "/run/lock/work.lock"),
    ):
        payload = contract.model_dump()
        payload[field] = value
        try:
            ArtifactExecutionContract(**payload)
            raise AssertionError(f"invalid contract accepted: {field}={value}")
        except ValidationError:
            pass


def test_execution_contract_migration_is_additive_unpopulated_and_relational():
    sql = (ROOT / "db/migrations/015_generated_artifact_execution_contracts.sql").read_text(encoding="utf-8")
    lowered = sql.lower()
    assert "create table model.generated_artifact_execution_contracts" in lowered
    assert "references model.generated_artifacts" in lowered
    assert lowered.count("references docplane.principals") >= 4
    assert "insert into" not in lowered
    for forbidden in ("drop ", "delete from", "update model.", "update docs.", "update observe."):
        assert forbidden not in lowered
    try:
        EntityLinkCreate(relation="POKES", to_entity_id="11111111-1111-1111-1111-111111111111")
        raise AssertionError("unknown relation must be rejected")
    except ValidationError:
        pass
    try:
        ArtifactDeclare(artifact_key="k", generator_name="tbls", generator_version="1", source_entity_id="11111111-1111-1111-1111-111111111111", config_hash="NOT-HEX")
        raise AssertionError("non-hex config hash must be rejected")
    except ValidationError:
        pass


def test_model_migration_is_additive_and_gapless():
    migrations = migrate.discover(ROOT / "db" / "migrations")
    names = [migration.filename for migration in migrations]
    assert "002_model_genesis.sql" in names
    ordinals = [migration.ordinal for migration in migrations]
    assert ordinals == list(range(ordinals[0], ordinals[-1] + 1))
    lowered = MODEL_SQL.lower()
    assert "create schema model" in lowered
    for forbidden in ("alter table", "drop ", "update docs.", "update work.", "delete from"):
        assert forbidden not in lowered, forbidden
