from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class EventIn(BaseModel):
    event_type: str = Field(min_length=2, max_length=64, pattern=r"^[A-Z][A-Z0-9_]+$")
    occurred_at: datetime | None = None
    channel: Literal["WEB", "API", "MCP", "SEARCH", "AI", "CLI", "WEBHOOK", "SYSTEM"]
    idempotency_key: str = Field(min_length=1, max_length=256)
    client_identity: str | None = Field(default=None, max_length=200)
    workspace_key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    resource_type: str | None = Field(default=None, max_length=64)
    resource_id: str | None = Field(default=None, max_length=300)
    page_resource_id: UUID | None = None
    page_path: str | None = Field(default=None, max_length=500)
    page_revision: str | None = Field(default=None, max_length=200)
    release_id: str | None = Field(default=None, max_length=300)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def metadata_is_safe_and_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        forbidden = {"password", "secret", "token", "api_key", "authorization", "dsn", "prompt"}
        collisions = sorted(forbidden & {str(key).lower() for key in value})
        if collisions:
            raise ValueError("metadata contains forbidden keys: " + ", ".join(collisions))
        return value


class EventBatch(BaseModel):
    producer_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$")
    events: list[EventIn] = Field(min_length=1, max_length=100)


class EventReceipt(BaseModel):
    accepted: int
    duplicate: int
    event_ids: list[UUID]
    last_cursor: str | None


class RestrictedPayloadIn(BaseModel):
    event_id: UUID
    payload_kind: Literal["SEARCH_QUERY", "AI_QUESTION", "FEEDBACK_TEXT"]
    payload: dict[str, Any]
    expires_at: datetime


class EventView(BaseModel):
    event_id: UUID
    occurred_at: datetime
    event_type: str
    principal_id: UUID | None
    actor_class: str
    channel: str
    producer_id: str
    client_identity: str | None
    workspace_key: str | None
    resource_type: str | None
    resource_id: str | None
    page_resource_id: UUID | None
    page_path: str | None
    page_revision: str | None
    release_id: str | None
    metadata: dict[str, Any]
