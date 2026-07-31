from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class PageClassificationUpdate(BaseModel):
    expected_metadata_version: int = Field(ge=1)
    workspace_id: UUID
    publication_state: Literal["DRAFT", "PUBLISHED", "ARCHIVED"]
    knowledge_class: Literal[
        "REFERENCE",
        "OPERATION",
        "ARCHITECTURE",
        "DESIGN",
        "DECISION",
        "POLICY",
        "EVIDENCE",
        "WORK_NOTE",
    ] | None
    criticality: Literal["NORMAL", "IMPORTANT", "OPERATIONAL_CRITICAL", "POLICY_REQUIRED"] = (
        "NORMAL"
    )
    owner_principal_id: UUID | None = None
    review_due_at: datetime | None = None
    provenance: Literal["AUTHORED", "GENERATED"] | None = None
    reason: str = Field(min_length=1, max_length=4000)


class PageOwnerUpdate(BaseModel):
    expected_metadata_version: int = Field(ge=1)
    owner_principal_id: UUID
    reason: str = Field(min_length=1, max_length=4000)


class PageVerificationCreate(BaseModel):
    expected_revision: str = Field(min_length=1, max_length=200)
    expires_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def expiry_is_after_now_when_supplied(self):
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return self


class VerificationExpiryRun(BaseModel):
    cutoff: datetime | None = None


class MaintenanceQueueRequest(BaseModel):
    queue: Literal[
        "metadata_review",
        "unowned",
        "unverified",
        "verification_due",
        "expired",
        "outdated",
        "all",
    ] = "all"
    due_within_days: int = Field(default=30, ge=0, le=3650)
    limit: int = Field(default=100, ge=1, le=1000)
