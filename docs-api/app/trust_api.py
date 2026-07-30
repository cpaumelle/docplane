"""Ownership, classification, verification and maintenance APIs for contributors."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.agent_auth import Principal, require_contributor
from app.db import get_conn
from app.event_store import append_event
from app.maintenance_policy import MAINTENANCE_PREDICATES
from app.trust_models import PageClassificationUpdate, PageOwnerUpdate, PageVerificationCreate, VerificationExpiryRun
from app.workspace_access import page_workspace

router = APIRouter(tags=["trust-v1", "maintenance-v1"])

_DURABLE = {"REFERENCE", "OPERATION", "ARCHITECTURE", "DESIGN", "DECISION", "POLICY", "EVIDENCE"}


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def _key(value: str | None) -> str:
    if not value or not value.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    return value.strip()[:256]


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _receipt(conn, principal: Principal, key: str, operation: str, page_id: str, digest: str):
    cur = conn.cursor()
    cur.execute("SELECT operation_type, page_resource_id::text, request_hash, response FROM docs.trust_mutation_receipts WHERE principal_id = %s AND idempotency_key = %s", (principal.principal_id, key))
    row = cur.fetchone()
    if row is None:
        return None
    if row[0] != operation or row[1] != page_id or row[2] != digest:
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED"})
    return row[3]


def _save_receipt(conn, principal: Principal, key: str, operation: str, page_id: str, digest: str, response: dict[str, Any]) -> None:
    cur = conn.cursor()
    cur.execute("INSERT INTO docs.trust_mutation_receipts (principal_id, idempotency_key, operation_type, page_resource_id, request_hash, response) VALUES (%s, %s, %s, %s, %s, %s)", (principal.principal_id, key, operation, page_id, digest, _json(response)))


def _snapshot(conn, page: dict[str, Any], principal: Principal, reason: str) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO docs.page_metadata_history
            (page_resource_id, metadata_version, workspace_id, publication_state,
             knowledge_class, verification_state, owner_principal_id, review_due_at,
             criticality, metadata_review_required, provenance, changed_by_principal_id, change_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (page["resource_id"], page["metadata_version"], page["workspace_id"], page["publication_state"], page["knowledge_class"], page["verification_state"], page["owner_principal_id"], page["review_due_at"], page["criticality"], page["metadata_review_required"], page.get("provenance"), principal.principal_id, reason),
    )


def _view(conn, page_id: str) -> dict[str, Any]:
    page = page_workspace(conn, page_id)
    cur = conn.cursor()
    cur.execute("SELECT verification_id::text, page_revision, verifier_principal_id::text, verification_state, verified_at, expires_at, notes FROM docs.page_verifications WHERE page_resource_id = %s ORDER BY verified_at DESC LIMIT 50", (page_id,))
    verifications = [dict(zip(("verification_id", "page_revision", "verifier_principal_id", "verification_state", "verified_at", "expires_at", "notes"), row)) for row in cur.fetchall()]
    cur.execute("SELECT metadata_version, workspace_id::text, publication_state, knowledge_class, verification_state, owner_principal_id::text, review_due_at, criticality, metadata_review_required, provenance, changed_by_principal_id::text, change_reason, recorded_at FROM docs.page_metadata_history WHERE page_resource_id = %s ORDER BY metadata_version DESC, recorded_at DESC LIMIT 50", (page_id,))
    history = [dict(zip(("metadata_version", "workspace_id", "publication_state", "knowledge_class", "verification_state", "owner_principal_id", "review_due_at", "criticality", "metadata_review_required", "provenance", "changed_by_principal_id", "change_reason", "recorded_at"), row)) for row in cur.fetchall()]
    return {**page, "verifications": verifications, "metadata_history": history}


@router.get("/api/v1/pages/{page_resource_id}/trust")
def get_trust(page_resource_id: UUID, principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        return _view(conn, str(page_resource_id))


@router.post("/api/v1/pages/{page_resource_id}/classification")
def classify(
    page_resource_id: UUID,
    request: PageClassificationUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    value = {"page_resource_id": str(page_resource_id), **request.model_dump(mode="json")}
    digest = _hash(value)
    with get_conn() as conn:
        existing = _receipt(conn, principal, key, "CLASSIFY_PAGE", str(page_resource_id), digest)
        if existing is not None:
            return existing
        page = page_workspace(conn, str(page_resource_id), for_update=True)
        if page["metadata_version"] != request.expected_metadata_version:
            raise HTTPException(status_code=409, detail={"code": "PAGE_METADATA_VERSION_STALE", "current": page["metadata_version"]})
        cur = conn.cursor()
        cur.execute("SELECT workspace_kind, workspace_key FROM docplane.workspaces WHERE workspace_id = %s", (str(request.workspace_id),))
        workspace = cur.fetchone()
        if workspace is None:
            raise HTTPException(status_code=404, detail={"code": "WORKSPACE_NOT_FOUND"})
        if workspace[0] == "WORK" and request.knowledge_class != "WORK_NOTE":
            raise HTTPException(status_code=422, detail={"code": "WORKSPACE_KNOWLEDGE_CLASS_MISMATCH"})
        if workspace[0] != "WORK" and request.knowledge_class == "WORK_NOTE":
            raise HTTPException(status_code=422, detail={"code": "WORKSPACE_KNOWLEDGE_CLASS_MISMATCH"})
        if request.owner_principal_id:
            cur.execute("SELECT status FROM docplane.principals WHERE principal_id = %s", (str(request.owner_principal_id),))
            owner = cur.fetchone()
            if owner is None or owner[0] != "ACTIVE":
                raise HTTPException(status_code=409, detail={"code": "OWNER_PRINCIPAL_INACTIVE"})
        _snapshot(conn, page, principal, request.reason.strip())
        cur.execute(
            """
            UPDATE docs.pages
               SET workspace_id = %s, publication_state = %s, knowledge_class = %s,
                   criticality = %s, owner_principal_id = %s, review_due_at = %s,
                   provenance = coalesce(%s, provenance),
                   metadata_review_required = false, metadata_version = metadata_version + 1,
                   updated_at = now(), updated_by = %s
             WHERE resource_id = %s AND metadata_version = %s
            """,
            (str(request.workspace_id), request.publication_state, request.knowledge_class, request.criticality, str(request.owner_principal_id) if request.owner_principal_id else None, request.review_due_at, request.provenance, principal.display_name, str(page_resource_id), request.expected_metadata_version),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail={"code": "PAGE_METADATA_UPDATE_RACE"})
        response = _view(conn, str(page_resource_id))
        _save_receipt(conn, principal, key, "CLASSIFY_PAGE", str(page_resource_id), digest, response)
        append_event(conn, event_type="PAGE_CLASSIFIED", channel="API", producer_id="docplane-trust", idempotency_key=f"CLASSIFY_PAGE:{principal.principal_id}:{key}", principal=principal, workspace_key=workspace[1], resource_type="PAGE", resource_id=str(page_resource_id), page_resource_id=str(page_resource_id), page_path=page["path"], page_revision=page["revision"], metadata={"knowledge_class": request.knowledge_class})
        conn.commit()
        return response


@router.post("/api/v1/pages/{page_resource_id}/owner")
def assign_owner(
    page_resource_id: UUID,
    request: PageOwnerUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    value = {"page_resource_id": str(page_resource_id), **request.model_dump(mode="json")}
    digest = _hash(value)
    with get_conn() as conn:
        existing = _receipt(conn, principal, key, "ASSIGN_PAGE_OWNER", str(page_resource_id), digest)
        if existing is not None:
            return existing
        page = page_workspace(conn, str(page_resource_id), for_update=True)
        if page["metadata_version"] != request.expected_metadata_version:
            raise HTTPException(status_code=409, detail={"code": "PAGE_METADATA_VERSION_STALE", "current": page["metadata_version"]})
        cur = conn.cursor()
        cur.execute("SELECT status FROM docplane.principals WHERE principal_id = %s", (str(request.owner_principal_id),))
        owner = cur.fetchone()
        if owner is None or owner[0] != "ACTIVE":
            raise HTTPException(status_code=409, detail={"code": "OWNER_PRINCIPAL_INACTIVE"})
        _snapshot(conn, page, principal, request.reason.strip())
        cur.execute("UPDATE docs.pages SET owner_principal_id = %s, metadata_version = metadata_version + 1, updated_at = now(), updated_by = %s WHERE resource_id = %s AND metadata_version = %s", (str(request.owner_principal_id), principal.display_name, str(page_resource_id), request.expected_metadata_version))
        response = _view(conn, str(page_resource_id))
        _save_receipt(conn, principal, key, "ASSIGN_PAGE_OWNER", str(page_resource_id), digest, response)
        conn.commit()
        return response


@router.post("/api/v1/pages/{page_resource_id}/verify", status_code=201)
def verify(
    page_resource_id: UUID,
    request: PageVerificationCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    value = {"page_resource_id": str(page_resource_id), **request.model_dump(mode="json")}
    digest = _hash(value)
    with get_conn() as conn:
        existing = _receipt(conn, principal, key, "VERIFY_PAGE", str(page_resource_id), digest)
        if existing is not None:
            return existing
        page = page_workspace(conn, str(page_resource_id), for_update=True)
        if page["revision"] != request.expected_revision:
            raise HTTPException(status_code=409, detail={"code": "PAGE_REVISION_STALE", "current": page["revision"]})
        if page["publication_state"] != "PUBLISHED" or page["knowledge_class"] not in _DURABLE:
            raise HTTPException(status_code=409, detail={"code": "PAGE_NOT_VERIFIABLE"})
        if request.expires_at and request.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=422, detail={"code": "VERIFICATION_EXPIRY_INVALID"})
        _snapshot(conn, page, principal, "Page revision verified")
        cur = conn.cursor()
        cur.execute("UPDATE docs.page_verifications SET verification_state = 'REVOKED' WHERE page_resource_id = %s AND verification_state = 'VERIFIED'", (str(page_resource_id),))
        cur.execute("INSERT INTO docs.page_verifications (page_resource_id, page_revision, verifier_principal_id, verification_state, expires_at, notes) VALUES (%s, %s, %s, 'VERIFIED', %s, %s) RETURNING verification_id::text, verified_at", (str(page_resource_id), page["revision"], principal.principal_id, request.expires_at, request.notes))
        verification_id, verified_at = cur.fetchone()
        cur.execute("UPDATE docs.pages SET verification_state = 'VERIFIED', review_due_at = %s, metadata_version = metadata_version + 1, updated_at = now(), updated_by = %s WHERE resource_id = %s", (request.expires_at, principal.display_name, str(page_resource_id)))
        response = _view(conn, str(page_resource_id))
        response["verification_receipt"] = {"verification_id": verification_id, "verified_at": verified_at, "page_revision": page["revision"], "expires_at": request.expires_at}
        _save_receipt(conn, principal, key, "VERIFY_PAGE", str(page_resource_id), digest, response)
        conn.commit()
        return response


@router.post("/api/v1/maintenance/verifications/expire")
def expire(
    request: VerificationExpiryRun,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    cutoff = request.cutoff or datetime.now(timezone.utc)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT docs.expire_due_verifications(%s)", (cutoff,))
        changed = int(cur.fetchone()[0])
        append_event(conn, event_type="VERIFICATION_EXPIRY_RUN", channel="SYSTEM", producer_id="docplane-trust", idempotency_key=f"VERIFICATION_EXPIRY_RUN:{principal.principal_id}:{key}", principal=principal, resource_type="MAINTENANCE_JOB", resource_id=key, metadata={"cutoff": cutoff.isoformat(), "pages_expired": changed})
        conn.commit()
    return {"cutoff": cutoff, "pages_expired": changed}


@router.get("/api/v1/maintenance/pages")
def maintenance_pages(
    queue: str = Query(default="all", pattern="^(metadata_review|unowned|unverified|verification_due|expired|outdated|all)$"),
    due_within_days: int = Query(default=30, ge=0, le=3650),
    limit: int = Query(default=100, ge=1, le=1000),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) + timedelta(days=due_within_days)
    selected = list(MAINTENANCE_PREDICATES) if queue == "all" else [queue]
    result: dict[str, list[dict[str, Any]]] = {}
    with get_conn() as conn:
        cur = conn.cursor()
        for name in selected:
            params: list[Any] = [cutoff] if name == "verification_due" else []
            params.append(limit)
            cur.execute(f"SELECT p.resource_id::text, p.path, p.title, p.revision, w.workspace_key, p.publication_state, p.knowledge_class, p.verification_state, p.owner_principal_id::text, p.review_due_at, p.criticality, p.metadata_review_required, p.metadata_version, p.updated_at FROM docs.pages p JOIN docplane.workspaces w ON w.workspace_id = p.workspace_id WHERE {MAINTENANCE_PREDICATES[name]} ORDER BY coalesce(p.review_due_at, p.updated_at), p.path LIMIT %s", params)
            result[name] = [dict(zip(("resource_id", "path", "title", "revision", "workspace_key", "publication_state", "knowledge_class", "verification_state", "owner_principal_id", "review_due_at", "criticality", "metadata_review_required", "metadata_version", "updated_at"), row)) for row in cur.fetchall()]
    return {"queues": result, "due_cutoff": cutoff}
