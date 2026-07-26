from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class WorkspaceCreate(BaseModel):
    workspace_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    name: str = Field(min_length=1, max_length=200)
    workspace_kind: Literal["REFERENCE", "OPERATIONS", "WORK"]
    visibility: Literal["PRIVATE", "INTERNAL", "PUBLIC"] = "INTERNAL"
    mutation_policy: Literal["PROPOSAL_REQUIRED", "DIRECT_LOW_RISK", "DIRECT_ALLOWED"] = (
        "PROPOSAL_REQUIRED"
    )
    default_verification_days: int | None = Field(default=None, ge=1, le=3650)
    retention_policy: dict[str, Any] = Field(default_factory=dict)


class MembershipSet(BaseModel):
    principal_id: UUID
    role: Literal["READER", "PROPOSER", "EDITOR", "REVIEWER", "MERGER", "OWNER"]


class InitiativeCreate(BaseModel):
    initiative_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")
    workspace_id: UUID
    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=5000)
    scope: str | None = Field(default=None, max_length=10000)
    work_state: Literal[
        "BACKLOG", "ACTIVE", "BLOCKED", "SOAKING", "PAUSED", "PARKED"
    ] = "BACKLOG"
    priority: Literal["LOW", "NORMAL", "HIGH", "CRITICAL"] = "NORMAL"
    target_date: date | None = None
    review_due_at: datetime | None = None
    blocker_summary: str | None = Field(default=None, max_length=4000)
    soak_started_at: datetime | None = None
    soak_review_at: datetime | None = None
    soak_success_criteria: str | None = Field(default=None, max_length=10000)
    soak_failure_conditions: str | None = Field(default=None, max_length=10000)
    parked_reason: str | None = Field(default=None, max_length=4000)
    parked_review_at: datetime | None = None
    parked_indefinitely: bool = False

    @model_validator(mode="after")
    def state_contracts(self):
        if self.work_state == "BLOCKED" and not (self.blocker_summary or "").strip():
            raise ValueError("BLOCKED requires blocker_summary")
        if self.work_state == "SOAKING":
            required = (
                self.soak_started_at,
                self.soak_review_at,
                (self.soak_success_criteria or "").strip(),
                (self.soak_failure_conditions or "").strip(),
            )
            if not all(required):
                raise ValueError(
                    "SOAKING requires soak_started_at, soak_review_at, success criteria and failure conditions"
                )
        if self.work_state == "PARKED":
            if not (self.parked_reason or "").strip():
                raise ValueError("PARKED requires parked_reason")
            if self.parked_review_at is None and not self.parked_indefinitely:
                raise ValueError("PARKED requires parked_review_at or parked_indefinitely=true")
        return self


class WorkTransition(BaseModel):
    expected_version: int = Field(ge=1)
    work_state: Literal[
        "BACKLOG",
        "ACTIVE",
        "BLOCKED",
        "SOAKING",
        "PAUSED",
        "PARKED",
        "COMPLETE",
        "ABANDONED",
    ]
    review_due_at: datetime | None = None
    blocker_summary: str | None = Field(default=None, max_length=4000)
    soak_started_at: datetime | None = None
    soak_review_at: datetime | None = None
    soak_success_criteria: str | None = Field(default=None, max_length=10000)
    soak_failure_conditions: str | None = Field(default=None, max_length=10000)
    parked_reason: str | None = Field(default=None, max_length=4000)
    parked_review_at: datetime | None = None
    parked_indefinitely: bool = False
    note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def target_contract(self):
        if self.work_state == "BLOCKED" and not (self.blocker_summary or "").strip():
            raise ValueError("BLOCKED requires blocker_summary")
        if self.work_state == "SOAKING":
            if not all(
                (
                    self.soak_started_at,
                    self.soak_review_at,
                    (self.soak_success_criteria or "").strip(),
                    (self.soak_failure_conditions or "").strip(),
                )
            ):
                raise ValueError(
                    "SOAKING requires soak_started_at, soak_review_at, success criteria and failure conditions"
                )
        if self.work_state == "PARKED":
            if not (self.parked_reason or "").strip():
                raise ValueError("PARKED requires parked_reason")
            if self.parked_review_at is None and not self.parked_indefinitely:
                raise ValueError("PARKED requires parked_review_at or parked_indefinitely=true")
        return self


class ActivityCreate(BaseModel):
    activity_type: Literal[
        "NOTE",
        "OBSERVATION",
        "DECISION_REQUIRED",
        "HANDOFF",
        "SOAK_OBSERVATION",
        "BLOCKER",
        "RESOLUTION",
    ]
    body: str = Field(min_length=1, max_length=20000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InitiativeLinkCreate(BaseModel):
    relation: Literal[
        "CONTEXT",
        "EVIDENCE",
        "DECISION",
        "RUNBOOK",
        "BLOCKED_BY",
        "PROMOTES_TO",
        "CHANGE_PROPOSAL",
        "CATALOG_SNAPSHOT",
    ]
    resource_type: Literal["PAGE", "INITIATIVE", "CHANGE", "CATALOG", "EXTERNAL"]
    resource_id: str = Field(min_length=1, max_length=1000)


class PromotionUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    promotion_state: Literal["NOT_READY", "READY", "IN_PROGRESS", "PROMOTED", "NOT_REQUIRED"]
    promotion_change_id: UUID | None = None

    @model_validator(mode="after")
    def promoted_requires_change(self):
        if self.promotion_state == "PROMOTED" and self.promotion_change_id is None:
            raise ValueError("PROMOTED requires promotion_change_id")
        return self
