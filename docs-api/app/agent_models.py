from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class PrincipalCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    principal_kind: Literal["HUMAN", "AGENT", "AUTOMATION"] = "HUMAN"
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PrincipalToken(BaseModel):
    principal_id: UUID
    display_name: str
    principal_kind: str
    role: Literal["CONTRIBUTOR"] = "CONTRIBUTOR"
    token: str
    token_prefix: str
    expires_at: datetime | None


class ChangeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    purpose: str = Field(min_length=1, max_length=4000)
    workspace_key: str = Field(default="reference", pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    base_state_identity: str | None = Field(default=None, max_length=256)


_OPERATION_TYPES = {
    "CREATE_PAGE",
    "REPLACE_DOCUMENT",
    "PATCH_METADATA",
    "REPLACE_SECTION",
    "INSERT_BEFORE_HEADING",
    "INSERT_AFTER_HEADING",
    "MOVE_PAGE",
    "REPARENT_NAV",
    "ARCHIVE_PAGE",
    "RESTORE_PAGE",
    "ADD_REDIRECT",
    "REMOVE_REDIRECT",
    "REORDER_SECTIONS",
}


class ChangeOperationCreate(BaseModel):
    operation_type: str = Field(json_schema_extra={"enum": sorted(_OPERATION_TYPES)})
    page_resource_id: UUID | None = None
    expected_revision: str | None = Field(default=None, max_length=200)
    expected_section_hash: str | None = Field(default=None, max_length=128)
    payload: dict[str, Any]
    sequence: int | None = Field(default=None, ge=0)

    @field_validator("operation_type")
    @classmethod
    def operation_known(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in _OPERATION_TYPES:
            raise ValueError("unsupported operation_type")
        return normalized

    @model_validator(mode="after")
    def exact_binding(self):
        unbound = {"CREATE_PAGE", "ADD_REDIRECT", "REMOVE_REDIRECT", "REORDER_SECTIONS"}
        if self.operation_type not in unbound:
            if self.page_resource_id is None:
                raise ValueError("page_resource_id is required")
            if not self.expected_revision:
                raise ValueError("expected_revision is required")
        if self.operation_type in {"REPLACE_SECTION", "INSERT_BEFORE_HEADING", "INSERT_AFTER_HEADING"} and not self.expected_section_hash:
            raise ValueError("expected_section_hash is required for bounded section edits")
        return self


class PageReplaceRequest(BaseModel):
    expected_revision: str = Field(min_length=1, max_length=200)
    content: str
    title: str | None = Field(default=None, min_length=1, max_length=300)
    nav_path: str | None = Field(default=None, min_length=1, max_length=1000)
    purpose: str = Field(default="Replace a page through the audited agent shortcut", min_length=1, max_length=4000)


class ChangeAbandonRequest(BaseModel):
    reason: str = Field(default="Abandoned by contributor", min_length=1, max_length=4000)


class ChangeCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class RollbackRequest(BaseModel):
    target_revision: str = Field(min_length=1, max_length=200)
    expected_revision: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="Restore a prior audited page version", min_length=1, max_length=4000)
