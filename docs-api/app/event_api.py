"""Versioned append-only events and usage analytics APIs."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.agent_auth import Principal, require_scopes
from app.db import get_conn
from app.event_cursor import decode_cursor, encode_cursor
from app.event_models import EventBatch, EventReceipt, EventView, RestrictedPayloadIn
from app.event_store import append_event, append_restricted_payload


router = APIRouter(tags=["events-v1", "analytics-v1"])

_USAGE_TYPES = (
    "PAGE_VIEW",
    "PAGE_READ_API",
    "PAGE_READ_MCP",
    "SEARCH_RESULT_CLICKED",
    "AI_PAGE_CITED",
)


def _effective_producer(principal: Principal, producer_id: str) -> str:
    return f"{principal.principal_id}:{producer_id}"


def _event_dict(row) -> dict[str, Any]:
    keys = (
        "event_seq",
        "event_id",
        "occurred_at",
        "event_type",
        "principal_id",
        "actor_class",
        "channel",
        "producer_id",
        "client_identity",
        "workspace_key",
        "resource_type",
        "resource_id",
        "page_resource_id",
        "page_path",
        "page_revision",
        "release_id",
        "metadata",
    )
    value = dict(zip(keys, row))
    for key in ("event_id", "principal_id", "page_resource_id"):
        if value[key] is not None:
            value[key] = str(value[key])
    return value


@router.post("/api/v1/events/batch", response_model=EventReceipt, status_code=202)
def ingest_events(
    request: EventBatch,
    principal: Principal = Depends(require_scopes("events:write")),
) -> EventReceipt:
    event_ids: list[str] = []
    accepted = 0
    duplicate = 0
    last_sequence = 0
    producer = _effective_producer(principal, request.producer_id)
    with get_conn() as conn:
        try:
            for event in request.events:
                event_id, sequence, inserted = append_event(
                    conn,
                    event_type=event.event_type,
                    channel=event.channel,
                    producer_id=producer,
                    idempotency_key=event.idempotency_key,
                    principal=principal,
                    occurred_at=event.occurred_at,
                    client_identity=event.client_identity,
                    workspace_key=event.workspace_key,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    page_resource_id=str(event.page_resource_id) if event.page_resource_id else None,
                    page_path=event.page_path,
                    page_revision=event.page_revision,
                    release_id=event.release_id,
                    metadata=event.metadata,
                )
                event_ids.append(event_id)
                last_sequence = max(last_sequence, sequence)
                if inserted:
                    accepted += 1
                else:
                    duplicate += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return EventReceipt(
        accepted=accepted,
        duplicate=duplicate,
        event_ids=event_ids,
        last_cursor=encode_cursor(last_sequence, principal_id=principal.principal_id)
        if last_sequence
        else None,
    )


@router.post("/api/v1/events/restricted-payload", status_code=201)
def store_restricted_payload(
    request: RestrictedPayloadIn,
    principal: Principal = Depends(require_scopes("events:write")),
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if request.expires_at <= now:
        raise HTTPException(
            status_code=422,
            detail={"code": "RESTRICTED_PAYLOAD_EXPIRY_INVALID", "message": "expires_at must be in the future"},
        )
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT principal_id::text FROM docplane.events WHERE event_id = %s",
            (str(request.event_id),),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "EVENT_NOT_FOUND"})
        if row[0] != principal.principal_id and "analytics:read" not in principal.scopes:
            raise HTTPException(status_code=403, detail={"code": "EVENT_ACCESS_DENIED"})
        inserted = append_restricted_payload(
            conn,
            event_id=str(request.event_id),
            payload_kind=request.payload_kind,
            payload=request.payload,
            expires_at=request.expires_at,
        )
        conn.commit()
    return {
        "event_id": request.event_id,
        "stored": inserted,
        "expires_at": request.expires_at,
        "restricted": True,
    }


@router.get("/api/v1/events")
def list_events(
    after: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    event_type: str | None = Query(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$"),
    workspace_key: str | None = Query(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$"),
    principal: Principal = Depends(require_scopes("events:subscribe")),
) -> dict[str, Any]:
    sequence = decode_cursor(after, principal_id=principal.principal_id)
    predicates = ["event_seq > %s"]
    params: list[Any] = [sequence]
    if event_type:
        predicates.append("event_type = %s")
        params.append(event_type)
    if workspace_key:
        predicates.append("workspace_key = %s")
        params.append(workspace_key)
    params.append(limit)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT event_seq, event_id, occurred_at, event_type, principal_id,
                   actor_class, channel, producer_id, client_identity, workspace_key,
                   resource_type, resource_id, page_resource_id, page_path,
                   page_revision, release_id, metadata
              FROM docplane.events
             WHERE {' AND '.join(predicates)}
             ORDER BY event_seq
             LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()
    events = [_event_dict(row) for row in rows]
    next_sequence = events[-1]["event_seq"] if events else sequence
    for event in events:
        event.pop("event_seq", None)
    return {
        "events": events,
        "count": len(events),
        "next_cursor": encode_cursor(next_sequence, principal_id=principal.principal_id),
        "contract_version": "events-v1",
        "permission_filter": "workspace RBAC will narrow this stream when workspace policy lands",
    }


def _window(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


@router.get("/api/v1/analytics/overview")
def analytics_overview(
    days: int = Query(default=30, ge=1, le=365),
    principal: Principal = Depends(require_scopes("analytics:read")),
) -> dict[str, Any]:
    since = _window(days)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT actor_class, channel, event_type, count(*) AS events,
                   count(DISTINCT principal_id) FILTER (WHERE principal_id IS NOT NULL) AS principals,
                   max(occurred_at) AS last_event_at
              FROM docplane.events
             WHERE occurred_at >= %s
             GROUP BY actor_class, channel, event_type
             ORDER BY events DESC, actor_class, channel, event_type
            """,
            (since,),
        )
        rows = cur.fetchall()
        cur.execute(
            "SELECT count(*), min(occurred_at), max(occurred_at) FROM docplane.events WHERE occurred_at >= %s",
            (since,),
        )
        total, first_event, last_event = cur.fetchone()
    return {
        "window_days": days,
        "since": since,
        "total_events": total,
        "first_event_at": first_event,
        "last_event_at": last_event,
        "breakdown": [
            {
                "actor_class": row[0],
                "channel": row[1],
                "event_type": row[2],
                "events": row[3],
                "unique_principals": row[4],
                "last_event_at": row[5],
            }
            for row in rows
        ],
    }


@router.get("/api/v1/analytics/pages")
def page_usage(
    days: int = Query(default=30, ge=1, le=365),
    actor_class: str | None = Query(default=None, pattern=r"^(HUMAN|AGENT|AUTOMATION|ANONYMOUS)$"),
    order: str = Query(default="desc", pattern=r"^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=500),
    principal: Principal = Depends(require_scopes("analytics:read")),
) -> dict[str, Any]:
    since = _window(days)
    predicates = ["e.occurred_at >= %s", "e.event_type = ANY(%s)", "e.page_resource_id IS NOT NULL"]
    params: list[Any] = [since, list(_USAGE_TYPES)]
    if actor_class:
        predicates.append("e.actor_class = %s")
        params.append(actor_class)
    direction = "ASC" if order == "asc" else "DESC"
    params.append(limit)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT e.page_resource_id::text,
                   coalesce(max(p.path), max(e.page_path)) AS path,
                   max(p.title) AS title,
                   count(*) AS events,
                   count(*) FILTER (WHERE e.actor_class = 'HUMAN') AS human_events,
                   count(*) FILTER (WHERE e.actor_class = 'AGENT') AS agent_events,
                   count(*) FILTER (WHERE e.actor_class = 'AUTOMATION') AS automation_events,
                   count(DISTINCT e.principal_id) FILTER (WHERE e.principal_id IS NOT NULL) AS principals,
                   max(e.occurred_at) AS last_used_at
              FROM docplane.events e
              LEFT JOIN docs.pages p ON p.resource_id = e.page_resource_id
             WHERE {' AND '.join(predicates)}
             GROUP BY e.page_resource_id
             ORDER BY events {direction}, path
             LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()
    return {
        "window_days": days,
        "actor_class": actor_class,
        "order": order,
        "count": len(rows),
        "pages": [
            {
                "page_resource_id": row[0],
                "path": row[1],
                "title": row[2],
                "events": row[3],
                "human_events": row[4],
                "agent_events": row[5],
                "automation_events": row[6],
                "unique_principals": row[7],
                "last_used_at": row[8],
            }
            for row in rows
        ],
        "interpretation": "Traffic is review evidence, not an archive or quality verdict.",
    }


@router.get("/api/v1/analytics/pages/{page_resource_id}")
def page_usage_detail(
    page_resource_id: UUID,
    days: int = Query(default=90, ge=1, le=730),
    principal: Principal = Depends(require_scopes("analytics:read")),
) -> dict[str, Any]:
    since = _window(days)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT path, title, status, revision FROM docs.pages WHERE resource_id = %s",
            (str(page_resource_id),),
        )
        page = cur.fetchone()
        cur.execute(
            """
            SELECT occurred_at::date, actor_class, channel, event_type,
                   count(*), count(DISTINCT principal_id) FILTER (WHERE principal_id IS NOT NULL),
                   max(occurred_at)
              FROM docplane.events
             WHERE page_resource_id = %s AND occurred_at >= %s
             GROUP BY occurred_at::date, actor_class, channel, event_type
             ORDER BY occurred_at::date DESC, actor_class, channel, event_type
            """,
            (str(page_resource_id), since),
        )
        rows = cur.fetchall()
    if page is None and not rows:
        raise HTTPException(status_code=404, detail={"code": "PAGE_USAGE_NOT_FOUND"})
    return {
        "page_resource_id": page_resource_id,
        "page": None
        if page is None
        else {"path": page[0], "title": page[1], "status": page[2], "revision": page[3]},
        "window_days": days,
        "daily": [
            {
                "date": row[0],
                "actor_class": row[1],
                "channel": row[2],
                "event_type": row[3],
                "events": row[4],
                "unique_principals": row[5],
                "last_event_at": row[6],
            }
            for row in rows
        ],
    }


@router.get("/api/v1/analytics/search-gaps")
def search_gaps(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=500),
    principal: Principal = Depends(require_scopes("analytics:read")),
) -> dict[str, Any]:
    since = _window(days)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT metadata->>'query_fingerprint' AS query_fingerprint,
                   count(*) AS occurrences,
                   max(occurred_at) AS last_seen_at,
                   count(DISTINCT principal_id) FILTER (WHERE principal_id IS NOT NULL) AS principals
              FROM docplane.events
             WHERE event_type = 'SEARCH_ZERO_RESULTS'
               AND occurred_at >= %s
               AND metadata ? 'query_fingerprint'
             GROUP BY metadata->>'query_fingerprint'
             ORDER BY occurrences DESC, last_seen_at DESC
             LIMIT %s
            """,
            (since, limit),
        )
        rows = cur.fetchall()
    return {
        "window_days": days,
        "count": len(rows),
        "gaps": [
            {
                "query_fingerprint": row[0],
                "occurrences": row[1],
                "last_seen_at": row[2],
                "unique_principals": row[3],
            }
            for row in rows
        ],
        "restricted_query_text": "available only through a separately authorized short-retention payload surface",
    }


@router.get("/api/v1/analytics/retention")
def analytics_retention(
    principal: Principal = Depends(require_scopes("analytics:read")),
) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT raw_event_days, restricted_payload_days, daily_aggregate_days, updated_at,
                   updated_by_principal_id::text
              FROM analytics.retention_policy
             WHERE id = TRUE
            """
        )
        row = cur.fetchone()
        cur.execute(
            "SELECT count(*), min(occurred_at), max(occurred_at) FROM docplane.events"
        )
        count, oldest, newest = cur.fetchone()
        cur.execute(
            "SELECT count(*), min(expires_at), max(expires_at) FROM analytics.restricted_payloads"
        )
        restricted_count, earliest_expiry, latest_expiry = cur.fetchone()
    return {
        "policy": {
            "raw_event_days": row[0],
            "restricted_payload_days": row[1],
            "daily_aggregate_days": row[2],
            "updated_at": row[3],
            "updated_by_principal_id": row[4],
        },
        "raw_events": {"count": count, "oldest": oldest, "newest": newest},
        "restricted_payloads": {
            "count": restricted_count,
            "earliest_expiry": earliest_expiry,
            "latest_expiry": latest_expiry,
        },
    }
