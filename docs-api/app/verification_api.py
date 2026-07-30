"""Verification-on-demand: freshness reads and fabric-check requests.

The staleness loop: the freshness table shows what has not been verified
recently; a human or agent mints a verification request (scoped to a page,
a section, or a model entity's described pages); an agent executes on the
fabric and returns evidence through the existing revision-bound
verification endpoint or a drafted correction. Triggers are on-demand and
graph ripple; expiry is a prompt only. No scheduled re-verification exists,
and this module must never grow one.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.agent_auth import Principal, require_contributor
from app.db import get_conn
from app.event_store import append_event
from app.mutation_receipts import load_receipt, receipt_digest, save_receipt

router = APIRouter(tags=["verification-v1", "maintenance-v1"])


def _json(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda item: json.dumps(item, sort_keys=True, default=str))


def _key(value: str | None) -> str:
    if not value or not value.strip():
        raise HTTPException(status_code=428, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    return value.strip()[:256]


def correction_policy(criticality: str) -> str:
    """How an executing agent should deliver a correction when facts drifted:
    minor pages publish directly; critical pages leave a pending change for a
    human. Pure so the rule is testable and visible in every briefing."""
    if criticality in {"OPERATIONAL_CRITICAL", "POLICY_REQUIRED"}:
        return "PENDING_CHANGE"
    return "DIRECT_PUBLISH"


class VerificationRequestCreate(BaseModel):
    page_resource_id: UUID | None = None
    path_prefix: str | None = Field(default=None, pattern=r"^[a-z0-9/_-]+$", max_length=300)
    entity_id: UUID | None = None
    note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def exactly_one_scope(self):
        if sum(item is not None for item in (self.page_resource_id, self.path_prefix, self.entity_id)) != 1:
            raise ValueError("exactly one of page_resource_id, path_prefix or entity_id is required")
        return self


class PageOutcome(BaseModel):
    resource_id: UUID
    outcome: Literal["VERIFIED", "CORRECTED", "INCONCLUSIVE"]


class VerificationRequestComplete(BaseModel):
    outcome: Literal["VERIFIED", "CORRECTED", "MIXED", "INCONCLUSIVE"]
    summary: str = Field(min_length=1, max_length=4000)
    verification_ids: list[UUID] = Field(default_factory=list, max_length=200)
    change_ids: list[UUID] = Field(default_factory=list, max_length=200)
    per_page: list[PageOutcome] = Field(default_factory=list, max_length=200)


_BRIEFING_PAGE_KEYS = ("resource_id", "path", "title", "revision", "verification_state", "criticality", "provenance")


def _briefing(conn, request: VerificationRequestCreate) -> dict[str, Any]:
    cur = conn.cursor()
    select = (
        "SELECT p.resource_id::text, p.path, p.title, p.revision, p.verification_state, "
        "p.criticality, p.provenance FROM docs.pages p"
    )
    if request.page_resource_id is not None:
        cur.execute(select + " WHERE p.resource_id = %s AND p.status = 'active'", (str(request.page_resource_id),))
    elif request.path_prefix is not None:
        cur.execute(select + " WHERE p.path LIKE %s AND p.status = 'active' ORDER BY p.path LIMIT 201", (request.path_prefix.rstrip("/") + "/%",))
    else:
        cur.execute(
            select
            + """
              JOIN model.entity_page_links l ON l.page_resource_id = p.resource_id
             WHERE l.entity_id = %s AND l.relation IN ('DESCRIBES', 'OPERATES') AND p.status = 'active'
             ORDER BY p.path LIMIT 201
            """,
            (str(request.entity_id),),
        )
    rows = cur.fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail={"code": "VERIFICATION_SCOPE_EMPTY"})
    if len(rows) > 200:
        # Never silently truncate a verification scope: an incomplete
        # briefing would let completion claim coverage it does not have.
        raise HTTPException(status_code=422, detail={
            "code": "VERIFICATION_SCOPE_TOO_LARGE",
            "limit": 200,
            "remedy": "Split the scope into narrower path prefixes and mint one request per slice.",
        })
    pages = []
    for row in rows:
        page = dict(zip(_BRIEFING_PAGE_KEYS, row))
        page["correction_policy"] = correction_policy(page["criticality"])
        cur.execute(
            """
            SELECT e.entity_id::text, e.entity_kind, e.entity_key, l.relation
              FROM model.entity_page_links l JOIN model.entities e ON e.entity_id = l.entity_id
             WHERE l.page_resource_id = %s ORDER BY e.entity_kind, e.entity_key
            """,
            (page["resource_id"],),
        )
        page["entities"] = [dict(zip(("entity_id", "entity_kind", "entity_key", "relation"), item)) for item in cur.fetchall()]
        pages.append(page)
    return {
        "pages": pages,
        "page_count": len(pages),
        "result_contract": {
            "verify": "POST /api/v1/pages/{resource_id}/verify with the exact revision checked and evidence (commands run, values seen) in notes",
            "correct": "normal change contract; delivery per each page's correction_policy",
            "complete": "POST /api/v1/verification-requests/{request_id}/complete with outcome, summary and the verification/change ids",
        },
    }


_REQUEST_COLUMNS = (
    "request_id", "page_resource_id", "path_prefix", "entity_id", "reason",
    "status", "note", "briefing", "resolution", "requested_by", "claimed_by",
    "requested_at", "claimed_at", "completed_at",
)


def _request_select() -> str:
    return (
        "SELECT request_id, page_resource_id, path_prefix, entity_id, reason, "
        "status, note, briefing, resolution, requested_by, claimed_by, "
        "requested_at, claimed_at, completed_at FROM docs.verification_requests"
    )


def _request(row) -> dict[str, Any]:
    value = dict(zip(_REQUEST_COLUMNS, row))
    for key in ("request_id", "page_resource_id", "entity_id", "requested_by", "claimed_by"):
        if value.get(key) is not None:
            value[key] = str(value[key])
    value["uri"] = f"docplane://know/verification-requests/{value['request_id']}"
    return value


def _load_request(conn, request_id: UUID, *, for_update: bool = False) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(_request_select() + " WHERE request_id = %s" + (" FOR UPDATE" if for_update else ""), (str(request_id),))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "VERIFICATION_REQUEST_NOT_FOUND"})
    return _request(row)


def mint_ripple_request(conn, entity: dict[str, Any], principal: Principal) -> str | None:
    """Graph ripple: a changed model entity flags its described pages as
    verification candidates. One live ripple request per entity; returns the
    request id, or None when nothing is described or one is already open."""
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM model.entity_page_links WHERE entity_id = %s AND relation IN ('DESCRIBES', 'OPERATES') LIMIT 1",
        (entity["entity_id"],),
    )
    if cur.fetchone() is None:
        return None
    create = VerificationRequestCreate(entity_id=entity["entity_id"], note=f"Model entity {entity['entity_kind']}/{entity['entity_key']} changed; described pages may be stale.")
    briefing = _briefing(conn, create)
    cur.execute(
        """
        INSERT INTO docs.verification_requests
            (entity_id, reason, note, briefing, requested_by, idempotency_key)
        VALUES (%s, 'RIPPLE', %s, %s, %s, %s)
        ON CONFLICT (entity_id) WHERE status IN ('OPEN', 'CLAIMED') AND reason = 'RIPPLE' DO NOTHING
        RETURNING request_id::text
        """,
        (entity["entity_id"], create.note, _json(briefing), principal.principal_id, f"ripple:{entity['entity_id']}:{entity['version']}"),
    )
    row = cur.fetchone()
    return row[0] if row else None


@router.post("/api/v1/verification-requests", status_code=201)
def create_verification_request(
    request: VerificationRequestCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    digest = receipt_digest({"route": "verification-request-create", **request.model_dump(mode="json")})
    with get_conn() as conn:
        replayed = load_receipt(conn, principal, key, "VERIFICATION_REQUEST_CREATE", digest)
        if replayed is not None:
            return replayed
        briefing = _briefing(conn, request)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO docs.verification_requests
                (page_resource_id, path_prefix, entity_id, reason, note, briefing, requested_by, idempotency_key)
            VALUES (%s, %s, %s, 'MANUAL', %s, %s, %s, %s)
            ON CONFLICT (requested_by, idempotency_key)
            DO UPDATE SET requested_at = docs.verification_requests.requested_at
            RETURNING request_id
            """,
            (
                str(request.page_resource_id) if request.page_resource_id else None,
                request.path_prefix, str(request.entity_id) if request.entity_id else None,
                request.note, _json(briefing), principal.principal_id, key,
            ),
        )
        request_id = cur.fetchone()[0]
        response = _load_request(conn, request_id)
        append_event(
            conn,
            event_type="VERIFICATION_REQUEST_CREATED",
            channel="API",
            producer_id="docplane-verification",
            idempotency_key=f"VERIFICATION_REQUEST_CREATED:{principal.principal_id}:{key}",
            principal=principal,
            resource_type="VERIFICATION_REQUEST",
            resource_id=str(request_id),
            metadata={"reason": "MANUAL", "page_count": briefing["page_count"]},
        )
        save_receipt(conn, principal, key, "VERIFICATION_REQUEST_CREATE", str(request_id), digest, response)
        conn.commit()
        return response


@router.get("/api/v1/verification-requests")
def list_verification_requests(
    status: str = Query(default="OPEN"),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    if status not in {"OPEN", "CLAIMED", "COMPLETED", "CANCELLED", "all"}:
        raise HTTPException(status_code=422, detail={"code": "VERIFICATION_STATUS_INVALID"})
    predicate = "" if status == "all" else " WHERE status = %s"
    params: list[Any] = [] if status == "all" else [status]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT count(*) FROM docs.verification_requests{predicate}", params)
        total = int(cur.fetchone()[0])
        cur.execute(_request_select() + predicate + " ORDER BY requested_at DESC LIMIT %s", [*params, limit])
        rows = cur.fetchall()
    values = [_request(row) for row in rows]
    return {"requests": values, "count": len(values), "total": total, "truncated": len(values) < total}


@router.get("/api/v1/verification-requests/{request_id}")
def get_verification_request(request_id: UUID, principal: Principal = Depends(require_contributor)) -> dict[str, Any]:
    with get_conn() as conn:
        return _load_request(conn, request_id)


@router.post("/api/v1/verification-requests/{request_id}/claim")
def claim_verification_request(
    request_id: UUID,
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    with get_conn() as conn:
        value = _load_request(conn, request_id, for_update=True)
        if value["status"] == "CLAIMED" and value["claimed_by"] == str(principal.principal_id):
            return value
        if value["status"] != "OPEN":
            raise HTTPException(status_code=409, detail={"code": "VERIFICATION_REQUEST_NOT_OPEN", "status": value["status"]})
        cur = conn.cursor()
        cur.execute(
            "UPDATE docs.verification_requests SET status = 'CLAIMED', claimed_by = %s, claimed_at = now() WHERE request_id = %s",
            (principal.principal_id, str(request_id)),
        )
        conn.commit()
        return _load_request(conn, request_id)


def _reconcile_completion(conn, briefing: dict[str, Any], request: VerificationRequestComplete) -> list[dict[str, Any]]:
    """Completion must reconcile every briefing page against real evidence.

    VERIFIED   — each page has a supplied verification receipt bound to the
                 EXACT revision captured in the briefing, in state VERIFIED.
    CORRECTED  — supplied changes are PUBLISHED, and each page is either
                 verified as above or targeted by one of those changes.
    MIXED      — a per-page outcome covers every page; VERIFIED/CORRECTED
                 entries reconcile under their respective rules.
    INCONCLUSIVE — closes with an explanation; implies no verification and
                 leaves every page's verification state untouched.
    """
    pages = briefing.get("pages", [])
    if request.outcome == "INCONCLUSIVE":
        return []
    cur = conn.cursor()
    verified_pages: dict[str, str] = {}
    if request.verification_ids:
        cur.execute(
            """
            SELECT page_resource_id::text, page_revision FROM docs.page_verifications
             WHERE verification_id = ANY(%s::uuid[]) AND verification_state = 'VERIFIED'
            """,
            ([str(item) for item in request.verification_ids],),
        )
        verified_pages = {row[0]: row[1] for row in cur.fetchall()}
    corrected_pages: set[str] = set()
    if request.change_ids:
        cur.execute(
            "SELECT change_id::text, status FROM docs.changes WHERE change_id = ANY(%s::uuid[])",
            ([str(item) for item in request.change_ids],),
        )
        statuses = {row[0]: row[1] for row in cur.fetchall()}
        unpublished = sorted(str(item) for item in request.change_ids if statuses.get(str(item)) != "PUBLISHED")
        if unpublished:
            return [{"code": "CHANGE_NOT_PUBLISHED", "change_ids": unpublished}]
        cur.execute(
            "SELECT DISTINCT page_resource_id::text FROM docs.change_operations WHERE change_id = ANY(%s::uuid[]) AND page_resource_id IS NOT NULL",
            ([str(item) for item in request.change_ids],),
        )
        corrected_pages = {row[0] for row in cur.fetchall()}

    def verified_ok(page: dict[str, Any]) -> bool:
        return verified_pages.get(page["resource_id"]) == page["revision"]

    per_page = {str(item.resource_id): item.outcome for item in request.per_page}
    unresolved: list[dict[str, Any]] = []
    for page in pages:
        rid = page["resource_id"]
        if request.outcome == "VERIFIED":
            if not verified_ok(page):
                unresolved.append({"resource_id": rid, "path": page["path"], "code": "VERIFICATION_RECEIPT_MISSING_OR_STALE", "expected_revision": page["revision"]})
        elif request.outcome == "CORRECTED":
            if not verified_ok(page) and rid not in corrected_pages:
                unresolved.append({"resource_id": rid, "path": page["path"], "code": "CORRECTION_COVERAGE_MISSING"})
        else:  # MIXED
            outcome = per_page.get(rid)
            if outcome is None:
                unresolved.append({"resource_id": rid, "path": page["path"], "code": "PER_PAGE_OUTCOME_MISSING"})
            elif outcome == "VERIFIED" and not verified_ok(page):
                unresolved.append({"resource_id": rid, "path": page["path"], "code": "VERIFICATION_RECEIPT_MISSING_OR_STALE", "expected_revision": page["revision"]})
            elif outcome == "CORRECTED" and rid not in corrected_pages:
                unresolved.append({"resource_id": rid, "path": page["path"], "code": "CORRECTION_COVERAGE_MISSING"})
    return unresolved


@router.post("/api/v1/verification-requests/{request_id}/complete")
def complete_verification_request(
    request_id: UUID,
    request: VerificationRequestComplete,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    key = _key(idempotency_key)
    digest = receipt_digest({"route": "verification-request-complete", "request_id": str(request_id), **request.model_dump(mode="json")})
    with get_conn() as conn:
        replayed = load_receipt(conn, principal, key, "VERIFICATION_REQUEST_COMPLETE", digest)
        if replayed is not None:
            return replayed
        value = _load_request(conn, request_id, for_update=True)
        if value["status"] == "COMPLETED":
            return value
        if value["status"] not in {"OPEN", "CLAIMED"}:
            raise HTTPException(status_code=409, detail={"code": "VERIFICATION_REQUEST_NOT_OPEN", "status": value["status"]})
        if value["status"] == "CLAIMED" and value["claimed_by"] != str(principal.principal_id):
            raise HTTPException(status_code=409, detail={"code": "VERIFICATION_REQUEST_CLAIMED_BY_OTHER", "claimed_by": value["claimed_by"]})
        unresolved = _reconcile_completion(conn, value["briefing"], request)
        if unresolved:
            raise HTTPException(status_code=422, detail={
                "code": "VERIFICATION_COMPLETION_UNRECONCILED",
                "unresolved": unresolved,
                "remedy": "Every briefing page needs revision-exact verification receipts (VERIFIED), applicable published changes (CORRECTED), or a per-page outcome (MIXED). INCONCLUSIVE closes with an explanation and implies no verification.",
            })
        resolution = {
            "outcome": request.outcome,
            "summary": request.summary,
            "verification_ids": [str(item) for item in request.verification_ids],
            "change_ids": [str(item) for item in request.change_ids],
            "per_page": [item.model_dump(mode="json") for item in request.per_page],
            "completed_by": str(principal.principal_id),
        }
        cur = conn.cursor()
        cur.execute(
            "UPDATE docs.verification_requests SET status = 'COMPLETED', resolution = %s, completed_at = %s, claimed_by = coalesce(claimed_by, %s), claimed_at = coalesce(claimed_at, now()) WHERE request_id = %s",
            (_json(resolution), datetime.now(timezone.utc), principal.principal_id, str(request_id)),
        )
        append_event(
            conn,
            event_type="VERIFICATION_REQUEST_COMPLETED",
            channel="API",
            producer_id="docplane-verification",
            idempotency_key=f"VERIFICATION_REQUEST_COMPLETED:{principal.principal_id}:{key}",
            principal=principal,
            resource_type="VERIFICATION_REQUEST",
            resource_id=str(request_id),
            metadata={"outcome": request.outcome, "verifications": len(request.verification_ids), "changes": len(request.change_ids)},
        )
        response = _load_request(conn, request_id)
        save_receipt(conn, principal, key, "VERIFICATION_REQUEST_COMPLETE", str(request_id), digest, response)
        conn.commit()
        return response


@router.post("/api/v1/verification-requests/{request_id}/cancel")
def cancel_verification_request(
    request_id: UUID,
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    with get_conn() as conn:
        value = _load_request(conn, request_id, for_update=True)
        if value["status"] == "CANCELLED":
            return value
        if value["status"] == "COMPLETED":
            raise HTTPException(status_code=409, detail={"code": "VERIFICATION_REQUEST_COMPLETED"})
        cur = conn.cursor()
        cur.execute(
            "UPDATE docs.verification_requests SET status = 'CANCELLED', completed_at = %s WHERE request_id = %s",
            (datetime.now(timezone.utc), str(request_id)),
        )
        conn.commit()
        return _load_request(conn, request_id)


@router.get("/api/v1/maintenance/freshness")
def freshness(
    limit: int = Query(default=200, ge=1, le=1000),
    principal: Principal = Depends(require_contributor),
) -> dict[str, Any]:
    """The freshness table: least-recently-verified active pages first."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM docs.pages WHERE status = 'active'")
        total = int(cur.fetchone()[0])
        cur.execute(
            """
            SELECT p.resource_id::text, p.path, split_part(p.path, '/', 1) AS section,
                   p.title, p.updated_at, p.verification_state, p.criticality,
                   p.provenance, p.review_due_at, v.verified_at
              FROM docs.pages p
              LEFT JOIN LATERAL (
                    SELECT verified_at FROM docs.page_verifications
                     WHERE page_resource_id = p.resource_id AND verification_state = 'VERIFIED'
                     ORDER BY verified_at DESC LIMIT 1
                   ) v ON true
             WHERE p.status = 'active'
             ORDER BY v.verified_at ASC NULLS FIRST, p.updated_at ASC
             LIMIT %s
            """,
            (limit,),
        )
        keys = ("resource_id", "path", "section", "title", "updated_at", "verification_state", "criticality", "provenance", "review_due_at", "last_verified_at")
        rows = [dict(zip(keys, row)) for row in cur.fetchall()]
    return {"pages": rows, "count": len(rows), "total": total, "truncated": len(rows) < total}
