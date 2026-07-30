"""Credential admission endpoints for managed and private-fabric deployments."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2.extras
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.access_policy import current_access_policy
from app.agent_auth import Principal, issue_token
from app.agent_models import SelfIssueRequest, SelfIssuedPrincipalToken
from app.db import get_conn
from app.event_store import append_event

router = APIRouter(tags=["docplane-auth"])
_SELF_ISSUE_MODE = "private_fabric_self_service"
_HOUR_SECONDS = 60 * 60
log = logging.getLogger(__name__)


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def _request_evidence(request: Request, admitted_source: str | None) -> dict[str, str]:
    peer = request.client.host if request.client else "unknown"
    observed = (admitted_source or peer).strip()[:128]
    user_agent = request.headers.get("user-agent", "")[:512]
    fingerprint = hashlib.sha256(observed.encode("utf-8")).hexdigest()
    return {
        "observed_source": observed,
        "source_fingerprint": fingerprint,
        "proxy_peer": peer[:128],
        "user_agent": user_agent,
    }


def _usage(
    cur,
    *,
    window_seconds: int,
    fingerprint: str | None = None,
) -> tuple[int, int]:
    source_filter = ""
    params: list[Any] = [window_seconds, _SELF_ISSUE_MODE]
    if fingerprint is not None:
        source_filter = "AND metadata->>'source_fingerprint' = %s"
        params.append(fingerprint)
    params.append(window_seconds)
    cur.execute(
        f"""
        SELECT count(*),
               COALESCE(
                   GREATEST(
                       1,
                       CEIL(EXTRACT(EPOCH FROM (
                           min(created_at) + make_interval(secs => %s) - now()
                       )))::integer
                   ),
                   1
               )
          FROM docplane.principals
         WHERE principal_kind = 'AGENT'
           AND metadata->>'issuance_mode' = %s
           {source_filter}
           AND created_at > now() - make_interval(secs => %s)
        """,
        tuple(params),
    )
    count, retry_after = cur.fetchone()
    return int(count), int(retry_after)


def _enforce_limit(
    *,
    scope: str,
    count: int,
    limit: int,
    window_seconds: int,
    retry_after: int,
    fingerprint: str,
) -> None:
    if count < limit:
        return
    log.warning(
        "event=self_issue_rate_limited scope=%s count=%d limit=%d "
        "window_seconds=%d retry_after_seconds=%d source_fingerprint=%s",
        scope,
        count,
        limit,
        window_seconds,
        retry_after,
        fingerprint[:12],
    )
    raise HTTPException(
        status_code=429,
        detail={
            "code": "SELF_ISSUE_RATE_LIMITED",
            "message": "The private-fabric credential issuance limit was reached.",
            "remedy": (
                "Reuse an existing unexpired token. If none is available, retry after "
                f"{retry_after} seconds as directed by the Retry-After header."
            ),
            "scope": scope,
            "limit": limit,
            "window_seconds": window_seconds,
            "retry_after_seconds": retry_after,
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
        },
    )


def _rate_limit(
    cur,
    *,
    fingerprint: str,
    source_burst_limit: int,
    source_burst_window_seconds: int,
    source_limit: int,
    global_limit: int,
) -> None:
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('docplane-private-fabric-self-issue'))")
    checks = (
        ("source_burst", source_burst_limit, source_burst_window_seconds, fingerprint),
        ("source_sustained", source_limit, _HOUR_SECONDS, fingerprint),
        ("global_sustained", global_limit, _HOUR_SECONDS, None),
    )
    for scope, limit, window_seconds, scoped_fingerprint in checks:
        count, retry_after = _usage(
            cur,
            window_seconds=window_seconds,
            fingerprint=scoped_fingerprint,
        )
        _enforce_limit(
            scope=scope,
            count=count,
            limit=limit,
            window_seconds=window_seconds,
            retry_after=retry_after,
            fingerprint=fingerprint,
        )


@router.post("/api/v1/auth/self-issue", response_model=SelfIssuedPrincipalToken, status_code=status.HTTP_201_CREATED)
def self_issue_agent_token(
    request: Request,
    body: SelfIssueRequest | None = None,
    fabric_admission: str | None = Header(default=None, alias="X-DocPlane-Fabric-Admission", include_in_schema=False),
    admitted_source: str | None = Header(default=None, alias="X-DocPlane-Source-IP", include_in_schema=False),
) -> SelfIssuedPrincipalToken:
    policy = current_access_policy()
    if not policy.self_service:
        raise HTTPException(status_code=404, detail={"code":"SELF_ISSUE_DISABLED","message":"Self-service credential issuance is disabled for this deployment.","remedy":"Follow the token_acquisition procedure in /.well-known/docplane.json."})
    if fabric_admission != "1":
        raise HTTPException(status_code=403, detail={"code":"FABRIC_ADMISSION_REQUIRED","message":"Private-fabric self-issuance is available only through the trusted routed front.","remedy":"Use the DocPlane hostname advertised by the deployment, not a direct docs-api port."})

    evidence = _request_evidence(request, admitted_source)
    clear, digest, prefix = issue_token()
    display_name = body.display_name.strip() if body is not None and body.display_name else f"fabric-agent-{prefix[-6:]}"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=policy.self_issue_ttl_seconds)
    metadata: dict[str, Any] = {"access_profile":policy.profile,"issuance_mode":_SELF_ISSUE_MODE,**evidence}
    if body is not None and body.client_context:
        metadata["client_context"] = body.client_context.strip()

    with get_conn() as conn:
        cur = conn.cursor()
        _rate_limit(
            cur,
            fingerprint=evidence["source_fingerprint"],
            source_burst_limit=policy.source_burst_limit,
            source_burst_window_seconds=policy.source_burst_window_seconds,
            source_limit=policy.source_limit_per_hour,
            global_limit=policy.global_limit_per_hour,
        )
        cur.execute("""INSERT INTO docplane.principals (principal_kind, display_name, status, metadata) VALUES ('AGENT', %s, 'ACTIVE', %s) RETURNING principal_id::text""", (display_name, _json(metadata)))
        principal_id = cur.fetchone()[0]
        cur.execute("""INSERT INTO docplane.api_tokens (principal_id, token_hash, token_prefix, description, expires_at) VALUES (%s, %s, %s, %s, %s) RETURNING token_id::text""", (principal_id,digest,prefix,"Private-fabric self-issued AGENT contributor token",expires_at))
        token_id = cur.fetchone()[0]
        principal = Principal(principal_id=principal_id, principal_kind="AGENT", display_name=display_name, token_id=token_id)
        append_event(conn,event_type="AGENT_CREDENTIAL_SELF_ISSUED",channel="API",producer_id="docplane-auth-self-issue",idempotency_key=principal_id,principal=principal,client_identity=display_name,resource_type="principal",resource_id=principal_id,metadata={"access_profile":policy.profile,"expires_at":expires_at.isoformat(),"token_prefix":prefix,**evidence})
        conn.commit()

    log.info(
        "event=self_issue_issued principal_id=%s source_fingerprint=%s expires_at=%s",
        principal_id,
        evidence["source_fingerprint"][:12],
        expires_at.isoformat(),
    )
    return SelfIssuedPrincipalToken(principal_id=principal_id,display_name=display_name,principal_kind="AGENT",token=clear,token_prefix=prefix,expires_at=expires_at,access_profile=policy.profile,issued_via="fabric_reachability")
