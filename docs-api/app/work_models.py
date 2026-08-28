from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkspaceCreate(BaseModel):
    workspace_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    name: str = Field(min_length=1, max_length=200)
    workspace_kind: Literal["REFERENCE", "OPERATIONS", "WORK"]
    visibility: Literal["PRIVATE", "INTERNAL", "PUBLIC"] = "INTERNAL"
    default_verification_days: int | None = Field(default=None, ge=1, le=3650)
    retention_policy: dict[str, Any] = Field(default_factory=dict)


class InitiativeCreate(BaseModel):
    initiative_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")
    workspace_id: UUID
    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=5000)
    scope: str | None = Field(default=None, max_length=10000)
    work_state: Literal["BACKLOG", "ACTIVE", "BLOCKED", "SOAKING", "PAUSED", "PARKED"] = "BACKLOG"
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
        _validate_state(self)
        return self


class WorkTransition(BaseModel):
    expected_version: int = Field(ge=1)
    work_state: Literal["BACKLOG", "ACTIVE", "BLOCKED", "SOAKING", "PAUSED", "PARKED", "COMPLETE", "ABANDONED"]
    review_due_at: datetime | None = None
    blocker_summary: str | None = Field(default=None, max_length=4000)
    soak_started_at: datetime | None = None
    soak_review_at: datetime | None = None
    soak_success_criteria: str | None = Field(default=None, max_length=10000)
    soak_failure_conditions: str | None = Field(default=None, max_length=10000)
    soak_monitoring_ref: str | None = Field(default=None, max_length=2000)
    parked_reason: str | None = Field(default=None, max_length=4000)
    parked_review_at: datetime | None = None
    parked_indefinitely: bool = False
    model_disposition: Literal["UPDATED", "NOT_REQUIRED", "DEFERRED"] | None = None
    model_disposition_note: str | None = Field(default=None, max_length=4000)
    observe_disposition: Literal["UPDATED", "NOT_REQUIRED", "DEFERRED"] | None = None
    observe_disposition_note: str | None = Field(default=None, max_length=4000)
    note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def state_contracts(self):
        _validate_state(self)
        return self


class DispositionsUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    model_disposition: Literal["UPDATED", "NOT_REQUIRED", "DEFERRED"] | None = None
    model_disposition_note: str | None = Field(default=None, max_length=4000)
    observe_disposition: Literal["UPDATED", "NOT_REQUIRED", "DEFERRED"] | None = None
    observe_disposition_note: str | None = Field(default=None, max_length=4000)
    soak_monitoring_ref: str | None = Field(default=None, max_length=2000)


class ActivityReference(BaseModel):
    """Bounded, typed evidence reference; never arbitrary activity metadata."""

    model_config = ConfigDict(extra="forbid")

    reference_type: Literal["ACTIVITY", "CONDITION", "ARTIFACT", "PR", "COMMIT"]
    reference_id: str = Field(min_length=1, max_length=300)

    @field_validator("reference_id", mode="before")
    @classmethod
    def strip_reference_id(cls, value):
        # Normalise before Field(min_length=1) so whitespace cannot become a
        # persisted empty identifier after validation.
        return value.strip() if isinstance(value, str) else value


class ActivityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_type: Literal["NOTE", "OBSERVATION", "DECISION_REQUIRED", "HANDOFF", "SOAK_OBSERVATION", "BLOCKER", "RESOLUTION"]
    title: str | None = Field(default=None, min_length=1, max_length=300)
    classification: str | None = Field(default=None, min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    references: list[ActivityReference] = Field(default_factory=list, max_length=20)

    @field_validator("title", "classification", "body", mode="before")
    @classmethod
    def strip_activity_strings(cls, value):
        # Explicit whitespace-only optional values are rejected after this
        # normalisation; omission/None remains the sole representation of
        # "not supplied" for title and classification.
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def bounded_utf8(self):
        limits = {"title": 600, "classification": 400, "body": 20000}
        for field, limit in limits.items():
            value = getattr(self, field)
            if value is not None and len(value.encode("utf-8")) > limit:
                raise ValueError(f"{field} exceeds UTF-8 byte limit")
        references = json.dumps(
            [item.model_dump(mode="json") for item in self.references],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(references) > 8192:
            raise ValueError("references exceed UTF-8 byte limit")
        return self


class InitiativeLinkCreate(BaseModel):
    relation: Literal[
        "CONTEXT", "EVIDENCE", "DECISION", "RUNBOOK", "BLOCKED_BY", "PROMOTES_TO",
        "CHANGE", "CATALOG_SNAPSHOT",
        "IMPLEMENTS", "CONCERNS", "PRODUCES", "UPDATES",
    ]
    resource_type: Literal[
        "PAGE", "INITIATIVE", "CHANGE", "CATALOG", "EXTERNAL",
        "MODEL_ENTITY", "ARTIFACT", "OBSERVATION",
    ]
    resource_id: str = Field(min_length=1, max_length=1000)


class InitiativeDependencyCreate(BaseModel):
    depends_on_initiative_id: UUID
    dependency_kind: Literal["REQUIRES", "BLOCKED_BY", "RELATED"] = "REQUIRES"


class CaptureCreate(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
    kind: Literal["IDEA", "NEXT_ACTION", "FINDING", "QUESTION", "BUG", "IMPROVEMENT"] = "IDEA"
    origin: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def origin_bounded(self):
        if len(json.dumps(self.origin, sort_keys=True, default=str)) > 8000:
            raise ValueError("origin context exceeds 8000 bytes")
        return self


class CapturePromote(BaseModel):
    workspace_id: UUID | None = None
    initiative_key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")
    title: str | None = Field(default=None, min_length=1, max_length=300)
    objective: str | None = Field(default=None, min_length=1, max_length=5000)
    priority: Literal["LOW", "NORMAL", "HIGH", "CRITICAL"] = "NORMAL"
    note: str | None = Field(default=None, max_length=4000)


class CaptureAttach(BaseModel):
    initiative_id: UUID
    activity_type: Literal["NOTE", "OBSERVATION", "DECISION_REQUIRED", "HANDOFF", "SOAK_OBSERVATION", "BLOCKER", "RESOLUTION"] = "NOTE"
    note: str | None = Field(default=None, max_length=4000)


class CaptureDiscard(BaseModel):
    note: str | None = Field(default=None, max_length=4000)


class PromotionUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    promotion_state: Literal["NOT_READY", "READY", "IN_PROGRESS", "PROMOTED", "NOT_REQUIRED"]
    promotion_change_id: UUID | None = None

    @model_validator(mode="after")
    def promoted_requires_change(self):
        if self.promotion_state == "PROMOTED" and self.promotion_change_id is None:
            raise ValueError("PROMOTED requires promotion_change_id")
        return self


def closure_gate_errors(
    promotion_state: str,
    model_disposition: str | None,
    model_note: str | None,
    observe_disposition: str | None,
    observe_note: str | None,
    *,
    has_model_evidence: bool = False,
    has_observe_evidence: bool = False,
) -> list[dict[str, str]]:
    """The keystone gate, as a pure function: finishing an initiative must
    answer one disposition per durable domain — and UPDATED is evidence-bound,
    not honor-system: it requires a resolvable typed link to what changed.
    (know=UPDATED is evidence-bound already: PROMOTED carries a real change id
    enforced by foreign key.) Structured and machine-readable so a blocked
    agent learns exactly what is missing."""
    missing: list[dict[str, str]] = []
    if promotion_state not in {"PROMOTED", "NOT_REQUIRED"}:
        missing.append({
            "domain": "know",
            "code": "PROMOTION_UNRESOLVED",
            "current": promotion_state,
            "remedy": "PUT /api/v1/initiatives/{id}/promotion with PROMOTED (and the change id) or NOT_REQUIRED.",
        })
    evidence = {"model": has_model_evidence, "observe": has_observe_evidence}
    evidence_remedy = {
        "model": "Add an initiative link with resource_type MODEL_ENTITY or ARTIFACT referencing what changed (links are existence-checked).",
        "observe": "Add an initiative link with resource_type OBSERVATION referencing the recorded evidence (links are existence-checked).",
    }
    for domain, disposition, note in (("model", model_disposition, model_note), ("observe", observe_disposition, observe_note)):
        if disposition is None:
            missing.append({
                "domain": domain,
                "code": "DISPOSITION_UNANSWERED",
                "remedy": f"Set {domain}_disposition to UPDATED, NOT_REQUIRED or DEFERRED via PUT /api/v1/initiatives/{{id}}/dispositions or on the COMPLETE transition.",
            })
        elif disposition == "UPDATED" and not evidence[domain]:
            missing.append({
                "domain": domain,
                "code": "DISPOSITION_EVIDENCE_REQUIRED",
                "current": disposition,
                "remedy": evidence_remedy[domain],
            })
        elif disposition in {"NOT_REQUIRED", "DEFERRED"} and not (note or "").strip():
            missing.append({
                "domain": domain,
                "code": "DISPOSITION_NOTE_REQUIRED",
                "current": disposition,
                "remedy": f"{disposition} requires a one-line {domain}_disposition_note; DEFERRED mints a visible inbox gap.",
            })
    return missing


def soak_gate_errors(soak_monitoring_ref: str | None, has_observability_link: bool) -> list[dict[str, str]]:
    """A thing that cannot be observed cannot be soaked: entering SOAKING
    requires the soak criteria to reference real monitoring — a named
    rule/dashboard reference, or a link to a model entity, artifact or
    observation."""
    if (soak_monitoring_ref or "").strip() or has_observability_link:
        return []
    return [{
        "code": "SOAK_OBSERVABILITY_MISSING",
        "remedy": "Provide soak_monitoring_ref (a named alert/dashboard) or add an initiative link with resource_type MODEL_ENTITY, ARTIFACT or OBSERVATION before entering SOAKING.",
    }]


def _validate_state(value) -> None:
    if value.work_state == "BLOCKED" and not (value.blocker_summary or "").strip():
        raise ValueError("BLOCKED requires blocker_summary")
    if value.work_state == "SOAKING" and not all(
        (
            value.soak_started_at,
            value.soak_review_at,
            (value.soak_success_criteria or "").strip(),
            (value.soak_failure_conditions or "").strip(),
        )
    ):
        raise ValueError("SOAKING requires dates, success criteria and failure conditions")
    if value.work_state == "PARKED":
        if not (value.parked_reason or "").strip():
            raise ValueError("PARKED requires parked_reason")
        if value.parked_review_at is None and not value.parked_indefinitely:
            raise ValueError("PARKED requires parked_review_at or parked_indefinitely=true")
