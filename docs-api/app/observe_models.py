from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

ObservationKind = Literal[
    "DEPLOYED_VERSION", "GENERATION", "CERTIFICATION", "FRESHNESS_CHECK",
    "TEST", "SOAK_READING", "RUNBOOK_EXERCISED",
]

Outcome = Literal["NOMINAL", "DEGRADED", "FAILED", "UNKNOWN"]


class ObservationCreate(BaseModel):
    subject_entity_id: UUID | None = None
    subject_artifact_id: UUID | None = None
    observation_kind: ObservationKind
    outcome: Outcome = "NOMINAL"
    observed_at: datetime | None = None
    source_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{8,64}$")
    summary: str | None = Field(default=None, max_length=1000)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def exactly_one_subject(self):
        if (self.subject_entity_id is None) == (self.subject_artifact_id is None):
            raise ValueError("exactly one of subject_entity_id or subject_artifact_id is required")
        if len(json.dumps(self.payload, sort_keys=True, ensure_ascii=False, default=str)) > 15000:
            raise ValueError("payload exceeds 15000 bytes")
        return self


class ObservationBatch(BaseModel):
    observations: list[ObservationCreate] = Field(min_length=1, max_length=100)
