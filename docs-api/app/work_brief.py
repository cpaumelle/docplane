"""Deterministic WORK checkpoints. No inference, filesystem state or model calls.

The latest typed checkpoint is an immutable initiative activity. Updates merge
named items into it; omission never deletes an item or releases a hold. Existing
activities remain evidence, not implicit changes to the checkpoint.
"""
from __future__ import annotations

import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT = "work-brief-v1"
MAX_ITEMS = 50
MAX_SNAPSHOT_BYTES = 65536
TERMINAL = {"DONE_IN_RECORD", "SUPERSEDED", "WITHDRAWN"}


class WorkEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reference_type: Literal["ACTIVITY", "CAPTURE", "PR", "COMMIT", "ARTIFACT", "PAGE", "CONDITION"]
    reference_id: str = Field(min_length=1, max_length=300)


class WorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    item_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,95}$")
    title: str = Field(min_length=1, max_length=300)
    state: Literal["OPEN", "DONE_IN_RECORD", "UNCLEAR", "SUPERSEDED", "WITHDRAWN"] = "OPEN"
    stance: Literal["ACTIVE", "HELD", "DEFERRED", "UNKNOWN"] = "ACTIVE"
    next_action: str | None = Field(default=None, min_length=1, max_length=1000)
    owner: str | None = Field(default=None, min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)
    references: list[WorkEvidence] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def actionable(self):
        if self.state == "OPEN" and self.stance == "ACTIVE" and not self.next_action:
            raise ValueError("active open work requires a next action")
        return self


class HoldDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=1000)
    references: list[WorkEvidence] = Field(min_length=1, max_length=10)


class WorkChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: WorkItem
    decision: HoldDecision | None = None


class WorkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_context: str = Field(pattern=r"^[a-f0-9]{64}$")
    summary: str = Field(min_length=1, max_length=4000)
    reported_producer: str = Field(min_length=1, max_length=120)
    changes: list[WorkChange] = Field(min_length=1, max_length=10)

    @field_validator("changes")
    @classmethod
    def unique_items(cls, changes):
        ids = [change.item.item_id for change in changes]
        if len(set(ids)) != len(ids):
            raise ValueError("one change per item")
        return changes


def merge_items(previous: list[dict], update: WorkUpdate) -> list[dict]:
    """Preserve untouched work. A hold change must name its decision evidence.

    Decision evidence is attributable recorded intent, never an execution grant.
    DONE_IN_RECORD is a claim with cited evidence, never live verification.
    """
    items = {item["item_id"]: item for item in previous}
    for change in update.changes:
        item = change.item.model_dump(mode="json")
        old = items.get(item["item_id"])
        if old is not None:
            changed_posture = (old["state"], old["stance"]) != (item["state"], item["stance"])
            if changed_posture and (old["stance"] in {"HELD", "DEFERRED"} or old["state"] in TERMINAL):
                if change.decision is None:
                    raise HTTPException(409, detail={"code": "WORK_DECISION_REQUIRED", "item_id": item["item_id"]})
            # Re-reporting progress must not silently replace the decision's
            # source or explanation while retaining the same HELD label.
            if old["stance"] in {"HELD", "DEFERRED"} and (
                old["reason"] != item["reason"] or old["references"] != item["references"]
            ) and change.decision is None:
                raise HTTPException(409, detail={"code": "WORK_DECISION_REQUIRED", "item_id": item["item_id"]})
        items[item["item_id"]] = item
    result = list(items.values())
    if len(result) > MAX_ITEMS or len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(413, detail={"code": "WORK_CHECKPOINT_LIMIT", "max_items": MAX_ITEMS,
                                         "max_bytes": MAX_SNAPSHOT_BYTES})
    return result


def group_items(items: list[dict], initiative_state: str) -> dict:
    """Explicit state only: neither similarity nor recency can close work."""
    groups = {name: [] for name in ("next_actions", "held", "deferred", "uncertain", "recorded_done")}
    for item in items:
        if item["state"] in TERMINAL:
            group = "recorded_done"
        elif item["stance"] == "HELD":
            group = "held"
        elif item["stance"] == "DEFERRED":
            group = "deferred"
        elif item["state"] == "UNCLEAR" or item["stance"] == "UNKNOWN":
            group = "uncertain"
        else:
            group = "next_actions"
        groups[group].append(item)
    # An item does not override an initiative's parked/blocked/closed state.
    eligible = initiative_state in {"ACTIVE", "BACKLOG"}
    groups["execution_eligible"] = eligible
    groups["next_actions"] = groups["next_actions"] if eligible else []
    groups["paused_actions"] = [item for item in items if item["state"] == "OPEN" and item["stance"] == "ACTIVE"] if not eligible else []
    return groups
