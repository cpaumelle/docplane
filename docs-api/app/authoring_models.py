from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class AuthoringPreviewRequest(BaseModel):
    page_resource_id: UUID | None = None
    expected_revision: str | None = Field(default=None, max_length=128)
    scope: Literal["document", "section"] = "document"
    heading_id: str | None = Field(default=None, min_length=1, max_length=200)
    content: str = Field(max_length=2_000_000)

    @model_validator(mode="after")
    def validate_scope(self) -> "AuthoringPreviewRequest":
        if self.page_resource_id is not None and not self.expected_revision:
            raise ValueError("expected_revision is required when previewing an existing page")
        if self.scope == "section":
            if self.page_resource_id is None:
                raise ValueError("section preview requires page_resource_id")
            if not self.heading_id:
                raise ValueError("section preview requires heading_id")
        return self


class AuthoringDiagnostic(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    line: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AuthoringPreviewResponse(BaseModel):
    contract_version: str = "human-authoring-preview-v1"
    source_content: str
    candidate_content: str
    source_hash: str
    candidate_hash: str
    base_revision: str | None = None
    page_resource_id: UUID | None = None
    path: str | None = None
    title: str | None = None
    workspace_key: str | None = None
    scope: Literal["document", "section"]
    heading_id: str | None = None
    rendered_html: str
    preview_sandbox_required: bool = True
    raw_diff: str
    semantic_diff: list[dict[str, Any]]
    outline: list[dict[str, Any]]
    diagnostics: list[AuthoringDiagnostic]
    operation: dict[str, Any]
    source_fidelity: dict[str, Any]
