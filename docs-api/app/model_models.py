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


class EntityPageLinkCreate(BaseModel):
    relation: PageRelation
    page_resource_id: UUID


class ArtifactDeclare(BaseModel):
    artifact_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,126}$")
    generator_name: str = Field(min_length=1, max_length=200)
    generator_version: str = Field(min_length=1, max_length=100)
    config_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{16,64}$")
    source_entity_id: UUID
    redaction_policy: str = Field(default="canonical", min_length=1, max_length=200)
    # Ownership binds to stable page identities (model.artifact_targets);
    # paths are display metadata only and carry no protection semantics.
    target_page_resource_ids: list[UUID] = Field(default_factory=list, max_length=200)
    target_page_paths: list[str] = Field(default_factory=list, max_length=200)


class ArtifactRetire(BaseModel):
    expected_version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=4000)
