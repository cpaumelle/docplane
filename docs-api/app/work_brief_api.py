"""Bounded context and attributable work updates over existing WORK activities."""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import TypeAdapter, ValidationError

from app.agent_auth import Principal, require_contributor
from app.db import get_conn
from app.event_store import append_event
from app.model_contracts import secret_findings
from app.mutation_receipts import load_receipt, receipt_digest, save_receipt
from app.work_api import _activity_key, _json, _load, _redacted_activity_body, _redacted_short_text
from app.work_brief import CONTRACT, WorkItem, WorkUpdate, group_items, merge_items

router = APIRouter(tags=["work-v1"])


def _checkpoint(conn, initiative_id):
    cur = conn.cursor()
    cur.execute("""SELECT activity_id::text, metadata, created_at, author_principal_id::text
                     FROM work.initiative_activities
                    WHERE initiative_id = %s AND metadata ? 'work_checkpoint'
                    ORDER BY metadata->'work_checkpoint'->'revision' DESC NULLS LAST, created_at DESC, activity_id DESC LIMIT 1""", (str(initiative_id),))
    row = cur.fetchone()
    if row is None:
        return None, [], None
    try:
        checkpoint = row[1]["work_checkpoint"]
        if checkpoint["contract"] != CONTRACT or type(checkpoint.get("revision")) is not int or type(checkpoint.get("activity_count")) is not int:
            raise ValueError("unsupported checkpoint")
        items = TypeAdapter(list[WorkItem]).validate_python(checkpoint["items"])
        if len({item.item_id for item in items}) != len(items):
            raise ValueError("duplicate item")
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise HTTPException(409, detail={"code": "WORK_CHECKPOINT_UNREADABLE"}) from exc
    return row[0], [item.model_dump(mode="json") for item in items], {
        "activity_id": row[0], "created_at": row[2], "author_principal_id": row[3],
        "activity_count": checkpoint["activity_count"],
        "reported_producer": row[1].get("reported_producer"),
    }


def _context(conn, initiative):
    cur = conn.cursor()
    cur.execute("""SELECT count(*), (array_agg(activity_id::text ORDER BY created_at DESC, activity_id DESC))[1]
                     FROM work.initiative_activities WHERE initiative_id = %s""", (initiative["initiative_id"],))
    count, latest = cur.fetchone()
    # Initiative version covers transitions; count/latest covers appended
    # evidence. Wall-clock read time is deliberately absent from the token.
    return receipt_digest({"initiative_id": initiative["initiative_id"], "version": initiative["version"],
                           "activity_count": count, "latest_activity_id": latest}), int(count)


def _safe_object(value):
    raw = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    safe, refused = _redacted_activity_body(raw)
    return not refused and raw == safe


def _brief(conn, initiative):
    checkpoint_id, items, checkpoint = _checkpoint(conn, initiative["initiative_id"])
    token, count = _context(conn, initiative)
    cur = conn.cursor()
    cur.execute("""SELECT activity_id::text, activity_type, body, created_at, author_principal_id::text
                     FROM work.initiative_activities WHERE initiative_id = %s
                    ORDER BY created_at DESC, activity_id DESC LIMIT 6""", (initiative["initiative_id"],))
    rows = cur.fetchall()
    recent = []
    for row in rows[:5]:
        body, withheld = _redacted_activity_body(row[2])
        # No mid-sentence shortening: long bodies remain accessible through
        # the ordinary paged history instead of flooding the agent brief.
        omitted = body is not None and len(body.encode("utf-8")) > 2000
        recent.append({"activity_id": row[0], "activity_type": row[1], "body": None if omitted else body,
                       "body_withheld": withheld, "body_in_history": omitted,
                       "created_at": row[3], "author_principal_id": row[4]})
    safe_initiative = {field: initiative.get(field) for field in (
        "initiative_id", "initiative_key", "work_state", "priority", "version", "owner_principal_id", "updated_at",
        "parked_indefinitely", "parked_review_at", "soak_review_at")}
    for field, maximum in (("title", 300), ("objective", 5000), ("scope", 10000),
                           ("blocker_summary", 4000), ("parked_reason", 4000)):
        if initiative.get(field) is not None:
            safe_initiative[field], safe_initiative[field + "_withheld"] = _redacted_short_text(initiative[field], maximum)
    withheld = not _safe_object({"items": items, "checkpoint": checkpoint})
    exposed_items = [] if withheld else items
    groups = group_items(exposed_items, initiative["work_state"])
    groups = {key: [item["item_id"] for item in value] if isinstance(value, list) else value
              for key, value in groups.items()}
    return {"contract": CONTRACT, "initiative": safe_initiative, "context_token": token,
            "checkpoint": None if withheld else checkpoint, "checkpoint_withheld": withheld,
            "checkpoint_status": "WITHHELD" if withheld else ("RECORDED" if checkpoint_id else "NOT_SET"),
            "items": exposed_items, **groups,
            "activities_since_checkpoint": None if not checkpoint else max(0, count - checkpoint["activity_count"]),
            "counts": {"items": None if withheld else len(items), "activities": count},
            "recent_activity": recent, "recent_activity_has_more": len(rows) > 5,
            "history_url": f"/api/v1/initiatives/{initiative['initiative_id']}?activity_bodies=redacted",
            "evidence_policy": "Recorded claims; references are not live verification or execution approval.",
            "context_policy": "Read recent evidence and applicable decisions before updating. Unstructured notes do not update items automatically."}


@router.get("/api/v1/initiatives/{initiative_id}/brief")
def work_brief(initiative_id: UUID, principal: Principal = Depends(require_contributor)):
    with get_conn() as conn:
        # Serialise reads with checkpoint updates and ordinary activity appends.
        initiative = _load(conn, initiative_id, for_update=True)
        return _brief(conn, initiative)


def _request(payload):
    try:
        request = WorkUpdate.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, detail={"code": "WORK_UPDATE_INVALID", "validation_codes": sorted({
            item["type"] for item in exc.errors(include_input=False)})[:20]}) from exc
    dumped = request.model_dump(mode="json")
    if len(json.dumps(dumped, ensure_ascii=False).encode("utf-8")) > 32000:
        raise HTTPException(413, detail={"code": "WORK_UPDATE_TOO_LARGE"})
    # The context is a server-generated hash in a dedicated typed field,
    # not user-authored text. Canonical scanning still covers every text field.
    text_payload = {key: value for key, value in dumped.items() if key != "expected_context"}
    if secret_findings(text_payload) or not _safe_object(text_payload):
        raise HTTPException(422, detail={"code": "WORK_UPDATE_TEXT_REFUSED"})
    return request


def _check_evidence(conn, request):
    # Check local record identities. External references remain recorded claims;
    # do not perform HTTP/SSH calls or treat successful lookup as truth.
    queries = {
        "ACTIVITY": "SELECT 1 FROM work.initiative_activities WHERE activity_id::text = %s",
        "CAPTURE": "SELECT 1 FROM work.captures WHERE capture_id::text = %s",
        "PAGE": "SELECT 1 FROM docs.pages WHERE path = %s",
    }
    for change in request.changes:
        if change.decision and not any(ref.reference_type in {"ACTIVITY", "CAPTURE", "PAGE"} for ref in change.decision.references):
            raise HTTPException(422, detail={"code": "WORK_DECISION_SOURCE_REQUIRED"})
    refs = {(ref.reference_type, ref.reference_id) for change in request.changes
            for ref in [*change.item.references, *(change.decision.references if change.decision else [])]}
    cur = conn.cursor()
    for kind, ref in sorted(refs):
        if kind in queries:
            cur.execute(queries[kind], (ref,))
            if cur.fetchone() is None:
                raise HTTPException(422, detail={"code": "WORK_EVIDENCE_NOT_FOUND", "reference_type": kind})


@router.post("/api/v1/initiatives/{initiative_id}/updates", status_code=201,
             openapi_extra={"requestBody": {"required": True, "content": {"application/json": {
                 "schema": WorkUpdate.model_json_schema()}}}, "parameters": [{"name": "Idempotency-Key",
                 "in": "header", "required": True, "schema": {"type": "string", "format": "uuid"}}]})
def work_update(initiative_id: UUID, payload: dict = Body(...),
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", include_in_schema=False),
                principal: Principal = Depends(require_contributor)):
    key = _activity_key(idempotency_key)
    request = _request(payload)
    digest = receipt_digest({"route": "work-update-v1", "initiative_id": str(initiative_id),
                             **request.model_dump(mode="json")})
    with get_conn() as conn:
        replayed = load_receipt(conn, principal, key, "WORK_UPDATE", digest)
        if replayed is not None:
            return replayed
        initiative = _load(conn, initiative_id, for_update=True)
        if initiative["work_state"] in {"COMPLETE", "ABANDONED"}:
            raise HTTPException(409, detail={"code": "WORK_INITIATIVE_CLOSED"})
        context, activity_count = _context(conn, initiative)
        if request.expected_context != context:
            raise HTTPException(409, detail={"code": "WORK_CONTEXT_STALE", "remedy": "Read the brief and rebase the update with a new idempotency key."})
        _, previous, _ = _checkpoint(conn, initiative_id)
        if not _safe_object(previous):
            raise HTTPException(409, detail={"code": "WORK_CHECKPOINT_WITHHELD"})
        items = merge_items(previous, request)
        _check_evidence(conn, request)
        metadata = {"work_checkpoint": {"contract": CONTRACT, "revision": initiative["version"] + 1, "activity_count": activity_count + 1, "items": items},
                    "reported_producer": request.reported_producer,
                    "changes": [change.model_dump(mode="json") for change in request.changes]}
        # A readable activity accompanies the typed checkpoint. Old clients
        # still see progress, state, explicit holds and evidence in the history.
        lines = [request.summary, "", f"Reported producer: {request.reported_producer} (self-reported)"]
        for change in request.changes:
            item = change.item
            lines.extend(["", f"{item.item_id}: {item.title} [{item.state}; {item.stance}]", item.reason])
            if item.next_action:
                lines.append("Next action: " + item.next_action)
            lines.extend(f"Evidence {ref.reference_type}: {ref.reference_id}" for ref in item.references)
            if change.decision:
                lines.append("Decision: " + change.decision.reason)
                lines.extend(f"Decision evidence {ref.reference_type}: {ref.reference_id}" for ref in change.decision.references)
        body = "\n".join(lines)
        if len(body.encode("utf-8")) > 20000:
            raise HTTPException(413, detail={"code": "WORK_UPDATE_TOO_LARGE"})
        cur = conn.cursor()
        cur.execute("UPDATE work.initiatives SET version = version + 1, updated_at = clock_timestamp() WHERE initiative_id = %s", (str(initiative_id),))
        cur.execute("""INSERT INTO work.initiative_activities
                           (initiative_id, author_principal_id, idempotency_key, activity_type, body, metadata, created_at)
                       VALUES (%s, %s, %s, 'NOTE', %s, %s, clock_timestamp()) RETURNING activity_id::text, created_at""",
                    (str(initiative_id), principal.principal_id, key, body, _json(metadata)))
        activity_id, created_at = cur.fetchone()
        append_event(conn, event_type="WORK_CHECKPOINT_RECORDED", channel="API", producer_id="docplane-work",
                     idempotency_key=f"WORK_CHECKPOINT_RECORDED:{principal.principal_id}:{key}", principal=principal,
                     workspace_key=initiative["workspace_key"], resource_type="INITIATIVE", resource_id=str(initiative_id),
                     metadata={"activity_id": activity_id, "changed_item_ids": [change.item.item_id for change in request.changes]})
        response = {"contract": CONTRACT, "initiative_id": str(initiative_id), "activity_id": activity_id,
                    "author_principal_id": principal.principal_id, "created_at": created_at.isoformat(),
                    "changed_item_ids": [change.item.item_id for change in request.changes],
                    "item_count": len(items)}
        save_receipt(conn, principal, key, "WORK_UPDATE", str(initiative_id), digest, response)
        conn.commit()
        return response
