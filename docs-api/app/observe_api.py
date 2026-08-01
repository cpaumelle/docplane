"""Observe-domain API: push-only milestone evidence and current status.

DocPlane holds the meter list, not the readings. This surface accepts
observations pushed by agents and automation, projects the latest per
subject x kind, and DERIVES freshness/drift by fingerprint comparison on
read. It never pulls, never scrapes, never stores time series — the test
suite is the enforcement pointer for that invariant.
"""
from __future__ import annotations

import json
import os
from typing import Any
from uuid import UUID

import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from datetime import datetime, timedelta, timezone

from app.agent_auth import Principal, require_contributor
from app.db import get_conn
from app.event_store import append_event
from app.model_contracts import secret_findings
from app.mutation_receipts import load_receipt, receipt_digest, save_receipt
from app.observe_models import ObservationBatch

# Evidence payloads (command output, config fragments) are exactly where
# secrets leak; the same fail-closed policy as model attributes applies.
# Clock skew: a future-dated observation must not dominate the projection.
_MAX_FUTURE_SKEW = timedelta(minutes=5)

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
    digest = receipt_digest({"route": "observations-record", "observations": [item.model_dump(mode="json") for item in request.observations]})
    horizon = datetime.now(timezone.utc) + _MAX_FUTURE_SKEW
    item_errors: list[dict[str, Any]] = []
    for index, item in enumerate(request.observations):
        if item.observed_at is not None and item.observed_at > horizon:
            item_errors.append({"index": index, "code": "OBSERVATION_FROM_THE_FUTURE", "observed_at": item.observed_at.isoformat()})
        findings = secret_findings(item.payload)
        if findings:
            item_errors.append({"index": index, "code": "OBSERVATION_PAYLOAD_SECRET_SHAPED", "findings": findings})
    if item_errors:
        raise HTTPException(status_code=422, detail={"code": "OBSERVATION_BATCH_REJECTED", "errors": item_errors})
    with get_conn() as conn:
        replayed = load_receipt(conn, principal, key, "OBSERVATIONS_RECORD", digest)
        if replayed is not None:
            return replayed
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
        response = {"recorded": recorded, "count": len(recorded)}
        save_receipt(conn, principal, key, "OBSERVATIONS_RECORD", key, digest, response)
        conn.commit()
    return response


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
    """FRESHNESS_CHECK is the ONE authoritative kind for source state: a
    TEST or SOAK_READING carrying an incidental fingerprint must never
    redefine whether generated artifacts appear fresh."""
    cur.execute(
        """
        SELECT source_fingerprint FROM observe.observations
         WHERE subject_entity_id = %s AND observation_kind = 'FRESHNESS_CHECK'
           AND source_fingerprint IS NOT NULL
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


# The page-classification CHECK declares the escalation scale; gaps rank by
# it descending so the most critical unwatched service surfaces first.
_CRITICALITY_RANK = {"NORMAL": 0, "IMPORTANT": 1, "OPERATIONAL_CRITICAL": 2, "POLICY_REQUIRED": 3}

# Severities that page a human (deployment paging convention). A paging alert without a
# runbook_url annotation is an operational gap; informational severities
# merely benefit from one.
_PAGING_SEVERITIES = ("critical", "page")


def plan_gap_reconciliation(
    candidates: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    batch_limit: int,
) -> dict[str, list[dict[str, Any]]]:
    """Pure convergent projection plan.

    Existing identities reopen regardless of the mint cap; only brand-new
    triage load is capped. Resolution is exhaustive so the queue never
    knowingly retains debt that coverage says has disappeared.
    """
    wanted = {(item["gap_kind"], item["subject_entity_id"]): item for item in candidates}
    known = {(item["gap_kind"], item["subject_entity_id"]): item for item in existing}
    resolve = [item for key, item in known.items() if item["status"] == "OPEN" and key not in wanted]
    reopen = [
        wanted[key] for key, item in known.items()
        if item["status"] == "RESOLVED" and key in wanted
    ]
    refresh = [
        wanted[key] for key, item in known.items()
        if item["status"] == "OPEN" and key in wanted
        and (
            item.get("subject_entity_key") != wanted[key].get("subject_entity_key")
            or item.get("page_path") != wanted[key].get("page_path")
            or item.get("briefing") != wanted[key].get("briefing")
        )
    ]
    unseen = [item for key, item in wanted.items() if key not in known]
    return {
        "create": unseen[:batch_limit],
        "reopen": reopen,
        "refresh": refresh,
        "resolve": resolve,
    }


def _coverage_gap_candidates(cur) -> list[dict[str, Any]]:
    """Return gap subjects from the same model attributes as coverage.

    Page paths are importer-owned attributes, not inferred from rendered
    files. Criticality is inherited from WATCHES-linked services' governed
    pages and is used only to order the bounded triage projection.
    """
    cur.execute(
        """
        WITH rule_criticality AS (
            SELECT r.entity_id,
                   max(CASE p.criticality
                         WHEN 'POLICY_REQUIRED' THEN 3
                         WHEN 'OPERATIONAL_CRITICAL' THEN 2
                         WHEN 'IMPORTANT' THEN 1
                         ELSE 0 END) AS rank
              FROM model.entities r
              LEFT JOIN model.entity_links watches
                     ON watches.from_entity_id = r.entity_id
                    AND watches.relation = 'WATCHES'
              LEFT JOIN model.entity_page_links pl
                     ON pl.entity_id = watches.to_entity_id
                    AND pl.relation = 'DESCRIBES'
              LEFT JOIN docs.pages p ON p.resource_id = pl.page_resource_id
             WHERE r.entity_kind = 'MONITOR_RULE' AND r.status = 'ACTIVE'
             GROUP BY r.entity_id
        )
        SELECT r.entity_id::text, r.entity_key, r.display_name,
               r.attributes->>'source_page_path', r.created_at,
               coalesce(rc.rank, 0),
               gap.gap_kind
          FROM model.entities r
          JOIN rule_criticality rc ON rc.entity_id = r.entity_id
          CROSS JOIN LATERAL (
              SELECT 'MISSING_DESCRIPTION'::text AS gap_kind
               WHERE coalesce((r.attributes->>'has_description')::boolean, false) = false
              UNION ALL
              SELECT 'PAGING_ALERT_MISSING_RUNBOOK'::text
               WHERE r.attributes->>'rule_kind' = 'alert'
                 AND r.attributes->>'severity' = ANY(%s)
                 AND coalesce(r.attributes->>'runbook_url', '') = ''
          ) gap
         ORDER BY rc.rank DESC, r.created_at, r.entity_key, gap.gap_kind
        """,
        (list(_PAGING_SEVERITIES),),
    )
    return [
        {
            "subject_entity_id": row[0],
            "subject_entity_key": row[1],
            "rule_name": row[2],
            "page_path": row[3],
            "first_observed_at": row[4],
            "criticality_rank": int(row[5]),
            "gap_kind": row[6],
            "briefing": {
                "gap_kind": row[6],
                "rule": {"entity_id": row[0], "entity_key": row[1], "display_name": row[2]},
                "page_path": row[3],
                "authority": "/api/v1/observe/coverage",
                "runbook_discipline": (
                    "Triage the gap; do not manufacture a runbook. "
                    "Runbooks are born from real operational events."
                ),
            },
        }
        for row in cur.fetchall()
    ]


@router.post("/api/v1/observe/coverage/reconcile-work")
def reconcile_coverage_work(
    batch_limit: int | None = Query(default=None, ge=1, le=100),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    """Project current gaps into a bounded Work queue.

    Coverage remains authoritative for totals. This endpoint creates at most
    ``batch_limit`` new items, while resolving and reopening known identities
    exhaustively. Receipt replay makes every retry mutation-free.
    """
    key = _key(idempotency_key)
    effective_limit = batch_limit or int(os.environ.get("DOCPLANE_COVERAGE_GAP_BATCH_LIMIT", "10"))
    if not 1 <= effective_limit <= 100:
        raise HTTPException(status_code=422, detail={"code": "COVERAGE_GAP_BATCH_LIMIT_INVALID"})
    digest = receipt_digest({"route": "coverage-reconcile-work", "batch_limit": effective_limit})
    with get_conn() as conn:
        # Serialize the global projection even when the table is initially
        # empty (SELECT ... FOR UPDATE cannot lock a row that does not exist).
        lock_cur = conn.cursor()
        lock_cur.execute("SELECT pg_advisory_xact_lock(hashtext('docplane.coverage-gap-reconcile'))")
        replayed = load_receipt(conn, principal, key, "COVERAGE_GAP_RECONCILE", digest)
        if replayed is not None:
            return replayed
        cur = conn.cursor()
        candidates = _coverage_gap_candidates(cur)
        missing_page_links = sorted(
            item["subject_entity_key"] for item in candidates if not item["page_path"]
        )
        if missing_page_links:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "COVERAGE_GAP_PAGE_LINK_MISSING",
                    "count": len(missing_page_links),
                    "subjects": missing_page_links[:20],
                    "truncated": len(missing_page_links) > 20,
                    "remedy": "run the current meter-list importer before reconciling Work",
                },
            )
        cur.execute(
            """
            SELECT work_item_id::text, gap_kind, subject_entity_id::text,
                   subject_entity_key, page_path, briefing, status, version
              FROM work.coverage_gap_items
             ORDER BY first_seen_at, work_item_id
             FOR UPDATE
            """
        )
        existing = [
            dict(zip(
                ("work_item_id", "gap_kind", "subject_entity_id", "subject_entity_key",
                 "page_path", "briefing", "status", "version"),
                row,
            ))
            for row in cur.fetchall()
        ]
        plan = plan_gap_reconciliation(candidates, existing, effective_limit)
        known_keys = {(item["gap_kind"], item["subject_entity_id"]) for item in existing}
        unseen_total = sum(
            1 for item in candidates
            if (item["gap_kind"], item["subject_entity_id"]) not in known_keys
        )
        created_ids: list[str] = []
        for item in plan["create"]:
            cur.execute(
                """
                INSERT INTO work.coverage_gap_items
                    (gap_kind, subject_entity_id, subject_entity_key, page_path,
                     briefing, created_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING work_item_id::text
                """,
                (
                    item["gap_kind"], item["subject_entity_id"], item["subject_entity_key"],
                    item["page_path"], _json(item["briefing"]), principal.principal_id,
                ),
            )
            created_ids.append(cur.fetchone()[0])
        reopened_ids: list[str] = []
        for item in plan["reopen"]:
            cur.execute(
                """
                UPDATE work.coverage_gap_items
                   SET status = 'OPEN', resolved_at = NULL, last_transition_at = now(),
                       page_path = %s, briefing = %s, version = version + 1
                 WHERE gap_kind = %s AND subject_entity_id = %s
                RETURNING work_item_id::text
                """,
                (item["page_path"], _json(item["briefing"]), item["gap_kind"], item["subject_entity_id"]),
            )
            reopened_ids.append(cur.fetchone()[0])
        refreshed_ids: list[str] = []
        for item in plan["refresh"]:
            cur.execute(
                """
                UPDATE work.coverage_gap_items
                   SET subject_entity_key = %s, page_path = %s, briefing = %s,
                       last_transition_at = now(), version = version + 1
                 WHERE gap_kind = %s AND subject_entity_id = %s
                RETURNING work_item_id::text
                """,
                (
                    item["subject_entity_key"], item["page_path"], _json(item["briefing"]),
                    item["gap_kind"], item["subject_entity_id"],
                ),
            )
            refreshed_ids.append(cur.fetchone()[0])
        resolved_ids: list[str] = []
        for item in plan["resolve"]:
            cur.execute(
                """
                UPDATE work.coverage_gap_items
                   SET status = 'RESOLVED', resolved_at = now(),
                       last_transition_at = now(), version = version + 1
                 WHERE work_item_id = %s
                RETURNING work_item_id::text
                """,
                (item["work_item_id"],),
            )
            resolved_ids.append(cur.fetchone()[0])
        response = {
            "created": len(created_ids),
            "reopened": len(reopened_ids),
            "refreshed": len(refreshed_ids),
            "resolved": len(resolved_ids),
            "created_work_item_ids": created_ids,
            "reopened_work_item_ids": reopened_ids,
            "refreshed_work_item_ids": refreshed_ids,
            "resolved_work_item_ids": resolved_ids,
            "batch_limit": effective_limit,
            "coverage_gap_total": len(candidates),
            "remaining_unprojected": max(0, unseen_total - len(created_ids)),
        }
        append_event(
            conn,
            event_type="COVERAGE_GAPS_RECONCILED",
            channel="API",
            producer_id="docplane-observe",
            idempotency_key=f"COVERAGE_GAPS_RECONCILED:{principal.principal_id}:{key}",
            principal=principal,
            workspace_key="work",
            resource_type="COVERAGE_GAP_RECONCILIATION",
            resource_id=key,
            metadata={name: response[name] for name in ("created", "reopened", "refreshed", "resolved", "batch_limit", "coverage_gap_total")},
        )
        save_receipt(conn, principal, key, "COVERAGE_GAP_RECONCILE", key, digest, response)
        conn.commit()
        return response


@router.get("/api/v1/observe/coverage/work-items")
def list_coverage_work(
    status: str = Query(default="OPEN", pattern="^(OPEN|RESOLVED|all)$"),
    limit: int = Query(default=200, ge=1, le=1000),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    predicate = "" if status == "all" else " WHERE status = %s"
    params: list[Any] = [] if status == "all" else [status]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT count(*) FROM work.coverage_gap_items{predicate}", params)
        total = int(cur.fetchone()[0])
        cur.execute(
            """
            SELECT work_item_id::text, gap_kind, subject_entity_id::text,
                   subject_entity_key, page_path, briefing, status,
                   first_seen_at, last_transition_at, resolved_at, version
              FROM work.coverage_gap_items
            """ + predicate + " ORDER BY first_seen_at, work_item_id LIMIT %s",
            [*params, limit],
        )
        items = [
            dict(zip(
                ("work_item_id", "gap_kind", "subject_entity_id", "subject_entity_key",
                 "page_path", "briefing", "status", "first_seen_at", "last_transition_at",
                 "resolved_at", "version"),
                row,
            ))
            for row in cur.fetchall()
        ]
    return {"items": items, "count": len(items), "total": total, "truncated": len(items) < total}


@router.get("/api/v1/observe/coverage")
def observe_coverage(principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    """The meter-list coverage view (Sprint 6, exemplar B): gaps derived from
    the model graph, never stubs. A service is unwatched when no ACTIVE
    MONITOR_RULE has a WATCHES link to it; a rule lacks a description when
    its harvested has_description attribute is false or absent; a paging
    alert lacks a runbook when its rule file carries no runbook_url
    annotation. Service criticality derives from the pages the entity
    DESCRIBES — the graph, not a parallel register — and ranks the gaps.

    The response's scope block versions this surface explicitly: runbook
    gaps here are annotation-presence only. Counting only contract-meeting,
    exercised runbooks and feeding gaps into the work inbox are the
    runbook-discipline follow-up (implementation plan, Sprint 6)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.entity_key,
                   count(l.from_entity_id) FILTER (
                       WHERE r.entity_kind = 'MONITOR_RULE' AND r.status = 'ACTIVE'
                   ) AS watching_rules,
                   (SELECT p.criticality
                      FROM model.entity_page_links pl
                      JOIN docs.pages p ON p.resource_id = pl.page_resource_id
                     WHERE pl.entity_id = s.entity_id AND pl.relation = 'DESCRIBES'
                     ORDER BY CASE p.criticality
                                  WHEN 'POLICY_REQUIRED' THEN 3
                                  WHEN 'OPERATIONAL_CRITICAL' THEN 2
                                  WHEN 'IMPORTANT' THEN 1
                                  ELSE 0
                              END DESC
                     LIMIT 1) AS criticality
              FROM model.entities s
              LEFT JOIN model.entity_links l
                     ON l.to_entity_id = s.entity_id AND l.relation = 'WATCHES'
              LEFT JOIN model.entities r ON r.entity_id = l.from_entity_id
             WHERE s.entity_kind = 'SERVICE' AND s.status = 'ACTIVE'
             GROUP BY s.entity_id, s.entity_key
             ORDER BY s.entity_key
            """
        )
        services = [
            {"entity_key": row[0], "watching_rules": int(row[1]), "criticality": row[2]}
            for row in cur.fetchall()
        ]
        cur.execute(
            """
            SELECT entity_key FROM model.entities
             WHERE entity_kind = 'MONITOR_RULE' AND status = 'ACTIVE'
               AND COALESCE((attributes->>'has_description')::boolean, false) = false
             ORDER BY entity_key
            """
        )
        rules_without_description = [row[0] for row in cur.fetchall()]
        cur.execute(
            """
            SELECT entity_key FROM model.entities
             WHERE entity_kind = 'MONITOR_RULE' AND status = 'ACTIVE'
               AND attributes->>'rule_kind' = 'alert'
               AND attributes->>'severity' = ANY(%s)
               AND COALESCE(attributes->>'runbook_url', '') = ''
             ORDER BY entity_key
            """,
            (list(_PAGING_SEVERITIES),),
        )
        paging_alerts_without_runbook = [row[0] for row in cur.fetchall()]
        cur.execute(
            """
            SELECT r.entity_key FROM model.entities r
             WHERE r.entity_kind = 'MONITOR_RULE' AND r.status = 'ACTIVE'
               AND NOT EXISTS (
                   SELECT 1 FROM model.entity_links l
                    WHERE l.from_entity_id = r.entity_id AND l.relation = 'WATCHES'
               )
             ORDER BY r.entity_key
            """
        )
        rules_without_service = [row[0] for row in cur.fetchall()]
        cur.execute(
            "SELECT count(*) FROM model.entities WHERE entity_kind = 'MONITOR_RULE' AND status = 'ACTIVE'"
        )
        rule_count = int(cur.fetchone()[0])
    unwatched = sorted(
        (item for item in services if item["watching_rules"] == 0),
        key=lambda item: (-_CRITICALITY_RANK.get(item["criticality"] or "", 0), item["entity_key"]),
    )
    return {
        "services": services,
        "unwatched_services": [item["entity_key"] for item in unwatched],
        "rule_count": rule_count,
        "rules_without_description": rules_without_description,
        "paging_alerts_without_runbook": paging_alerts_without_runbook,
        "paging_severities": list(_PAGING_SEVERITIES),
        # The unwired share of the meter list (164 of 256 on first fabric
        # run) is itself coverage truth: a rule nobody can attribute to a
        # service is a labelling gap in the rule files.
        "rules_without_service": rules_without_service,
        "scope": {
            "implemented": [
                "unwatched_services",
                "rules_without_description",
                "paging_alerts_without_runbook",
                "rules_without_service",
                "criticality_ranking",
                "bounded_work_projection",
            ],
            # Additive compatibility: Sprint 6 clients may key on this token.
            # It remains in place even though Sprint 8 delivers the bounded
            # projection; compatibility_status says what changed without
            # deleting or retyping an existing response value.
            "follow_up": ["verified_runbook_gating", "work_inbox_feed"],
            "compatibility_status": {
                "work_inbox_feed": "DELIVERED_AS_BOUNDED_WORK_PROJECTION",
            },
        },
    }


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
