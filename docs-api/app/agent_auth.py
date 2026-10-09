"""Named-principal authentication for DocPlane.

Every active principal is a contributor. Workspaces classify state and never
partition authoring rights. The bootstrap credential exists only to issue or
revoke named principals.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Header, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import get_conn


_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="DocPlaneBearer",
    description="Individual DocPlane contributor token: Authorization: Bearer <token>",
)


@dataclass(frozen=True)
class ArtifactScope:
    artifact_key: str
    operation: str
    source_entity_key: str | None
    observation_kind: str | None
    page_path_prefix: str | None


@dataclass(frozen=True)
class Principal:
    principal_id: str
    principal_kind: str
    display_name: str
    token_id: str
    artifact_scopes: tuple[ArtifactScope, ...] | None = None

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
                   t.token_id::text, t.expires_at, t.revoked_at, p.status,
                   p.authorization_mode
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
            cur.execute(
                """
                SELECT artifact_key, operation, source_entity_key, observation_kind, page_path_prefix
                  FROM docplane.principal_artifact_scopes
                 WHERE principal_id = %s
                 ORDER BY artifact_key, operation
                """,
                (row[0],),
            )
            scope_rows = cur.fetchall()
            conn.commit()
        else:
            scope_rows = []
    if row is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_TOKEN_INVALID", "message": "bearer token is invalid"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    principal_id, kind, display_name, token_id, expires_at, revoked_at, status, authorization_mode = row
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
    scopes = (
        tuple(ArtifactScope(*scope_row) for scope_row in scope_rows)
        if authorization_mode == "SCOPED_AUTOMATION"
        else None
    )
    return Principal(
        principal_id=principal_id,
        principal_kind=kind,
        display_name=display_name,
        token_id=token_id,
        artifact_scopes=scopes,
    )


def _scoped_write_route_allowed(request: Request) -> bool:
    method = request.method.upper()
    path = request.url.path
    if method == "POST":
        return (
            path in {
                "/api/v1/model/artifacts", "/api/v1/changes",
                "/api/v1/model/entities", "/api/v1/observations",
            }
            or re.fullmatch(
                r"/api/v1/model/artifacts/[0-9a-fA-F-]+/(?:handoff|retire)", path
            ) is not None
            or re.fullmatch(
                r"/api/v1/model/entities/[0-9a-fA-F-]+/links", path
            ) is not None
            or re.fullmatch(
                r"/api/v1/changes/[0-9a-fA-F-]+/(?:operations|validate|publish)", path
            ) is not None
        )
    if method == "PUT":
        return re.fullmatch(
            r"/api/v1/model/(?:artifacts/[0-9a-fA-F-]+/(?:projection|targets|execution-contract)|entities/[0-9a-fA-F-]+/page-links/catalogues)",
            path,
        ) is not None
    return method in {"GET", "HEAD", "OPTIONS"}


def require_contributor(
    request: Request,
    authorization: str | None = Header(default=None, include_in_schema=False),
    _credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> Principal:
    # Parse the raw header ourselves to preserve DocPlane's precise error codes;
    # the Security dependency exists to publish the bearer contract in OpenAPI.
    principal = authenticate(authorization)
    if principal.artifact_scopes is not None and not _scoped_write_route_allowed(request):
        raise HTTPException(
            status_code=403,
            detail={"code": "PRINCIPAL_WRITE_SCOPE_DENIED"},
        )
    return principal


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
