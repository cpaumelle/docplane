"""Named-principal authentication for the endpoint-first DocPlane API.

Tokens are random bearer credentials. Only SHA-256 hashes are persisted; the clear token is returned
once at issuance. A trusted network may protect a deployment additionally, but network position is not
an identity and never grants product scopes by itself.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import Header, HTTPException

from app.db import get_conn


VALID_SCOPES = frozenset(
    {
        "docs:read",
        "docs:propose",
        "docs:write-direct",
        "docs:review",
        "docs:merge",
        "work:read",
        "work:update",
        "catalog:read",
        "analytics:read",
        "events:subscribe",
        "admin:principals",
    }
)


@dataclass(frozen=True)
class Principal:
    principal_id: str
    principal_kind: str
    display_name: str
    scopes: frozenset[str]
    token_id: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token() -> tuple[str, str, str]:
    clear = "dp_" + secrets.token_urlsafe(36)
    return clear, hash_token(clear), clear[:12]


def validate_scopes(scopes: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    normalized = sorted({str(scope).strip() for scope in scopes if str(scope).strip()})
    unknown = sorted(set(normalized) - VALID_SCOPES)
    if unknown:
        raise ValueError("unknown scopes: " + ", ".join(unknown))
    if not normalized:
        raise ValueError("at least one scope is required")
    return normalized


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_REQUIRED", "message": "Authorization: Bearer token is required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_SCHEME_INVALID", "message": "use Authorization: Bearer <token>"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


def authenticate(authorization: str | None) -> Principal:
    token = _bearer_token(authorization)
    digest = hash_token(token)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.principal_id::text, p.principal_kind, p.display_name,
                   t.scopes, t.token_id::text, t.expires_at, t.revoked_at, p.status
              FROM docplane.api_tokens t
              JOIN docplane.principals p ON p.principal_id = t.principal_id
             WHERE t.token_hash = %s
            """,
            (digest,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_TOKEN_INVALID", "message": "bearer token is invalid"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    principal_id, kind, display_name, scopes, token_id, expires_at, revoked_at, status = row
    now = datetime.now(timezone.utc)
    if revoked_at is not None or status != "ACTIVE":
        raise HTTPException(
            status_code=403,
            detail={"code": "AUTH_PRINCIPAL_INACTIVE", "message": "principal or token is inactive"},
        )
    if expires_at is not None and expires_at <= now:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_TOKEN_EXPIRED", "message": "bearer token has expired"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Principal(
        principal_id=principal_id,
        principal_kind=kind,
        display_name=display_name,
        scopes=frozenset(scopes or ()),
        token_id=token_id,
    )


def require_scopes(*required: str) -> Callable:
    required_set = frozenset(required)
    unknown = required_set - VALID_SCOPES
    if unknown:
        raise RuntimeError("route declares unknown scopes: " + ", ".join(sorted(unknown)))

    def dependency(authorization: str | None = Header(default=None)) -> Principal:
        principal = authenticate(authorization)
        missing = required_set - principal.scopes
        if missing:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "AUTH_SCOPE_REQUIRED",
                    "message": "principal lacks required scope",
                    "required": sorted(required_set),
                    "missing": sorted(missing),
                },
            )
        return principal

    return dependency


def require_bootstrap_token(value: str | None) -> None:
    configured = os.environ.get("DOCPLANE_BOOTSTRAP_ADMIN_TOKEN", "")
    if not configured:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "BOOTSTRAP_DISABLED",
                "message": "DOCPLANE_BOOTSTRAP_ADMIN_TOKEN is not configured",
            },
        )
    if not value or not hmac.compare_digest(value, configured):
        raise HTTPException(
            status_code=403,
            detail={"code": "BOOTSTRAP_TOKEN_INVALID", "message": "bootstrap token is invalid"},
        )
