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
from app.work_inbox_cursor import decode_cursor, encode_cursor

router = APIRouter(tags=["work-inbox-v1"])


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


def _grant(conn, initiative: str, principal: str, *, write: bool = False) -> str:
    cur = conn.cursor()
    cur.execute("""SELECT participant_role, can_contribute FROM work.collaboration_grants g
        JOIN docplane.principals p ON p.principal_id = g.principal_id
        WHERE g.initiative_id = %s AND g.principal_id = %s AND g.revoked_at IS NULL
          AND (g.expires_at IS NULL OR g.expires_at > now()) AND p.status = 'ACTIVE'
          AND p.authorization_mode = 'WORK_COLLABORATOR' FOR SHARE OF g, p""", (initiative, principal))
    row = cur.fetchone()
    if row is None or (write and not row[1]):
        raise HTTPException(status_code=403, detail={"code": "COLLABORATION_SCOPE_DENIED"})
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


def _signature(envelope: dict[str, Any], secret: bytes) -> str:
    content = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hmac.new(secret, b"docplane-inbox-envelope-v1\0" + content, hashlib.sha256).hexdigest()


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
               principal: Principal = Depends(collaborator)):
    # Validate without FastAPI reflecting rejected input (which could be secret).
    if not isinstance(payload, dict) or set(payload) - {"recipient_id", "body", "reported_producer"}:
        raise HTTPException(status_code=422, detail={"code": "INBOX_REQUEST_INVALID"})
    try:
        recipient = str(UUID(payload["recipient_id"]))
        producer = payload.get("reported_producer")
        if producer is not None and (not isinstance(producer, str) or not producer.strip() or len(producer) > 120):
            raise ValueError("producer")
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
    digest = receipt_digest({"initiative": initiative, "recipient": recipient, "body": activity.body, "reported_producer": producer})
    with get_conn() as conn:
        role = _grant(conn, initiative, principal.principal_id, write=True)
        _grant(conn, initiative, recipient)
        replay = load_receipt(conn, principal, key, "WORK_INBOX_CONTRIBUTE", digest)
        if replay is not None:
            return replay
        cur = conn.cursor()
        cur.execute("INSERT INTO work.inbox_positions (initiative_id,recipient_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (initiative, recipient))
        cur.execute("UPDATE work.inbox_positions SET last_position=last_position+1 WHERE initiative_id=%s AND recipient_id=%s RETURNING last_position", (initiative, recipient))
        position = cur.fetchone()[0]
        cur.execute("""INSERT INTO work.initiative_activities (initiative_id,author_principal_id,idempotency_key,activity_type,body,metadata)
            VALUES (%s,%s,%s,'NOTE',%s,%s) RETURNING activity_id::text""", (initiative, principal.principal_id, key, activity.body, _json({"reported_producer": producer})))
        aid = cur.fetchone()[0]
        delivery = str(uuid4())
        kid, signing_key = _active_key()
        envelope = {"version": 1, "kid": kid, "delivery_id": delivery, "initiative_id": initiative, "recipient_id": recipient,
                    "position": position, "activity_id": aid, "sender_id": principal.principal_id,
                    "sender_role": role, "reported_producer": producer,
                    "body_sha256": hashlib.sha256(activity.body.encode()).hexdigest()}
        sig = _signature(envelope, signing_key)
        cur.execute("""INSERT INTO work.inbox_deliveries (delivery_id,initiative_id,recipient_id,position,activity_id,sender_id,sender_role,envelope,signature)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (delivery, initiative, recipient, position, aid, principal.principal_id, role, _json(envelope), sig))
        append_event(conn, event_type="WORK_INBOX_MESSAGE", channel="API", producer_id="work-inbox:" + principal.principal_id,
                     idempotency_key=key, principal=principal, resource_type="INITIATIVE", resource_id=initiative,
                     metadata={"activity_id": aid, "delivery_id": delivery})
        response = {"delivery_id": delivery, "activity_id": aid, "position": position}
        save_receipt(conn, principal, key, "WORK_INBOX_CONTRIBUTE", initiative, digest, response)
        conn.commit()
        return response


@router.get("/api/v1/work-inbox/{initiative_id}/messages")
def inbox(initiative_id: UUID, cursor: str | None = None, limit: int = Query(default=50, ge=1, le=100),
          principal: Principal = Depends(collaborator)):
    initiative = str(initiative_id)
    position = decode_cursor(cursor, principal_id=principal.principal_id, initiative_id=initiative)
    with get_conn() as conn:
        _grant(conn, initiative, principal.principal_id)
        cur = conn.cursor()
        cur.execute("""SELECT d.envelope,d.signature,a.body,d.acknowledged_at,d.delivery_id::text,d.position,d.activity_id::text,d.sender_id::text,d.sender_role FROM work.inbox_deliveries d
          JOIN work.initiative_activities a ON a.activity_id=d.activity_id
          WHERE d.initiative_id=%s AND d.recipient_id=%s AND d.position>%s ORDER BY d.position LIMIT %s""",
          (initiative, principal.principal_id, position, limit))
        items = []
        keys = _keyring()
        for envelope, signature, body, ack, delivery_id, db_position, activity_id, sender_id, sender_role in cur.fetchall():
            expected_scope = {"delivery_id": delivery_id, "initiative_id": initiative, "recipient_id": principal.principal_id, "position": db_position, "activity_id": activity_id, "sender_id": sender_id, "sender_role": sender_role}
            # Never project unverified envelope fields, including claimed sender.
            # The database position is independently scoped by the query and lets
            # a recipient progress past a damaged row without trusting it.
            valid = False
            try:
                kid = envelope.get("kid")
                key = keys.get(kid) if isinstance(kid, str) else None
                valid = (key is not None and all(envelope.get(k) == v for k, v in expected_scope.items())
                         and hmac.compare_digest(_signature(envelope, key), signature)
                         and hmac.compare_digest(envelope["body_sha256"], hashlib.sha256(body.encode()).hexdigest()))
            except (AttributeError, KeyError, TypeError, ValueError):
                valid = False
            position = db_position
            if not valid:
                items.append({"delivery_id": delivery_id, "position": db_position, "integrity_failed": True,
                              "envelope": None, "signature": None, "body": None, "body_withheld": True,
                              "acknowledged": ack is not None})
                continue
            redacted, withheld = _redacted_activity_body(body)
            items.append({"delivery_id": delivery_id, "position": db_position, "integrity_failed": False,
                          "envelope": envelope, "signature": signature, "body": redacted,
                          "body_withheld": withheld, "acknowledged": ack is not None})

        return {"messages": items, "next_cursor": encode_cursor(position, principal_id=principal.principal_id, initiative_id=initiative)}


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
