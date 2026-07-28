"""Low-friction agent workflows built on the canonical change pipeline.

These endpoints are shortcuts, not alternate authorities: they create the same
change and operation rows, run the same validation, publish through the same
transaction, emit the same events, and produce the same certified release.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException

from app.agent_api import add_operation, create_change
from app.agent_auth import Principal, require_contributor
from app.agent_models import (
    ChangeAbandonRequest,
    ChangeCreate,
    ChangeOperationCreate,
    PageReplaceRequest,
)
from app.db import get_conn
from app.event_store import append_event
from app.publication import change_view, publish_change, validate_change

router = APIRouter(tags=["docplane-agent-shortcuts"])


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def _required_idempotency(value: str | None) -> str:
    if not value or not value.strip():
        raise HTTPException(
            status_code=428,
            detail={
                "code": "IDEMPOTENCY_KEY_REQUIRED",
                "message": "Idempotency-Key is required for this mutation.",
                "remedy": "Retry with a stable unique Idempotency-Key and reuse it only for the identical request.",
            },
        )
    return value.strip()


def _child_key(parent: str, suffix: str) -> str:
    digest = hashlib.sha256(parent.encode("utf-8")).hexdigest()[:40]
    return f"agent-shortcut:{digest}:{suffix}"


def _replace_payload(request: PageReplaceRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {"content": request.content}
    if request.title is not None:
        payload["title"] = request.title
    if request.nav_path is not None:
        payload["nav_path"] = request.nav_path
    return payload


def _assert_published_replay(
    change: dict[str, Any],
    resource_id: UUID,
    request: PageReplaceRequest,
    operation_key: str,
) -> None:
    operations = change.get("operations") or []
    expected = (
        "REPLACE_DOCUMENT",
        str(resource_id),
        request.expected_revision,
        _replace_payload(request),
        operation_key,
    )
    actual = None
    if len(operations) == 1:
        operation = operations[0]
        actual = (
            operation.get("operation_type"),
            operation.get("page_resource_id"),
            operation.get("expected_revision"),
            operation.get("payload"),
            operation.get("idempotency_key"),
        )
    if actual != expected:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "IDEMPOTENCY_KEY_REUSED",
                "message": "The Idempotency-Key already identifies a different page replacement.",
                "remedy": "Use the original request body or choose a new Idempotency-Key.",
            },
        )


@router.post("/api/v1/pages/{resource_id}/replace")
def replace_page(
    resource_id: UUID,
    request: PageReplaceRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    """Replace one page through the full audited publication workflow in one call."""
    outer_key = _required_idempotency(idempotency_key)
    change_key = _child_key(outer_key, "change")
    operation_key = _child_key(outer_key, "operation")

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.path, w.workspace_key
              FROM docs.pages p
              JOIN docplane.workspaces w ON w.workspace_id = p.workspace_id
             WHERE p.resource_id = %s
            """,
            (str(resource_id),),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PAGE_NOT_FOUND",
                "message": "The requested page does not exist.",
                "remedy": "Resolve or search for the page again before preparing the edit.",
            },
        )
    path, workspace_key = row

    change = create_change(
        ChangeCreate(
            title=f"Replace {path}",
            purpose=request.purpose,
            workspace_key=workspace_key,
        ),
        idempotency_key=change_key,
        principal=principal,
    )
    change_id = UUID(change["change_id"])

    if change["status"] == "PUBLISHED":
        _assert_published_replay(change, resource_id, request, operation_key)
        return change
    if change["status"] == "ABANDONED":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CHANGE_ABANDONED",
                "message": "This idempotent replacement was abandoned before publication.",
                "remedy": "Choose a new Idempotency-Key to create a new audited replacement.",
                "change_id": str(change_id),
            },
        )

    add_operation(
        change_id,
        ChangeOperationCreate(
            operation_type="REPLACE_DOCUMENT",
            page_resource_id=resource_id,
            expected_revision=request.expected_revision,
            payload=_replace_payload(request),
        ),
        idempotency_key=operation_key,
        principal=principal,
    )
    validate_change(change_id, principal)
    return publish_change(change_id, principal)


@router.post("/api/v1/changes/{change_id}/abandon")
def abandon_change(
    change_id: UUID,
    request: ChangeAbandonRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    """Close a draft or validated change without publishing it."""
    key = _child_key(_required_idempotency(idempotency_key), "abandon")
    with get_conn() as conn:
        change = change_view(conn, str(change_id))
        if change["status"] == "PUBLISHED":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CHANGE_ALREADY_PUBLISHED",
                    "message": "A published change cannot be abandoned.",
                    "remedy": "Create a new corrective change or use page rollback.",
                },
            )
        if change["status"] == "ABANDONED":
            receipt = change.get("failure_receipt") or {}
            if receipt.get("idempotency_key") != key or receipt.get("reason") != request.reason:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "IDEMPOTENCY_KEY_REUSED",
                        "message": "This change was abandoned with different request data.",
                        "remedy": "Reuse the original abandon request exactly.",
                    },
                )
            return change

        receipt = {
            "code": "CHANGE_ABANDONED_BY_CONTRIBUTOR",
            "reason": request.reason,
            "idempotency_key": key,
            "abandoned_by": principal.principal_id,
        }
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE docs.changes
               SET status = 'ABANDONED', failure_receipt = %s,
                   validation_summary = NULL, preview_receipt = NULL,
                   updated_at = now()
             WHERE change_id = %s AND status IN ('DRAFT', 'VALIDATED')
            """,
            (_json(receipt), str(change_id)),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail={"code": "CHANGE_NOT_MUTABLE"})
        append_event(
            conn,
            event_type="CHANGE_ABANDONED",
            channel="API",
            producer_id="docplane-api",
            idempotency_key=key,
            principal=principal,
            workspace_key=change["workspace_key"],
            resource_type="CHANGE",
            resource_id=str(change_id),
            metadata={"reason": request.reason},
        )
        conn.commit()
        return change_view(conn, str(change_id))
