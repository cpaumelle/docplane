"""Addressed WORK collaboration. Peer content is evidence, never authority."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent_auth import Principal, authenticate, issue_token, require_bootstrap_token
from app.db import get_conn
from app.inbox_secrets import InboxSecretError, read_secret, read_key_directory
from app.event_store import append_event
from app.mutation_receipts import load_receipt, receipt_digest, save_receipt
from app.work_api import _activity_key, _activity_request, _json, _redacted_activity_body, _redacted_short_text
from app import work_inbox_context as message_context
from app.work_inbox_cursor import decode_cursor, encode_cursor

router = APIRouter(tags=["work-inbox-v1"])

# Pages stop before the 1 MiB response cap that adapters enforce.
PAGE_BYTES = 700_000
_LIVE_GRANT = """g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at > now())
          AND p.status = 'ACTIVE' AND p.authorization_mode = 'WORK_COLLABORATOR'"""


class Grant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["AGENT", "ARCHITECT"]
    can_contribute: bool = False
    expires_at: datetime | None = None


class Participant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=200)
    expires_at: datetime | None = None


def collaborator(authorization: str | None = Header(default=None, include_in_schema=False)) -> Principal:
    principal = authenticate(authorization)
    if principal.authorization_mode != "WORK_COLLABORATOR":
        raise HTTPException(status_code=403, detail={"code": "COLLABORATION_IDENTITY_REQUIRED"})
    return principal


def _grant(conn, initiative: str, principal: str, *, write: bool = False, refusal: str = "COLLABORATION_SCOPE_DENIED") -> str:
    cur = conn.cursor()
    cur.execute("""SELECT participant_role, can_contribute FROM work.collaboration_grants g
        JOIN docplane.principals p ON p.principal_id = g.principal_id
        WHERE g.initiative_id = %s AND g.principal_id = %s AND g.revoked_at IS NULL
          AND (g.expires_at IS NULL OR g.expires_at > now()) AND p.status = 'ACTIVE'
          AND p.authorization_mode = 'WORK_COLLABORATOR' FOR SHARE OF g, p""", (initiative, principal))
    row = cur.fetchone()
    if row is None or (write and not row[1]):
        raise HTTPException(status_code=403, detail={"code": refusal})
    return row[0]


def _keyring() -> dict[str, bytes]:
    try:
        directory = os.environ.get("DOCPLANE_WORK_INBOX_SIGNING_KEYS_DIR", "")
        if "DOCPLANE_WORK_INBOX_SIGNING_KEYS" in os.environ or not directory:
            raise InboxSecretError("signing key directory required")
        raw = read_key_directory(directory)
        if not isinstance(raw, dict) or not raw or any(
            not isinstance(k, str) or not k or len(k) > 64 or not isinstance(v, str) or len(v.encode()) < 32
            for k, v in raw.items()
        ):
            raise ValueError("invalid keyring")
        return {k: v.encode() for k, v in raw.items()}
    except (ValueError, TypeError, InboxSecretError):
        raise HTTPException(status_code=503, detail={"code": "INBOX_SIGNING_KEYS_UNAVAILABLE"}) from None


def _active_key() -> tuple[str, bytes]:
    keys = _keyring()
    try:
        kid = read_secret("DOCPLANE_WORK_INBOX_ACTIVE_KID")
    except InboxSecretError as exc:
        raise HTTPException(status_code=503, detail={"code": "INBOX_SIGNING_KEYS_UNAVAILABLE"}) from exc
    if kid not in keys:
        raise HTTPException(status_code=503, detail={"code": "INBOX_SIGNING_KEYS_UNAVAILABLE"})
    return kid, keys[kid]


_signature = message_context.envelope_signature


@router.post("/api/v1/bootstrap/work-participant-identities", status_code=201)
def create_participant_identity(request: Participant,
                                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                                bootstrap: str | None = Header(default=None, alias="X-DocPlane-Bootstrap-Token")):
    """Create only a principal. Issue its first token through governed capture.

    The receipt contains no credential, so a lost response can be replayed
    without generating an orphan bearer. The existing idempotent token-issue
    endpoint and SecretHandle rotation adapter then own credential capture.
    """
    require_bootstrap_token(bootstrap)
    key = _activity_key(idempotency_key)
    if not request.display_name.strip() or request.expires_at is not None:
        raise HTTPException(status_code=422, detail={"code": "PARTICIPANT_IDENTITY_INVALID"})
    _activity_request({"activity_type": "NOTE", "body": request.display_name.strip()})
    clean, withheld = _redacted_short_text(request.display_name.strip(), 200)
    if withheld or clean != request.display_name.strip():
        raise HTTPException(status_code=422, detail={"code": "PARTICIPANT_IDENTITY_INVALID"})
    digest = receipt_digest({"display_name": request.display_name.strip()})
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 75))", ("work-participant-identity:" + key,))
        cur.execute("SELECT metadata FROM docplane.events WHERE producer_id=%s AND idempotency_key=%s",
                    ("work-participant-identity", key))
        row = cur.fetchone()
        if row:
            if row[0].get("request_hash") != digest:
                raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED"})
            return row[0]["response"]
        cur.execute("""INSERT INTO docplane.principals (principal_kind,display_name,authorization_mode)
                       VALUES ('AGENT',%s,'WORK_COLLABORATOR') RETURNING principal_id::text""",
                    (request.display_name.strip(),))
        pid = cur.fetchone()[0]
        response = {"principal_id": pid, "authorization_mode": "WORK_COLLABORATOR"}
        append_event(conn, event_type="WORK_PARTICIPANT_IDENTITY_CREATED", channel="API",
                     producer_id="work-participant-identity", idempotency_key=key,
                     resource_type="PRINCIPAL", resource_id=pid,
                     metadata={"request_hash": digest, "response": response})
        conn.commit()
        return response


@router.post("/api/v1/bootstrap/work-participants", status_code=201)
def create_participant(request: Participant, bootstrap: str | None = Header(default=None, alias="X-DocPlane-Bootstrap-Token")):
    require_bootstrap_token(bootstrap)
    if not request.display_name.strip():
        raise HTTPException(status_code=422, detail={"code": "PARTICIPANT_INVALID"})
    expires_at = request.expires_at or (datetime.now(timezone.utc) + timedelta(days=90))
    clear, digest, prefix = issue_token()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO docplane.principals (principal_kind, display_name, authorization_mode)
            VALUES ('AGENT', %s, 'WORK_COLLABORATOR') RETURNING principal_id::text""", (request.display_name.strip(),))
        pid = cur.fetchone()[0]
        cur.execute("""INSERT INTO docplane.api_tokens (principal_id, token_hash, token_prefix, description, expires_at)
            VALUES (%s, %s, %s, 'WORK collaboration participant', %s)""", (pid, digest, prefix, expires_at))
        append_event(conn, event_type="WORK_PARTICIPANT_CREATED", channel="API", producer_id="work-inbox-bootstrap", idempotency_key=str(uuid4()), resource_type="PRINCIPAL", resource_id=pid)
        conn.commit()
    return {"principal_id": pid, "token": clear, "expires_at": expires_at}


@router.put("/api/v1/bootstrap/work-grants/{initiative_id}/{principal_id}")
def put_grant(initiative_id: UUID, principal_id: UUID, request: Grant,
              bootstrap: str | None = Header(default=None, alias="X-DocPlane-Bootstrap-Token")):
    require_bootstrap_token(bootstrap)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM docplane.principals WHERE principal_id=%s AND authorization_mode='WORK_COLLABORATOR' AND status='ACTIVE'", (str(principal_id),))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail={"code": "PARTICIPANT_NOT_FOUND"})
        cur.execute("SELECT 1 FROM work.initiatives WHERE initiative_id=%s", (str(initiative_id),))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail={"code": "INITIATIVE_NOT_FOUND"})
        cur.execute("""INSERT INTO work.collaboration_grants (initiative_id, principal_id, participant_role, can_contribute, expires_at)
          VALUES (%s,%s,%s,%s,%s) ON CONFLICT (initiative_id,principal_id) DO UPDATE SET
          participant_role=EXCLUDED.participant_role, can_contribute=EXCLUDED.can_contribute,
          expires_at=EXCLUDED.expires_at, revoked_at=NULL""", (str(initiative_id), str(principal_id), request.role, request.can_contribute, request.expires_at))
        append_event(conn, event_type="WORK_GRANT_SET", channel="API", producer_id="work-inbox-bootstrap", idempotency_key=str(uuid4()), resource_type="INITIATIVE", resource_id=str(initiative_id), metadata={"participant_id": str(principal_id), "role": request.role, "can_contribute": request.can_contribute})
        conn.commit()
    return {"initiative_id": str(initiative_id), "principal_id": str(principal_id)}


@router.delete("/api/v1/bootstrap/work-grants/{initiative_id}/{principal_id}")
def revoke_grant(initiative_id: UUID, principal_id: UUID, bootstrap: str | None = Header(default=None, alias="X-DocPlane-Bootstrap-Token")):
    require_bootstrap_token(bootstrap)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE work.collaboration_grants SET revoked_at=now() WHERE initiative_id=%s AND principal_id=%s", (str(initiative_id), str(principal_id)))
        if cur.rowcount != 1:
            raise HTTPException(status_code=404, detail={"code": "GRANT_NOT_FOUND"})
        append_event(conn, event_type="WORK_GRANT_REVOKED", channel="API", producer_id="work-inbox-bootstrap", idempotency_key=str(uuid4()), resource_type="INITIATIVE", resource_id=str(initiative_id), metadata={"participant_id": str(principal_id)})
        conn.commit()
    return {"revoked": True}


@router.post("/api/v1/work-inbox/{initiative_id}/messages", status_code=201)
def contribute(initiative_id: UUID, payload: Any = Body(...),
               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
               contract: int = Query(default=1, ge=1, le=2),
               principal: Principal = Depends(collaborator)):
    # Validate without FastAPI reflecting rejected input (which could be secret).
    if not isinstance(payload, dict) or set(payload) - {"recipient_id", "body", "reported_producer", "kind", "in_reply_to"}:
        raise HTTPException(status_code=422, detail={"code": "INBOX_REQUEST_INVALID"})
    try:
        recipient = str(UUID(payload["recipient_id"]))
        producer = payload.get("reported_producer")
        if producer is not None and (not isinstance(producer, str) or not producer.strip() or len(producer) > 120):
            raise ValueError("producer")
        extra = message_context.parse(payload)
        activity = _activity_request({"activity_type": "NOTE", "body": payload["body"]})
        _, body_withheld = _redacted_activity_body(activity.body)
        if body_withheld:
            raise ValueError("body refused")
        if producer is not None:
            _activity_request({"activity_type": "NOTE", "body": producer})
            clean_producer, withheld = _redacted_short_text(producer, 120)
            if withheld or clean_producer != producer:
                raise ValueError("producer")
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail={"code": "INBOX_REQUEST_INVALID"}) from exc
    key = _activity_key(idempotency_key)
    initiative = str(initiative_id)
    intent = {"initiative": initiative, "recipient": recipient, "body": activity.body, "reported_producer": producer}
    if extra is not None:
        # Added only when present, so receipts written under contract 1 still replay.
        intent.update(kind=extra[0], in_reply_to=extra[1])
    digest = receipt_digest(intent)
    with get_conn() as conn:
        role = _grant(conn, initiative, principal.principal_id, write=True)
        replay = load_receipt(conn, principal, key, "WORK_INBOX_CONTRIBUTE", digest)
        if replay is not None:
            # Opt-in, so contract 1 adapters still see the receipt byte for byte.
            return {**replay, "replayed": True} if contract == 2 else replay
        # After the replay: a committed send keeps its receipt if the recipient is later revoked.
        _grant(conn, initiative, recipient, refusal="RECIPIENT_NOT_GRANTED")
        cur = conn.cursor()
        delivery = str(uuid4())
        thread = None
        if extra is not None:
            thread = message_context.thread_for(cur, initiative=initiative, sender=principal.principal_id,
                                                recipient=recipient, kind=extra[0], parent=extra[1], delivery=delivery,
                                                keys=_keyring() if extra[1] else {})
        cur.execute("INSERT INTO work.inbox_positions (initiative_id,recipient_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (initiative, recipient))
        cur.execute("UPDATE work.inbox_positions SET last_position=last_position+1 WHERE initiative_id=%s AND recipient_id=%s RETURNING last_position", (initiative, recipient))
        position = cur.fetchone()[0]
        cur.execute("""INSERT INTO work.initiative_activities (initiative_id,author_principal_id,idempotency_key,activity_type,body,metadata)
            VALUES (%s,%s,%s,'NOTE',%s,%s) RETURNING activity_id::text""", (initiative, principal.principal_id, key, activity.body, _json({"reported_producer": producer})))
        aid = cur.fetchone()[0]
        kid, signing_key = _active_key()
        envelope = {"version": 1, "kid": kid, "delivery_id": delivery, "initiative_id": initiative, "recipient_id": recipient,
                    "position": position, "activity_id": aid, "sender_id": principal.principal_id,
                    "sender_role": role, "reported_producer": producer,
                    "body_sha256": hashlib.sha256(activity.body.encode()).hexdigest()}
        sig = _signature(envelope, signing_key)
        cur.execute("""INSERT INTO work.inbox_deliveries (delivery_id,initiative_id,recipient_id,position,activity_id,sender_id,sender_role,envelope,signature)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (delivery, initiative, recipient, position, aid, principal.principal_id, role, _json(envelope), sig))
        if extra is not None:
            context = {"kind": extra[0], "in_reply_to": extra[1], "thread_id": thread}
            cur.execute("""INSERT INTO work.inbox_message_context (delivery_id,kind,in_reply_to,thread_id,kid,signature)
                VALUES (%s,%s,%s,%s,%s,%s)""", (delivery, extra[0], extra[1], thread, kid,
                                                message_context.sign(envelope, context, signing_key)))
        append_event(conn, event_type="WORK_INBOX_MESSAGE", channel="API", producer_id="work-inbox:" + principal.principal_id,
                     idempotency_key=key, principal=principal, resource_type="INITIATIVE", resource_id=initiative,
                     metadata={"activity_id": aid, "delivery_id": delivery})
        response = {"delivery_id": delivery, "activity_id": aid, "position": position}
        if thread is not None:
            response["thread_id"] = thread
        save_receipt(conn, principal, key, "WORK_INBOX_CONTRIBUTE", initiative, digest, response)
        conn.commit()
        return {**response, "replayed": False} if contract == 2 else response


@router.get("/api/v1/work-inbox/{initiative_id}/messages")
def inbox(initiative_id: UUID, cursor: str | None = None, limit: int = Query(default=50, ge=1, le=100),
          contract: int = Query(default=1, ge=1, le=2), view: Literal["all", "open"] = "all",
          principal: Principal = Depends(collaborator)):
    """One page. Contract 1 responses keep exactly their original fields."""
    if view == "open" and contract < 2:
        raise HTTPException(status_code=422, detail={"code": "INBOX_CONTRACT_REQUIRED"})
    initiative = str(initiative_id)
    position = decode_cursor(cursor, principal_id=principal.principal_id, initiative_id=initiative, view=view)
    with get_conn() as conn:
        _grant(conn, initiative, principal.principal_id)
        cur = conn.cursor()
        scope = "d.initiative_id=%s AND d.recipient_id=%s AND d.position>%s"
        params = [initiative, principal.principal_id, position]
        open_ids = None
        if view == "open":
            open_ids = message_context.Threads(cur, initiative, principal.principal_id, _keyring()).open_after(position)
            scope += " AND d.delivery_id = ANY(%s::uuid[])"
            params.append(open_ids)
        cur.execute(f"""SELECT d.envelope,d.signature,a.body,d.acknowledged_at,d.delivery_id::text,d.position,d.activity_id::text,d.sender_id::text,d.sender_role,
            c.kind,c.in_reply_to::text,c.thread_id::text,c.kid,c.signature
          FROM work.inbox_deliveries d
          JOIN work.initiative_activities a ON a.activity_id=d.activity_id
          LEFT JOIN work.inbox_message_context c ON c.delivery_id=d.delivery_id
          WHERE {scope} ORDER BY d.position LIMIT %s""",
          (*params, limit))
        items = []
        keys = _keyring()
        size = 0
        for envelope, signature, body, ack, delivery_id, db_position, activity_id, sender_id, sender_role, *context_row in cur.fetchall():
            expected_scope = {"delivery_id": delivery_id, "initiative_id": initiative, "recipient_id": principal.principal_id, "position": db_position, "activity_id": activity_id, "sender_id": sender_id, "sender_role": sender_role}
            # Never project unverified envelope fields, including claimed sender.
            # The database position is independently scoped by the query and lets
            # a recipient progress past a damaged row without trusting it.
            valid = False
            try:
                valid = message_context.envelope_intact(envelope, signature, hashlib.sha256(body.encode()).hexdigest(),
                                                        expected_scope, keys)
            except (AttributeError, KeyError, TypeError, ValueError):
                valid = False
            context = None
            if valid:
                context, valid = message_context.verified(envelope, tuple(context_row), keys)
            if valid:
                redacted, withheld = _redacted_activity_body(body)
                item = {"delivery_id": delivery_id, "position": db_position, "integrity_failed": False,
                        "envelope": envelope, "signature": signature, "body": redacted,
                        "body_withheld": withheld, "acknowledged": ack is not None}
            else:
                context = None
                item = {"delivery_id": delivery_id, "position": db_position, "integrity_failed": True,
                        "envelope": None, "signature": None, "body": None, "body_withheld": True,
                        "acknowledged": ack is not None}
            if contract == 2:
                item["context"] = context
            cost = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode())
            if items and size + cost > PAGE_BYTES:
                break  # the cursor stays on the last item returned
            size += cost
            position = db_position
            items.append(item)

        page = {"messages": items, "next_cursor": encode_cursor(position, principal_id=principal.principal_id, initiative_id=initiative, view=view)}
        if contract == 2:
            if open_ids is not None:
                remaining = len(open_ids) - len(items)
            else:
                cur.execute("""SELECT count(*) FROM work.inbox_deliveries
                  WHERE initiative_id=%s AND recipient_id=%s AND position>%s""", (initiative, principal.principal_id, position))
                remaining = cur.fetchone()[0]
            page.update(remaining=remaining, view=view)
        return page


@router.post("/api/v1/work-inbox/{initiative_id}/messages/{delivery_id}/ack")
def acknowledge(initiative_id: UUID, delivery_id: UUID, principal: Principal = Depends(collaborator)):
    with get_conn() as conn:
        _grant(conn, str(initiative_id), principal.principal_id)
        cur = conn.cursor()
        cur.execute("""UPDATE work.inbox_deliveries SET acknowledged_at=now()
          WHERE initiative_id=%s AND recipient_id=%s AND delivery_id=%s AND acknowledged_at IS NULL RETURNING acknowledged_at""",
          (str(initiative_id), principal.principal_id, str(delivery_id)))
        row = cur.fetchone()
        if row is not None:
            append_event(conn, event_type="WORK_INBOX_ACKNOWLEDGED", channel="API",
                         producer_id="work-inbox-ack:" + principal.principal_id,
                         idempotency_key=str(delivery_id), principal=principal,
                         resource_type="INITIATIVE", resource_id=str(initiative_id),
                         metadata={"delivery_id": str(delivery_id)})
        else:
            cur.execute("SELECT acknowledged_at FROM work.inbox_deliveries WHERE initiative_id=%s AND recipient_id=%s AND delivery_id=%s",
                        (str(initiative_id), principal.principal_id, str(delivery_id)))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail={"code": "DELIVERY_NOT_FOUND"})

        conn.commit()
    return {"delivery_id": str(delivery_id), "acknowledged_at": row[0].isoformat(), "meaning": "RECEIVED"}


@router.get("/api/v1/work-inbox")
def my_initiatives(principal: Principal = Depends(collaborator)):
    """The caller's live grants, with enough position to poll without reading bodies."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"""SELECT i.initiative_id::text, i.initiative_key, g.participant_role, g.can_contribute,
              (SELECT count(*) FROM work.inbox_deliveries d WHERE d.initiative_id=g.initiative_id
                 AND d.recipient_id=g.principal_id AND d.acknowledged_at IS NULL),
              COALESCE((SELECT last_position FROM work.inbox_positions n WHERE n.initiative_id=g.initiative_id
                 AND n.recipient_id=g.principal_id), 0)
            FROM work.collaboration_grants g
            JOIN docplane.principals p ON p.principal_id = g.principal_id
            JOIN work.initiatives i ON i.initiative_id = g.initiative_id
            WHERE g.principal_id = %s AND {_LIVE_GRANT} ORDER BY i.initiative_key, i.initiative_id""",
                    (principal.principal_id,))
        rows = cur.fetchall()
    return {"participant_id": principal.principal_id, "initiatives": [
        {"initiative_id": iid, "initiative_key": _redacted_short_text(key, 96)[0], "role": role,
         "can_contribute": write, "unacknowledged": unread, "latest_position": latest}
        for iid, key, role, write, unread, latest in rows]}


@router.get("/api/v1/work-inbox/{initiative_id}/participants")
def participants(initiative_id: UUID, principal: Principal = Depends(collaborator)):
    """Who the caller can address: live grants on an initiative it holds a grant on."""
    initiative = str(initiative_id)
    with get_conn() as conn:
        _grant(conn, initiative, principal.principal_id)
        cur = conn.cursor()
        cur.execute(f"""SELECT p.principal_id::text, p.display_name, g.participant_role, g.can_contribute
            FROM work.collaboration_grants g JOIN docplane.principals p ON p.principal_id = g.principal_id
            WHERE g.initiative_id = %s AND {_LIVE_GRANT} ORDER BY p.display_name, p.principal_id""", (initiative,))
        rows = cur.fetchall()
    return {"participant_id": principal.principal_id, "participants": [
        {"participant_id": pid, "name": _redacted_short_text(name, 200)[0], "role": role, "can_contribute": write}
        for pid, name, role, write in rows]}


@router.get("/api/v1/work-inbox/{initiative_id}/sent")
def sent(initiative_id: UUID, delivery_id: UUID | None = None, before: UUID | None = None,
         limit: int = Query(default=50, ge=1, le=100), principal: Principal = Depends(collaborator)):
    """The caller's own deliveries, newest first: receipt and latest verified reply. No bodies.

    Page with `before`: the last delivery_id of the previous page.
    """
    initiative = str(initiative_id)
    only = str(delivery_id) if delivery_id else None
    with get_conn() as conn:
        _grant(conn, initiative, principal.principal_id)
        cur = conn.cursor()
        edge = (None, None)
        if before:
            cur.execute("""SELECT created_at, delivery_id::text FROM work.inbox_deliveries
                WHERE delivery_id=%s AND initiative_id=%s AND sender_id=%s""", (str(before), initiative, principal.principal_id))
            edge = cur.fetchone()
            if edge is None:
                raise HTTPException(status_code=422, detail={"code": "INBOX_PAGE_INVALID"})
        cur.execute("""SELECT delivery_id::text, activity_id::text, recipient_id::text, created_at, acknowledged_at
            FROM work.inbox_deliveries
            WHERE initiative_id = %s AND sender_id = %s AND (%s::uuid IS NULL OR delivery_id = %s::uuid)
              AND (%s::timestamptz IS NULL OR created_at < %s::timestamptz
                   OR (created_at = %s::timestamptz AND delivery_id > %s::uuid))
            ORDER BY created_at DESC, delivery_id LIMIT %s""",
                    (initiative, principal.principal_id, only, only, edge[0], edge[0], edge[0], edge[1], limit + 1))
        rows = cur.fetchall()
        threads = message_context.Threads(cur, initiative, principal.principal_id, _keyring())
        out = []
        for did, aid, recipient, created, acked in rows[:limit]:
            row = threads.rows.get(did)
            context = {k: row[k] for k in ("kind", "in_reply_to", "thread_id")} if row and row["intact"] else None
            out.append({"delivery_id": did, "activity_id": aid, "recipient_id": recipient, "created_at": created,
                        "acknowledged_at": acked, "context": context, "latest_reply": threads.latest_reply(did)})
    return {"sent": out, "more": len(rows) > limit}
