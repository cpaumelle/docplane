from __future__ import annotations

import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

EntityKind = Literal[
    "SYSTEM", "SERVICE", "NODE", "VM", "SITE", "NETWORK", "DATABASE",
    "SCHEMA", "API", "ROUTE", "DEVICE_MODEL", "INTERFACE", "ARTIFACT",
    "MONITOR_RULE",
]

EntityRelation = Literal[
    "WIRED_TO", "RUNS_ON", "MEMBER_OF", "DEPENDS_ON", "EXPOSES",
    "STORES_IN", "GENERATED_FROM", "WATCHES",
]

PageRelation = Literal["DESCRIBES", "OPERATES", "DECIDES", "CATALOGUES"]
ExecutionTrigger = Literal["MANUAL", "SCHEDULED", "EVENT_DRIVEN", "HYBRID"]


def _attributes_bounded(attributes: dict[str, Any]) -> None:
    if len(json.dumps(attributes, sort_keys=True, ensure_ascii=False, default=str)) > 60000:
        raise ValueError("attributes exceed 60000 bytes")


class EntityCreate(BaseModel):
    entity_kind: EntityKind
    entity_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,126}$")
    display_name: str = Field(min_length=1, max_length=300)
    attributes: dict[str, Any] = Field(default_factory=dict)
    owner_principal_id: UUID | None = None

    @model_validator(mode="after")
    def bounded(self):
        _attributes_bounded(self.attributes)
        return self


class EntityUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=300)
    attributes: dict[str, Any] | None = None
    owner_principal_id: UUID | None = None

    @model_validator(mode="after")
    def bounded(self):
        if self.attributes is not None:
            _attributes_bounded(self.attributes)
        return self


class EntityRetire(BaseModel):
    expected_version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=4000)


class EntityLinkCreate(BaseModel):
    relation: EntityRelation
    to_entity_id: UUID
    note: str | None = Field(default=None, max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def metadata_bounded(self):
        if len(json.dumps(self.metadata, sort_keys=True, ensure_ascii=False, default=str)) > 7600:
            raise ValueError("metadata exceeds 7600 bytes")
        return self


class EntityLinkRemove(BaseModel):
    relation: EntityRelation
    to_entity_id: UUID
    note: str | None = Field(default=None, max_length=2000)


class EntityPageLinkCreate(BaseModel):
    relation: PageRelation
    page_resource_id: UUID


class ArtifactExecutionContract(BaseModel):
    contract_schema_version: Literal[1] = 1
    observation_owner_principal_id: UUID
    observation_trigger: ExecutionTrigger
    observation_max_age_seconds: int = Field(gt=0, le=31536000)
    generation_owner_principal_id: UUID
    generation_trigger: ExecutionTrigger
    exclusion_domain: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,126}$")


class ArtifactExecutionContractUpdate(ArtifactExecutionContract):
    expected_version: int = Field(ge=1)


class ArtifactDeclare(BaseModel):
    artifact_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,126}$")
    generator_name: str = Field(min_length=1, max_length=200)
    generator_version: str = Field(min_length=1, max_length=100)
    projection_contract_version: int = Field(default=1, ge=1, le=2147483647)
    config_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{16,64}$")
    source_entity_id: UUID
    redaction_policy: str = Field(default="canonical", min_length=1, max_length=200)
    # Ownership binds to stable page identities (model.artifact_targets);
    # paths are display metadata only and carry no protection semantics.
    target_page_resource_ids: list[UUID] = Field(default_factory=list, max_length=200)
    target_page_paths: list[str] = Field(default_factory=list, max_length=200)
    execution_contract: ArtifactExecutionContract | None = None


class ArtifactRetire(BaseModel):
    expected_version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=4000)


class ArtifactTargetSet(BaseModel):
    expected_version: int = Field(ge=1)
    target_page_resource_ids: list[UUID] = Field(max_length=200)
    target_page_paths: list[str] = Field(max_length=200)
    generator_version: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def exact_set(self):
        if len(self.target_page_resource_ids) != len(self.target_page_paths):
            raise ValueError("target IDs and paths must have equal length")
        if len(set(self.target_page_resource_ids)) != len(self.target_page_resource_ids):
            raise ValueError("target IDs must be unique")
        if len(set(self.target_page_paths)) != len(self.target_page_paths):
            raise ValueError("target paths must be unique")
        return self


class ArtifactSuccessor(BaseModel):
    artifact_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,126}$")
    generator_name: str = Field(min_length=1, max_length=200)
    generator_version: str = Field(min_length=1, max_length=100)
    projection_contract_version: int = Field(ge=1, le=2147483647)
    config_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{16,64}$")
    source_entity_id: UUID
    redaction_policy: str = Field(default="canonical", min_length=1, max_length=200)
    target_page_resource_ids: list[UUID] = Field(max_length=200)
    target_page_paths: list[str] = Field(max_length=200)

    @model_validator(mode="after")
    def exact_set(self):
        if len(self.target_page_resource_ids) != len(self.target_page_paths):
            raise ValueError("target IDs and paths must have equal length")
        if len(set(self.target_page_resource_ids)) != len(self.target_page_resource_ids):
            raise ValueError("target IDs must be unique")
        if len(set(self.target_page_paths)) != len(self.target_page_paths):
            raise ValueError("target paths must be unique")
        return self


class ArtifactHandoff(BaseModel):
    expected_version: int = Field(ge=1)
    successor: ArtifactSuccessor


class GeneratedOwnershipPlan(BaseModel):
    mode: Literal["IN_PLACE", "SUCCESSOR"]
    artifact_id: UUID | None = None
    predecessor_id: UUID | None = None
    expected_version: int = Field(ge=1)
    target_page_resource_ids: list[UUID] = Field(max_length=200)
    target_page_paths: list[str] = Field(max_length=200)
    generator_version: str = Field(min_length=1, max_length=100)
    successor: ArtifactSuccessor | None = None

    @model_validator(mode="after")
    def coherent(self):
        if len(self.target_page_resource_ids) != len(self.target_page_paths):
            raise ValueError("target IDs and paths must have equal length")
        if len(set(self.target_page_resource_ids)) != len(self.target_page_resource_ids):
            raise ValueError("target IDs must be unique")
        if self.mode == "IN_PLACE" and (self.artifact_id is None or self.predecessor_id is not None or self.successor is not None):
            raise ValueError("IN_PLACE requires artifact_id only")
        if self.mode == "SUCCESSOR" and (self.predecessor_id is None or self.artifact_id is not None or self.successor is None):
            raise ValueError("SUCCESSOR requires predecessor_id and successor")
        if self.successor is not None and (
            self.successor.target_page_resource_ids != self.target_page_resource_ids
            or self.successor.target_page_paths != self.target_page_paths
            or self.successor.generator_version != self.generator_version
        ):
            raise ValueError("successor target set and generator attribution must match the ownership plan")
        return self


class ArtifactCustodyReassign(BaseModel):
    expected_version: int = Field(ge=1)
    destination_principal_id: UUID
    purpose: str = Field(min_length=10, max_length=4000)
