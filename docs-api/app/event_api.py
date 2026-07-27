"""Versioned append-only domain-event API.

The core event contract supports audit, integration and cursor-based delta synchronization. Usage
instrumentation, aggregation, retention and traffic analysis are intentionally owned by the dashboard.
The event API therefore exposes product-domain evidence, not page-traffic analytics.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.agent_auth import Principal, require_scopes
from app.db import get_conn
from app.event_cursor import CursorError, decode_cursor, encode_cursor
from app.event_models import EventBatchCreate
from app.event_store import append_event, read_events

router = APIRouter(prefix="/api/v1", tags=["events"])


@router.post("/events", status_code=202)
def create_events(
    payload: EventBatchCreate,
    principal: Principal = Depends(require_scopes("events:write")),
) -> dict[str, Any]:
    receipts = []
    with get_conn() as conn:
        for event in payload.events:
            receipts.append(
                append_event(
                    conn,
                    event_type=event.event_type,
                    principal=principal,
                    channel=event.channel,
                    producer_id=event.producer_id,
                    idempotency_key=event.idempotency_key,
                    client_identity=event.client_identity,
                    workspace_key=event.workspace_key,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    page_resource_id=event.page_resource_id,
                    page_path=event.page_path,
                    page_revision=event.page_revision,
                    release_id=event.release_id,
                    metadata=event.metadata,
                )
            )
        conn.commit()
    return {"events": receipts}


@router.get("/events")
def list_events(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    workspace_key: str | None = Query(default=None),
    principal: Principal = Depends(require_scopes("events:subscribe")),
) -> dict[str, Any]:
    after_seq = 0
    if cursor:
        try:
            decoded = decode_cursor(cursor, principal.principal_id)
        except CursorError as exc:
            raise exc.to_http_exception() from exc
        after_seq = decoded.after_seq

    with get_conn() as conn:
        events = read_events(
            conn,
            after_seq=after_seq,
            limit=limit,
            workspace_key=workspace_key,
        )

    next_after = events[-1]["event_seq"] if events else after_seq
    return {
        "events": events,
        "next_cursor": encode_cursor(principal.principal_id, next_after),
        "analytics_owner": "dashboard-owned",
    }
