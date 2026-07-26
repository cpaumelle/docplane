from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class PrincipalCreate(BaseModel):
    principal_kind: Literal["HUMAN", "AGENT", "AUTOMATION"]
    display_name: str = Field(min_length=1, max_length=160)
    owner_principal_id: UUID | None = None
    scopes: list[str]
    description: str | None = Field(default=None, max_length=500)
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PrincipalIssued(BaseModel):
    principal_id: UUID
    token_id: UUID
    token: str
    token_prefix: str
    scopes: list[str]
    expires_at: datetime | None
    warning: str = "The token is shown once. Store it securely; DocPlane persists only its hash."


class PrincipalView(BaseModel):
    principal_id: UUID
    principal_kind: str
    display_name: str
    scopes: list[str]
    token_id: UUID


class CanonicalResolveRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    workspace_key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    intended_title: str | None = Field(default=None, max_length=300)
    intended_path: str | None = Field(default=None, max_length=500)
    creation_reason: str | None = Field(default=None, max_length=2000)


class ChangeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    purpose: str = Field(min_length=1, max_length=4000)
    workspace_key: str = Field(default="default", pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    base_state_identity: str | None = Field(default=None, max_length=256)


_OPERATION_TYPES = {
    "PATCH_METADATA",
    "REPLACE_SECTION",
    "INSERT_BEFORE_HEADING",
    "INSERT_AFTER_HEADING",
    "REPLACE_CONTENT_RANGE",
    "ADD_LINK",
    "REMOVE_LINK",
    "CREATE_FROM_TEMPLATE",
    "MOVE_PAGE",
    "REPARENT_NAV",
    "ARCHIVE_PAGE",
    "RESTORE_PAGE",
    "ADD_REDIRECT",
    "REPLACE_DOCUMENT",
}


class ChangeOperationCreate(BaseModel):
    operation_type: str
    page_resource_id: UUID | None = None
    expected_revision: str | None = Field(default=None, max_length=200)
    expected_section_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    payload: dict[str, Any]
    sequence: int | None = Field(default=None, ge=0)

    @field_validator("operation_type")
    @classmethod
    def operation_type_known(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in _OPERATION_TYPES:
            raise ValueError("unsupported operation_type")
        return normalized

    @model_validator(mode="after")
    def existing_page_operations_are_revision_bound(self):
        if self.operation_type != "CREATE_FROM_TEMPLATE":
            if self.page_resource_id is None:
                raise ValueError("page_resource_id is required for an existing-page operation")
            if not self.expected_revision:
                raise ValueError("expected_revision is required for an existing-page operation")
        return self


class ChangeSubmitRequest(BaseModel):
    note: str | None = Field(default=None, max_length=4000)


class PageReadView(BaseModel):
    resource_id: UUID
    path: str
    title: str
    nav_path: str
    revision: str
    version: int
    status: str
    updated_at: datetime
    updated_by: str
    view: str
    content: str | None = None
    summary: str | None = None
    outline: list[dict[str, Any]] | None = None
    section: dict[str, Any] | None = None
    backlinks: list[dict[str, Any]] | None = None
    permitted_operations: list[str] = Field(default_factory=list)


class ChangeReceipt(BaseModel):
    change_id: UUID
    status: str
    title: str
    purpose: str
    workspace_key: str
    author_principal_id: UUID
    base_state_identity: str | None
    validation_summary: dict[str, Any] | None
    preview_receipt: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    operations: list[dict[str, Any]] = Field(default_factory=list)
