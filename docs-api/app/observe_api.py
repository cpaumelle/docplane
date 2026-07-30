"""Observe-domain API: push-only milestone evidence and current status.

DocPlane holds the meter list, not the readings. This surface accepts
observations pushed by agents and automation, projects the latest per
subject x kind, and DERIVES freshness/drift by fingerprint comparison on
read. It never pulls, never scrapes, never stores time series — the test
suite is the enforcement pointer for that invariant.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.agent_auth import Principal, require_contributor
from app.db import get_conn
from app.event_store import append_event
from app.observe_models import ObservationBatch

router = APIRouter(tags=["observe-v1"])


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def _key(value: str | None) -> str:
    if not value or not value.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    return value.strip()[:256]


def derive_freshness(
    latest_generation: dict[str, Any] | None,
    latest_source_fingerprint: str | None,
) -> dict[str, Any]:
    """Pure derivation: never stored, computed on read.

    fresh    — generated from exactly the fingerprint reality last showed
    drifted  — the source moved since the last successful generation
    failed   — the latest generation attempt failed
    never_generated / unknown — nothing to compare yet
    """
    if latest_generation is None:
        return {"state": "NEVER_GENERATED", "generated_fingerprint": None, "source_fingerprint": latest_source_fingerprint}
    if latest_generation.get("outcome") == "FAILED":
        return {"state": "FAILED", "generated_fingerprint": latest_generation.get("source_fingerprint"), "source_fingerprint": latest_source_fingerprint, "observed_at": latest_generation.get("observed_at")}
    generated = latest_generation.get("source_fingerprint")
    if not generated or not latest_source_fingerprint:
        return {"state": "UNKNOWN", "generated_fingerprint": generated, "source_fingerprint": latest_source_fingerprint, "observed_at": latest_generation.get("observed_at")}
    state = "FRESH" if generated == latest_source_fingerprint else "DRIFTED"
    return {"state": state, "generated_fingerprint": generated, "source_fingerprint": latest_source_fingerprint, "observed_at": latest_generation.get("observed_at")}


_OBSERVATION_COLUMNS = (
    "observation_id", "seq", "observed_at", "recorded_at", "observer_principal_id",
    "subject_entity_id", "subject_artifact_id", "observation_kind", "outcome",
    "source_fingerprint", "summary", "payload",
)


def _observation_select() -> str:
    return (
        "SELECT observation_id, seq, observed_at, recorded_at, observer_principal_id, "
        "subject_entity_id, subject_artifact_id, observation_kind, outcome, "
        "source_fingerprint, summary, payload FROM observe.observations"
    )


def _observation(row) -> dict[str, Any]:
    value = dict(zip(_OBSERVATION_COLUMNS, row))
    for key in ("observation_id", "observer_principal_id", "subject_entity_id", "subject_artifact_id"):
        if value.get(key) is not None:
            value[key] = str(value[key])
    value["seq"] = int(value["seq"])
    value["uri"] = f"docplane://observe/observations/{value['observation_id']}"
    return value


@router.post("/api/v1/observations", status_code=201)
def record_observations(
    request: ObservationBatch,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    with get_conn() as conn:
        cur = conn.cursor()
        entity_ids = {str(item.subject_entity_id) for item in request.observations if item.subject_entity_id}
        artifact_ids = {str(item.subject_artifact_id) for item in request.observations if item.subject_artifact_id}
        missing: list[dict[str, str]] = []
        if entity_ids:
            cur.execute("SELECT entity_id::text FROM model.entities WHERE entity_id = ANY(%s::uuid[])", (list(entity_ids),))
            found = {row[0] for row in cur.fetchall()}
            missing.extend({"subject_entity_id": item} for item in sorted(entity_ids - found))
        if artifact_ids:
            cur.execute("SELECT artifact_id::text FROM model.generated_artifacts WHERE artifact_id = ANY(%s::uuid[])", (list(artifact_ids),))
            found = {row[0] for row in cur.fetchall()}
            missing.extend({"subject_artifact_id": item} for item in sorted(artifact_ids - found))
        if missing:
            raise HTTPException(status_code=404, detail={"code": "OBSERVATION_SUBJECT_NOT_FOUND", "missing": missing})

        recorded: list[dict[str, Any]] = []
        for index, item in enumerate(request.observations):
            item_key = item.idempotency_key or f"{key}:{index}"
            cur.execute(
                """
                INSERT INTO observe.observations
                    (observed_at, observer_principal_id, subject_entity_id, subject_artifact_id,
                     observation_kind, outcome, source_fingerprint, summary, payload, idempotency_key)
                VALUES (coalesce(%s, now()), %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (observer_principal_id, idempotency_key) DO NOTHING
                RETURNING observation_id
                """,
                (
                    item.observed_at, principal.principal_id,
                    str(item.subject_entity_id) if item.subject_entity_id else None,
                    str(item.subject_artifact_id) if item.subject_artifact_id else None,
                    item.observation_kind, item.outcome, item.source_fingerprint,
                    item.summary, _json(item.payload), item_key,
                ),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute("SELECT observation_id FROM observe.observations WHERE observer_principal_id = %s AND idempotency_key = %s", (principal.principal_id, item_key))
                row = cur.fetchone()
                replay = True
            else:
                replay = False
            observation_id = row[0]
            if not replay:
                cur.execute(
                    """
                    INSERT INTO observe.current_status
                        (subject_entity_id, subject_artifact_id, observation_kind, observation_id,
                         outcome, observed_at, source_fingerprint, summary)
                    SELECT subject_entity_id, subject_artifact_id, observation_kind, observation_id,
                           outcome, observed_at, source_fingerprint, summary
                      FROM observe.observations WHERE observation_id = %s
                    ON CONFLICT (subject_entity_id, subject_artifact_id, observation_kind)
                    DO UPDATE SET observation_id = EXCLUDED.observation_id,
                                  outcome = EXCLUDED.outcome,
                                  observed_at = EXCLUDED.observed_at,
                                  source_fingerprint = EXCLUDED.source_fingerprint,
                                  summary = EXCLUDED.summary,
                                  updated_at = now()
                     WHERE EXCLUDED.observed_at >= observe.current_status.observed_at
                    """,
                    (str(observation_id),),
                )
            recorded.append({"observation_id": str(observation_id), "replayed": replay})
        append_event(
            conn,
            event_type="OBSERVATIONS_RECORDED",
            channel="API",
            producer_id="docplane-observe",
            idempotency_key=f"OBSERVATIONS_RECORDED:{principal.principal_id}:{key}",
            principal=principal,
            resource_type="OBSERVATION_BATCH",
            resource_id=key,
            metadata={"count": len(recorded)},
        )
        conn.commit()
    return {"recorded": recorded, "count": len(recorded)}


@router.get("/api/v1/observations")
def list_observations(
    subject_entity_id: UUID | None = None,
    subject_artifact_id: UUID | None = None,
    observation_kind: str | None = None,
    outcome: str | None = None,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    predicates = ["seq > %s"]
    params: list[Any] = [after_seq]
    if subject_entity_id:
        predicates.append("subject_entity_id = %s")
        params.append(str(subject_entity_id))
    if subject_artifact_id:
        predicates.append("subject_artifact_id = %s")
        params.append(str(subject_artifact_id))
    if observation_kind:
        predicates.append("observation_kind = %s")
        params.append(observation_kind)
    if outcome:
        predicates.append("outcome = %s")
        params.append(outcome)
    where = " WHERE " + " AND ".join(predicates)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_observation_select() + where + " ORDER BY seq LIMIT %s", [*params, limit + 1])
        rows = cur.fetchall()
    has_more = len(rows) > limit
    values = [_observation(row) for row in rows[:limit]]
    return {
        "observations": values,
        "count": len(values),
        "has_more": has_more,
        "next_after_seq": values[-1]["seq"] if values and has_more else None,
    }


def _current_status(cur, *, entity_id: str | None = None, artifact_id: str | None = None) -> list[dict[str, Any]]:
    column = "subject_entity_id" if entity_id else "subject_artifact_id"
    cur.execute(
        f"""
        SELECT observation_kind, observation_id::text, outcome, observed_at, source_fingerprint, summary, updated_at
          FROM observe.current_status WHERE {column} = %s ORDER BY observation_kind
        """,
        (entity_id or artifact_id,),
    )
    keys = ("observation_kind", "observation_id", "outcome", "observed_at", "source_fingerprint", "summary", "updated_at")
    return [dict(zip(keys, row)) for row in cur.fetchall()]


def _latest_source_fingerprint(cur, entity_id: str) -> str | None:
    cur.execute(
        """
        SELECT source_fingerprint FROM observe.observations
         WHERE subject_entity_id = %s AND source_fingerprint IS NOT NULL
         ORDER BY observed_at DESC, seq DESC LIMIT 1
        """,
        (entity_id,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _latest_generation(cur, artifact_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT outcome, source_fingerprint, observed_at FROM observe.observations
         WHERE subject_artifact_id = %s AND observation_kind = 'GENERATION'
         ORDER BY observed_at DESC, seq DESC LIMIT 1
        """,
        (artifact_id,),
    )
    row = cur.fetchone()
    return dict(zip(("outcome", "source_fingerprint", "observed_at"), row)) if row else None


@router.get("/api/v1/model/entities/{entity_id}/status")
def entity_status(entity_id: UUID, principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT entity_kind, entity_key FROM model.entities WHERE entity_id = %s", (str(entity_id),))
        entity = cur.fetchone()
        if entity is None:
            raise HTTPException(status_code=404, detail={"code": "MODEL_ENTITY_NOT_FOUND"})
        status = _current_status(cur, entity_id=str(entity_id))
        cur.execute(
            "SELECT artifact_id::text, artifact_key FROM model.generated_artifacts WHERE source_entity_id = %s AND status = 'DECLARED' ORDER BY artifact_key",
            (str(entity_id),),
        )
        artifacts = []
        source_fingerprint = _latest_source_fingerprint(cur, str(entity_id))
        for artifact_id, artifact_key in cur.fetchall():
            artifacts.append({
                "artifact_id": artifact_id,
                "artifact_key": artifact_key,
                "freshness": derive_freshness(_latest_generation(cur, artifact_id), source_fingerprint),
            })
    return {
        "entity_id": str(entity_id),
        "entity_kind": entity[0],
        "entity_key": entity[1],
        "current_status": status,
        "generated_artifacts": artifacts,
    }


@router.get("/api/v1/model/artifacts/{artifact_id}/status")
def artifact_status(artifact_id: UUID, principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT artifact_key, source_entity_id::text FROM model.generated_artifacts WHERE artifact_id = %s", (str(artifact_id),))
        artifact = cur.fetchone()
        if artifact is None:
            raise HTTPException(status_code=404, detail={"code": "MODEL_ARTIFACT_NOT_FOUND"})
        status = _current_status(cur, artifact_id=str(artifact_id))
        freshness = derive_freshness(
            _latest_generation(cur, str(artifact_id)),
            _latest_source_fingerprint(cur, artifact[1]),
        )
    return {
        "artifact_id": str(artifact_id),
        "artifact_key": artifact[0],
        "source_entity_id": artifact[1],
        "current_status": status,
        "freshness": freshness,
    }
