"""Append-only domain event persistence shared by product endpoints and integrations."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg2.extras

from app.agent_auth import Principal


_ACTOR_CLASS = {
    "HUMAN": "HUMAN",
    "AGENT": "AGENT",
    "AUTOMATION": "AUTOMATION",
}


def actor_class(principal: Principal | None) -> str:
    if principal is None:
        return "ANONYMOUS"
    return _ACTOR_CLASS.get(principal.principal_kind, "AUTOMATION")


def append_event(
    conn,
    *,
    event_type: str,
    channel: str,
    producer_id: str,
    idempotency_key: str,
    principal: Principal | None = None,
    occurred_at: datetime | None = None,
    client_identity: str | None = None,
    workspace_key: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    page_resource_id: str | None = None,
    page_path: str | None = None,
    page_revision: str | None = None,
    release_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[str, int, bool]:
    """Append one event or return the original receipt for an idempotent retry."""
    cur = conn.cursor()
    event_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO docplane.events
            (event_id, occurred_at, event_type, principal_id, actor_class, channel,
             producer_id, client_identity, workspace_key, resource_type, resource_id,
             page_resource_id, page_path, page_revision, release_id, idempotency_key, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (producer_id, idempotency_key) DO NOTHING
        RETURNING event_id::text, event_seq
        """,
        (
            event_id,
            occurred_at or datetime.now(timezone.utc),
            event_type,
            principal.principal_id if principal else None,
            actor_class(principal),
            channel,
            producer_id,
            client_identity,
            workspace_key,
            resource_type,
            resource_id,
            page_resource_id,
            page_path,
            page_revision,
            release_id,
            idempotency_key,
            psycopg2.extras.Json(metadata or {}, dumps=lambda value: json.dumps(value, sort_keys=True)),
        ),
    )
    inserted = cur.fetchone()
    if inserted is not None:
        return inserted[0], int(inserted[1]), True
    cur.execute(
        "SELECT event_id::text, event_seq FROM docplane.events WHERE producer_id = %s AND idempotency_key = %s",
        (producer_id, idempotency_key),
    )
    existing = cur.fetchone()
    if existing is None:
        raise RuntimeError("idempotent event disappeared after conflict")
    return existing[0], int(existing[1]), False
