from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class ReorganisationPlanCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    purpose: str = Field(min_length=1, max_length=4000)
    workspace_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    base_state_identity: str | None = Field(default=None, max_length=256)


_OPERATION_TYPES = {
    "MOVE_PAGE",
    "REPARENT_NAV",
    "REORDER_SECTIONS",
    "ADD_REDIRECT",
    "REMOVE_REDIRECT",
    "ARCHIVE_PAGE",
    "RESTORE_PAGE",
}


class ReorganisationOperationCreate(BaseModel):
    operation_type: str
    page_resource_id: UUID | None = None
    expected_revision: str | None = Field(default=None, max_length=200)
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
    def binding_is_complete(self):
        unbound = {"REORDER_SECTIONS", "ADD_REDIRECT", "REMOVE_REDIRECT"}
        if self.operation_type not in unbound:
            if self.page_resource_id is None:
                raise ValueError("page_resource_id is required")
            if not self.expected_revision:
                raise ValueError("expected_revision is required")
        return self


class ReorganisationReviewCreate(BaseModel):
    decision: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]
    reviewed_plan_version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=4000)


class ReorganisationSubmitRequest(BaseModel):
    note: str | None = Field(default=None, max_length=4000)


class ReorganisationPlanView(BaseModel):
    plan_id: UUID
    workspace_key: str
    title: str
    purpose: str
    author_principal_id: UUID
    status: str
    version: int
    base_state_identity: str | None
    analysis_receipt: dict[str, Any] | None
    validation_receipt: dict[str, Any] | None
    linked_change_id: UUID | None
    execution_receipt: dict[str, Any] | None
    certification_receipt: dict[str, Any] | None
    stabilization_receipt: dict[str, Any] | None
    compensating_plan_id: UUID | None
    created_at: Any
    updated_at: Any
    operations: list[dict[str, Any]] = Field(default_factory=list)
    reviews: list[dict[str, Any]] = Field(default_factory=list)
    permitted_actions: list[str] = Field(default_factory=list)
