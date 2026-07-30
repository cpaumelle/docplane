"""Replay-safe idempotency for every mutating verb.

The trust API's receipt pattern, generalised: hash the request, retain the
response, replay it byte-for-byte on the same key, and reject a key reused
with different intent. A caller that loses a response after commit replays
with the SAME key and receives the original receipt — never a state-based
error like CAPTURE_ALREADY_TRIAGED.

Usage in a route:

    digest = receipt_digest(payload)
    replayed = load_receipt(conn, principal, key, operation, digest)
    if replayed is not None:
        return replayed
    ... perform the mutation, build `response` ...
    save_receipt(conn, principal, key, operation, resource_ref, digest, response)
    conn.commit()
    return response
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg2.extras
from fastapi import HTTPException

from app.agent_auth import Principal


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def receipt_digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def load_receipt(conn, principal: Principal, key: str, operation: str, digest: str) -> Any | None:
    cur = conn.cursor()
    cur.execute(
        "SELECT operation_type, request_hash, response FROM docplane.mutation_receipts WHERE principal_id = %s AND idempotency_key = %s",
        (principal.principal_id, key),
    )
    row = cur.fetchone()
    if row is None:
        return None
    if row[0] != operation or row[1] != digest:
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED", "original_operation": row[0]})
    return row[2]


def save_receipt(conn, principal: Principal, key: str, operation: str, resource_ref: str | None, digest: str, response: Any) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO docplane.mutation_receipts (principal_id, idempotency_key, operation_type, resource_ref, request_hash, response)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (principal_id, idempotency_key) DO NOTHING
        """,
        (principal.principal_id, key, operation, resource_ref, digest, _json(response)),
    )
