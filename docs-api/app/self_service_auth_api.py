"""Switchable self-service authentication for private-fabric deployments."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2.extras
from fastapi import APIRouter, Header, HTTPException, Request

from app.agent_auth import Principal, access_profile, issue_token
from app.agent_models import PrincipalToken, SelfIssueRequest
from app.db import get_conn
from app.event_store import append_event

router = APIRouter(tags=["docplane-auth"])


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value)


def _positive_int(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 1 or value > maximum:
        raise RuntimeError(f"{name} must be between 1 and {maximum}")
    return value


def _source(request: Request, source_ip: str | None) -> tuple[str, str | None, str | None]:
    proxy_peer = request.client.host if request.client else "unknown"
    forwarded_for = request.headers.get("x-forwarded-for")
    user_agent = request.headers.get("user-agent")
    return (source_ip or proxy_peer).strip()[:200], forwarded_for, user_agent


@router.post("/api/v1/auth/self-issue", response_model=PrincipalToken, status_code=201)
def self_issue(
    body: SelfIssueRequest,
    request: Request,
    fabric_admission: str | None = Header(default=None, alias="X-DocPlane-Fabric-Admission"),
    source_ip: str | None = Header(default=None, alias="X-DocPlane-Source-IP"),
) -> PrincipalToken:
    """Issue one short-lived contributor token when fabric position is the admission boundary."""
    if access_profile() != "private_fabric":
        raise HTTPException(status_code=404, detail={"code": "SELF_SERVICE_AUTH_DISABLED"})
    if fabric_admission != "1":
        raise HTTPException(
            status_code=403,
            detail={"code": "FABRIC_ADMISSION_REQUIRED", "message": "use the routed private-fabric endpoint"},
        )

    ttl_hours = _positive_int("DOCPLANE_SELF_ISSUE_TTL_HOURS", 24, 24)
    source, forwarded_for, user_agent = _source(request, source_ip)
    per_source_limit = _positive_int("DOCPLANE_SELF_ISSUE_PER_SOURCE_HOURLY", 10, 1000)
    global_limit = _positive_int("DOCPLANE_SELF_ISSUE_GLOBAL_HOURLY", 100, 10000)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    display_name = (body.display_name or f"agent@{source}").strip()

    clear, digest, prefix = issue_token()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
              count(*) FILTER (WHERE metadata->>'source' = %s),
              count(*)
              FROM docplane.events
             WHERE event_type = 'AGENT_TOKEN_SELF_ISSUED'
               AND occurred_at >= now() - interval '1 hour'
            """,
            (source,),
        )
        source_count, global_count = (int(value) for value in cur.fetchone())
        if source_count >= per_source_limit or global_count >= global_limit:
            raise HTTPException(
                status_code=429,
                detail={"code": "SELF_SERVICE_RATE_LIMITED", "retry_after_seconds": 3600},
                headers={"Retry-After": "3600"},
            )

        metadata = {
            "issued_via": "private_fabric_self_service",
            "source": source,
            "proxy_peer": request.client.host if request.client else None,
            "forwarded_for": forwarded_for,
            "user_agent": user_agent,
            "ttl_hours": ttl_hours,
        }
        cur.execute(
            """
            INSERT INTO docplane.principals
                (principal_kind, display_name, status, metadata)
            VALUES ('AGENT', %s, 'ACTIVE', %s)
            RETURNING principal_id::text
            """,
            (display_name, _json(metadata)),
        )
        principal_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO docplane.api_tokens
                (principal_id, token_hash, token_prefix, description, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING token_id::text
            """,
            (principal_id, digest, prefix, "Private-fabric self-issued agent token", expires_at),
        )
        token_id = cur.fetchone()[0]
        principal = Principal(
            principal_id=principal_id,
            principal_kind="AGENT",
            display_name=display_name,
            token_id=token_id,
        )
        append_event(
            conn,
            event_type="AGENT_TOKEN_SELF_ISSUED",
            channel="HTTP",
            producer_id="docplane-auth",
            idempotency_key=token_id,
            principal=principal,
            client_identity=display_name,
            resource_type="PRINCIPAL",
            resource_id=principal_id,
            metadata=metadata,
        )
        conn.commit()

    return PrincipalToken(
        principal_id=principal_id,
        display_name=display_name,
        principal_kind="AGENT",
        token=clear,
        token_prefix=prefix,
        expires_at=expires_at,
    )
