"""Versioned append-only domain-event API.

The core event contract supports audit, integration and cursor-based delta synchronization. Usage
instrumentation, aggregation, retention and traffic analysis are intentionally owned by the dashboard.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.agent_auth import Principal, require_scopes
from app.db import get_conn
from app.event_cursor import decode_cursor, encode_cursor
from app.event_models import EventBatch, EventReceipt
from app.event_store import append_event


router = APIRouter(tags=["events-v1"])


def _effective_producer(principal: Principal, producer_id: str) -> str:
    return f"{principal.principal_id}:{producer_id}"


def _event_dict(row) -> dict[str, Any]:
    keys = (
        "event_seq", "event_id", "occurred_at", "event_type", "principal_id",
        "actor_class", "channel", "producer_id", "client_identity", "workspace_key",
        "resource_type", "resource_id", "page_resource_id", "page_path",
        "page_revision", "release_id", "metadata",
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
                accepted += int(inserted)
                duplicate += int(not inserted)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return EventReceipt(
        accepted=accepted,
        duplicate=duplicate,
        event_ids=event_ids,
        last_cursor=encode_cursor(last_sequence, principal_id=principal.principal_id)
        if last_sequence else None,
    )


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
        "usage_analytics": "dashboard-owned",
    }
