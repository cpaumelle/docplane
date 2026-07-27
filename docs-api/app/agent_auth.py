"""Named-principal authentication for DocPlane.

Every active principal is a contributor. Workspaces classify state and never
partition authoring rights. The bootstrap credential exists only to issue or
revoke named principals.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Header, HTTPException

from app.db import get_conn


@dataclass(frozen=True)
class Principal:
    principal_id: str
    principal_kind: str
    display_name: str
    token_id: str

    @property
    def role(self) -> str:
        return "CONTRIBUTOR"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token() -> tuple[str, str, str]:
    clear = "dp_" + secrets.token_urlsafe(36)
    return clear, hash_token(clear), clear[:12]


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
    digest = hash_token(_bearer_token(authorization))
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.principal_id::text, p.principal_kind, p.display_name,
                   t.token_id::text, t.expires_at, t.revoked_at, p.status
              FROM docplane.api_tokens t
              JOIN docplane.principals p ON p.principal_id = t.principal_id
             WHERE t.token_hash = %s
            """,
            (digest,),
        )
        row = cur.fetchone()
        if row is not None:
            cur.execute(
                "UPDATE docplane.api_tokens SET last_used_at = now() WHERE token_id = %s",
                (row[3],),
            )
            conn.commit()
    if row is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_TOKEN_INVALID", "message": "bearer token is invalid"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    principal_id, kind, display_name, token_id, expires_at, revoked_at, status = row
    if revoked_at is not None or status != "ACTIVE":
        raise HTTPException(
            status_code=403,
            detail={"code": "AUTH_PRINCIPAL_INACTIVE", "message": "principal or token is inactive"},
        )
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_TOKEN_EXPIRED", "message": "bearer token has expired"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Principal(
        principal_id=principal_id,
        principal_kind=kind,
        display_name=display_name,
        token_id=token_id,
    )


def require_contributor(authorization: str | None = Header(default=None)) -> Principal:
    return authenticate(authorization)


def require_bootstrap_token(value: str | None) -> None:
    configured = os.environ.get("DOCPLANE_BOOTSTRAP_TOKEN", "")
    if not configured:
        raise HTTPException(
            status_code=503,
            detail={"code": "BOOTSTRAP_DISABLED", "message": "bootstrap administration is disabled"},
        )
    if not value or not hmac.compare_digest(value, configured):
        raise HTTPException(
            status_code=403,
            detail={"code": "BOOTSTRAP_TOKEN_INVALID", "message": "bootstrap token is invalid"},
        )
