"""Durable WORK projection for generated-artifact health conditions.

These are machine-reconcilable conditions, not human planning initiatives and
not monitoring-specific coverage gaps. One artifact/kind pair keeps a stable
identity across open, resolve and reopen cycles. Exact reconciliation is
zero-write when the semantic condition set and briefings are unchanged.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.agent_auth import Principal, require_contributor
from app.db import get_conn
from app.event_store import append_event
from app.model_contracts import secret_findings
from app.mutation_receipts import load_receipt, receipt_digest, save_receipt

router = APIRouter(tags=["work-v1"])


class GeneratedArtifactCondition(BaseModel):
    condition_kind: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    briefing: dict[str, Any] = Field(default_factory=dict)


class GeneratedArtifactConditionSet(BaseModel):
    conditions: list[GeneratedArtifactCondition] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def exact_kinds(self):
        kinds = [item.condition_kind for item in self.conditions]
        if len(kinds) != len(set(kinds)):
            raise ValueError("condition_kind values must be unique")
        return self


def _key(value: str | None) -> str:
    if not value or not value.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    return value.strip()[:256]


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def _condition(row) -> dict[str, Any]:
    keys = (
        "condition_id", "artifact_id", "condition_kind", "briefing", "status",
        "created_by", "updated_by", "first_seen_at", "last_transition_at",
        "resolved_at", "version", "updated_at",
    )
    value = dict(zip(keys, row))
    for key in ("condition_id", "artifact_id", "created_by", "updated_by"):
        value[key] = str(value[key])
    value["version"] = int(value["version"])
    value["uri"] = f"docplane://work/generated-artifact-conditions/{value['condition_id']}"
    return value


def _select() -> str:
    return (
        "SELECT condition_id, artifact_id, condition_kind, briefing, status, created_by, updated_by, "
        "first_seen_at, last_transition_at, resolved_at, version, updated_at "
        "FROM work.generated_artifact_conditions"
    )


def _validate_briefings(request: GeneratedArtifactConditionSet) -> None:
    errors: list[dict[str, Any]] = []
    for item in request.conditions:
        findings = secret_findings(item.briefing)
        if findings:
            errors.append({
                "condition_kind": item.condition_kind,
                "code": "WORK_CONDITION_BRIEFING_SECRET_SHAPED",
                "findings": findings,
            })
        encoded = json.dumps(item.briefing, sort_keys=True, default=str).encode("utf-8")
        if len(encoded) > 8192:
            errors.append({"condition_kind": item.condition_kind, "code": "WORK_CONDITION_BRIEFING_TOO_LARGE"})
    if errors:
        raise HTTPException(status_code=422, detail={"code": "WORK_CONDITIONS_REJECTED", "errors": errors})


@router.get("/api/v1/work/generated-artifacts/{artifact_id}/conditions")
def list_generated_artifact_conditions(
    artifact_id: UUID,
    status: str = Query(default="OPEN"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    if status not in {"OPEN", "RESOLVED", "all"}:
        raise HTTPException(status_code=422, detail={"code": "WORK_CONDITION_STATUS_INVALID"})
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM model.generated_artifacts WHERE artifact_id = %s", (str(artifact_id),))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail={"code": "MODEL_ARTIFACT_NOT_FOUND"})
        predicate = "" if status == "all" else " AND status = %s"
        params: list[Any] = [str(artifact_id)]
        if status != "all":
            params.append(status)
        cur.execute(_select() + " WHERE artifact_id = %s" + predicate + " ORDER BY condition_kind", params)
        values = [_condition(row) for row in cur.fetchall()]
    return {"artifact_id": str(artifact_id), "conditions": values, "count": len(values)}


@router.put("/api/v1/work/generated-artifacts/{artifact_id}/conditions")
def reconcile_generated_artifact_conditions(
    artifact_id: UUID,
    request: GeneratedArtifactConditionSet,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    """Reconcile the complete currently-true condition set for one artifact."""
    key = _key(idempotency_key)
    _validate_briefings(request)
    desired = {item.condition_kind: item.briefing for item in request.conditions}
    digest = receipt_digest({
        "route": "work-generated-artifact-conditions-exact-set",
        "artifact_id": str(artifact_id),
        "conditions": [
            {"condition_kind": kind, "briefing": desired[kind]}
            for kind in sorted(desired)
        ],
    })

    with get_conn() as conn:
        replayed = load_receipt(conn, principal, key, "WORK_GENERATED_ARTIFACT_CONDITIONS_EXACT_SET", digest)
        if replayed is not None:
            return replayed

        cur = conn.cursor()
        cur.execute(
            "SELECT status FROM model.generated_artifacts WHERE artifact_id = %s FOR UPDATE",
            (str(artifact_id),),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail={"code": "MODEL_ARTIFACT_NOT_FOUND"})

        cur.execute(_select() + " WHERE artifact_id = %s FOR UPDATE", (str(artifact_id),))
        current = {value["condition_kind"]: value for value in (_condition(row) for row in cur.fetchall())}

        opened: list[str] = []
        reopened: list[str] = []
        refreshed: list[str] = []
        resolved: list[str] = []
        continuing: list[str] = []

        for kind in sorted(desired):
            briefing = desired[kind]
            existing = current.get(kind)
            if existing is None:
                cur.execute(
                    """
                    INSERT INTO work.generated_artifact_conditions
                        (artifact_id, condition_kind, briefing, created_by, updated_by)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (str(artifact_id), kind, _json(briefing), principal.principal_id, principal.principal_id),
                )
                opened.append(kind)
                continue
            if existing["status"] == "RESOLVED":
                cur.execute(
                    """
                    UPDATE work.generated_artifact_conditions
                       SET briefing = %s, status = 'OPEN', resolved_at = NULL,
                           updated_by = %s, version = version + 1,
                           last_transition_at = now(), updated_at = now()
                     WHERE condition_id = %s
                    """,
                    (_json(briefing), principal.principal_id, existing["condition_id"]),
                )
                reopened.append(kind)
                continue
            if existing["briefing"] != briefing:
                cur.execute(
                    """
                    UPDATE work.generated_artifact_conditions
                       SET briefing = %s, updated_by = %s, version = version + 1,
                           updated_at = now()
                     WHERE condition_id = %s
                    """,
                    (_json(briefing), principal.principal_id, existing["condition_id"]),
                )
                refreshed.append(kind)
            else:
                continuing.append(kind)

        for kind in sorted(set(current) - set(desired)):
            existing = current[kind]
            if existing["status"] != "OPEN":
                continue
            cur.execute(
                """
                UPDATE work.generated_artifact_conditions
                   SET status = 'RESOLVED', resolved_at = now(), updated_by = %s,
                       version = version + 1, last_transition_at = now(), updated_at = now()
                 WHERE condition_id = %s
                """,
                (principal.principal_id, existing["condition_id"]),
            )
            resolved.append(kind)

        changed = bool(opened or reopened or refreshed or resolved)
        response = {
            "artifact_id": str(artifact_id),
            "desired_condition_kinds": sorted(desired),
            "opened": opened,
            "reopened": reopened,
            "refreshed": refreshed,
            "resolved": resolved,
            "continuing": continuing,
            "changed": changed,
        }

        if changed:
            append_event(
                conn,
                event_type="WORK_GENERATED_ARTIFACT_CONDITIONS_RECONCILED",
                channel="API",
                producer_id="docplane-work",
                idempotency_key=f"WORK_GENERATED_ARTIFACT_CONDITIONS_RECONCILED:{principal.principal_id}:{key}",
                principal=principal,
                workspace_key="work",
                resource_type="GENERATED_ARTIFACT",
                resource_id=str(artifact_id),
                metadata={
                    "opened": opened,
                    "reopened": reopened,
                    "refreshed": refreshed,
                    "resolved": resolved,
                    "continuing_count": len(continuing),
                },
            )

        save_receipt(
            conn,
            principal,
            key,
            "WORK_GENERATED_ARTIFACT_CONDITIONS_EXACT_SET",
            str(artifact_id),
            digest,
            response,
        )
        conn.commit()
        return response
