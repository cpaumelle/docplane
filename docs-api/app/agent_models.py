from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator
from app.model_models import GeneratedOwnershipPlan


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


class PrincipalTokenIssue(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    expires_at: datetime | None = None

    @field_validator("description")
    @classmethod
    def description_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value.strip()

    @field_validator("expires_at")
    @classmethod
    def expiry_is_future(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        normalized = value.astimezone(timezone.utc)
        if normalized <= datetime.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        return normalized


class PrincipalTokenMetadata(BaseModel):
    token_id: UUID
    token_prefix: str
    description: str | None
    issued_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    status: Literal["ACTIVE", "EXPIRED", "REVOKED"]


class PrincipalTokenIssueResponse(PrincipalTokenMetadata):
    principal_id: UUID
    token: str | None = None
    bearer_returned: bool
    replayed: bool


class PrincipalTokenListResponse(BaseModel):
    principal_id: UUID
    display_name: str
    principal_kind: str
    principal_status: str
    tokens: list[PrincipalTokenMetadata]
    count: int
    truncated: bool


class PrincipalTokenRevokeResponse(PrincipalTokenMetadata):
    principal_id: UUID
    replayed: bool


class SelfIssueRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    client_context: str | None = Field(default=None, min_length=1, max_length=500)


class SelfIssuedPrincipalToken(PrincipalToken):
    access_profile: Literal["private_fabric"]
    issued_via: Literal["fabric_reachability"]


class ChangeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    purpose: str = Field(min_length=1, max_length=4000)
    workspace_key: str = Field(default="reference", pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    base_state_identity: str | None = Field(default=None, max_length=256)
    generated_ownership_plan: GeneratedOwnershipPlan | None = None


_OPERATION_TYPES = {
    "CREATE_PAGE",
    "REPLACE_DOCUMENT",
    "PATCH_TEXT",
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


class TextPatchEdit(BaseModel):
    old_text: str = Field(min_length=1, max_length=262144)
    new_text: str = Field(max_length=262144)
    expected_occurrences: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def changes_text(self):
        if self.old_text == self.new_text:
            raise ValueError("old_text and new_text must differ")
        return self


class PagePatchRequest(BaseModel):
    expected_revision: str = Field(min_length=1, max_length=200)
    edits: list[TextPatchEdit] = Field(min_length=1, max_length=100)
    purpose: str = Field(default="Patch a page through the audited agent shortcut", min_length=1, max_length=4000)

    @model_validator(mode="after")
    def bounded_payload(self):
        total = sum(len(edit.old_text) + len(edit.new_text) for edit in self.edits)
        if total > 1048576:
            raise ValueError("combined patch text exceeds 1 MiB")
        return self


class ChangeAbandonRequest(BaseModel):
    reason: str = Field(default="Abandoned by contributor", min_length=1, max_length=4000)


class ChangeCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class RollbackRequest(BaseModel):
    target_revision: str = Field(min_length=1, max_length=200)
    expected_revision: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="Restore a prior audited page version", min_length=1, max_length=4000)
