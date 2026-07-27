from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class ReorganisationPlanCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    purpose: str = Field(min_length=1, max_length=4000)
    workspace_key: str = Field(default="reference", pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    base_state_identity: str | None = Field(default=None, max_length=256)


_TYPES = {
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
    def known(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in _TYPES:
            raise ValueError("unsupported reorganisation operation")
        return normalized

    @model_validator(mode="after")
    def exact_binding(self):
        if self.operation_type not in {"REORDER_SECTIONS", "ADD_REDIRECT", "REMOVE_REDIRECT"}:
            if self.page_resource_id is None or not self.expected_revision:
                raise ValueError("page_resource_id and expected_revision are required")
        return self
