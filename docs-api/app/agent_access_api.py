"""Credential admission endpoints for managed and private-fabric deployments."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2.extras
from fastapi import APIRouter, HTTPException, Request, status

from app.access_policy import current_access_policy
from app.agent_auth import Principal, issue_token
from app.agent_models import SelfIssueRequest, SelfIssuedPrincipalToken
from app.db import get_conn
from app.event_store import append_event


router = APIRouter(tags=["docplane-auth"])
_SELF_ISSUE_MODE = "private_fabric_self_service"


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def _request_evidence(request: Request) -> dict[str, str]:
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")[:512]
    observed = (forwarded.split(",", 1)[0].strip() or peer)[:128]
    user_agent = request.headers.get("user-agent", "")[:512]
    fingerprint = hashlib.sha256(observed.encode("utf-8")).hexdigest()
    return {
        "observed_source": observed,
        "source_fingerprint": fingerprint,
        "proxy_peer": peer[:128],
        "forwarded_chain": forwarded,
        "user_agent": user_agent,
    }


def _rate_limit(cur, *, fingerprint: str, source_limit: int, global_limit: int) -> None:
    # Serialize the check+insert window so parallel cold starts cannot exceed the
    # configured ceiling by racing one another.
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('docplane-private-fabric-self-issue'))")
    cur.execute(
        """
        SELECT count(*)
          FROM docplane.principals
         WHERE principal_kind = 'AGENT'
           AND metadata->>'issuance_mode' = %s
           AND created_at > now() - interval '1 hour'
        """,
        (_SELF_ISSUE_MODE,),
    )
    if int(cur.fetchone()[0]) >= global_limit:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "SELF_ISSUE_RATE_LIMITED",
                "message": "The private-fabric credential issuance ceiling was reached.",
                "remedy": "Reuse an existing unexpired token or retry after the one-hour window.",
            },
            headers={"Retry-After": "3600"},
        )
    cur.execute(
        """
        SELECT count(*)
          FROM docplane.principals
         WHERE principal_kind = 'AGENT'
           AND metadata->>'issuance_mode' = %s
           AND metadata->>'source_fingerprint' = %s
           AND created_at > now() - interval '1 hour'
        """,
        (_SELF_ISSUE_MODE, fingerprint),
    )
    if int(cur.fetchone()[0]) >= source_limit:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "SELF_ISSUE_RATE_LIMITED",
                "message": "This observed fabric source reached its credential issuance ceiling.",
                "remedy": "Reuse an existing unexpired token or retry after the one-hour window.",
            },
            headers={"Retry-After": "3600"},
        )


@router.post(
    "/api/v1/auth/self-issue",
    response_model=SelfIssuedPrincipalToken,
    status_code=status.HTTP_201_CREATED,
)
def self_issue_agent_token(
    request: Request,
    body: SelfIssueRequest | None = None,
) -> SelfIssuedPrincipalToken:
    policy = current_access_policy()
    if not policy.self_service:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "SELF_ISSUE_DISABLED",
                "message": "Self-service credential issuance is disabled for this deployment.",
                "remedy": "Follow the token_acquisition procedure in /.well-known/docplane.json.",
            },
        )

    evidence = _request_evidence(request)
    clear, digest, prefix = issue_token()
    display_name = (
        body.display_name.strip()
        if body is not None and body.display_name
        else f"fabric-agent-{prefix[-6:]}"
    )
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=policy.self_issue_ttl_seconds)
    metadata: dict[str, Any] = {
        "access_profile": policy.profile,
        "issuance_mode": _SELF_ISSUE_MODE,
        **evidence,
    }
    if body is not None and body.client_context:
        metadata["client_context"] = body.client_context.strip()

    with get_conn() as conn:
        cur = conn.cursor()
        _rate_limit(
            cur,
            fingerprint=evidence["source_fingerprint"],
            source_limit=policy.source_limit_per_hour,
            global_limit=policy.global_limit_per_hour,
        )
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
            (
                principal_id,
                digest,
                prefix,
                "Private-fabric self-issued AGENT contributor token",
                expires_at,
            ),
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
            event_type="AGENT_CREDENTIAL_SELF_ISSUED",
            channel="API",
            producer_id="docplane-auth-self-issue",
            idempotency_key=principal_id,
            principal=principal,
            client_identity=display_name,
            resource_type="principal",
            resource_id=principal_id,
            metadata={
                "access_profile": policy.profile,
                "expires_at": expires_at.isoformat(),
                "token_prefix": prefix,
                **evidence,
            },
        )
        conn.commit()

    return SelfIssuedPrincipalToken(
        principal_id=principal_id,
        display_name=display_name,
        principal_kind="AGENT",
        token=clear,
        token_prefix=prefix,
        expires_at=expires_at,
        access_profile=policy.profile,
        issued_via="fabric_reachability",
    )
