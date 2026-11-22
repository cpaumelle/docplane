"""Inbox contract 2: kind, correlation and what is still open.

Context is signed separately from the delivery envelope and bound to it, so the envelope
every deployed adapter validates is unchanged. Completion is derived from later deliveries,
never asserted by an acknowledgement.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException

KINDS = frozenset({"NOTE", "TASK", "QUESTION", "ANSWER", "PROGRESS", "RESULT", "FAILED", "DECLINED", "CANCELLED"})
NEEDS_PARENT = frozenset({"ANSWER", "PROGRESS", "RESULT", "FAILED", "DECLINED", "CANCELLED"})

# A task closes when its recipient reports RESULT, FAILED or DECLINED in the thread, or its
# sender cancels. A question closes when its recipient answers or declines it, or when the
# task it belongs to closes. Everything else is open until acknowledged.
OPEN_SQL = """CASE COALESCE(c.kind, 'NOTE')
  WHEN 'TASK' THEN NOT EXISTS (
    SELECT 1 FROM work.inbox_message_context rc JOIN work.inbox_deliveries r ON r.delivery_id = rc.delivery_id
     WHERE rc.thread_id = d.delivery_id AND r.initiative_id = d.initiative_id
       AND ((r.sender_id = d.recipient_id AND r.recipient_id = d.sender_id AND rc.kind IN ('RESULT','FAILED','DECLINED'))
         OR (r.sender_id = d.sender_id AND r.recipient_id = d.recipient_id AND rc.kind = 'CANCELLED')))
  WHEN 'QUESTION' THEN NOT EXISTS (
    SELECT 1 FROM work.inbox_message_context rc JOIN work.inbox_deliveries r ON r.delivery_id = rc.delivery_id
     WHERE rc.in_reply_to = d.delivery_id AND r.sender_id = d.recipient_id AND rc.kind IN ('ANSWER','DECLINED'))
    AND NOT EXISTS (
    SELECT 1 FROM work.inbox_message_context rc
      JOIN work.inbox_deliveries r ON r.delivery_id = rc.delivery_id
      JOIN work.inbox_deliveries task ON task.delivery_id = rc.thread_id
      JOIN work.inbox_message_context tc ON tc.delivery_id = task.delivery_id AND tc.kind = 'TASK'
     WHERE rc.thread_id = c.thread_id
       AND ((r.sender_id = task.recipient_id AND rc.kind IN ('RESULT','FAILED','DECLINED'))
         OR (r.sender_id = task.sender_id AND rc.kind = 'CANCELLED')))
  ELSE d.acknowledged_at IS NULL END"""


def parse(payload: dict[str, Any]) -> tuple[str, str | None] | None:
    """Validated (kind, in_reply_to), or None for a contract 1 message."""
    kind, parent = payload.get("kind"), payload.get("in_reply_to")
    if kind is None and parent is None:
        return None
    kind = "NOTE" if kind is None else kind
    if not isinstance(kind, str) or kind not in KINDS:
        raise HTTPException(status_code=422, detail={"code": "INBOX_KIND_INVALID"})
    if parent is None:
        if kind in NEEDS_PARENT:
            raise HTTPException(status_code=422, detail={"code": "INBOX_REPLY_TARGET_REQUIRED"})
        return kind, None
    try:
        if str(UUID(parent)) != parent:
            raise ValueError("not canonical")
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail={"code": "INBOX_REPLY_TARGET_INVALID"}) from None
    return kind, parent


def thread_for(cur, *, initiative: str, sender: str, recipient: str, kind: str, parent: str | None, delivery: str) -> str:
    """The thread a new delivery joins. Threads have exactly two parties."""
    if parent is None:
        return delivery
    cur.execute("""SELECT d.sender_id::text, d.recipient_id::text, c.thread_id::text,
          task.sender_id::text, task.recipient_id::text, tc.kind
        FROM work.inbox_deliveries d
        LEFT JOIN work.inbox_message_context c ON c.delivery_id = d.delivery_id
        LEFT JOIN work.inbox_deliveries task ON task.delivery_id = COALESCE(c.thread_id, d.delivery_id)
        LEFT JOIN work.inbox_message_context tc ON tc.delivery_id = task.delivery_id
        WHERE d.delivery_id = %s AND d.initiative_id = %s""", (parent, initiative))
    row = cur.fetchone()
    # One code for absent and for someone else's delivery: do not reveal which.
    if row is None or sorted((row[0], row[1])) != sorted((sender, recipient)):
        raise HTTPException(status_code=422, detail={"code": "INBOX_REPLY_TARGET_INVALID"})
    root_sender, root_recipient, root_kind = row[3], row[4], row[5]
    if kind == "CANCELLED" and (root_kind != "TASK" or sender != root_sender):
        raise HTTPException(status_code=422, detail={"code": "INBOX_KIND_NOT_YOURS"})
    if kind in ("RESULT", "FAILED") and root_kind == "TASK" and sender != root_recipient:
        raise HTTPException(status_code=422, detail={"code": "INBOX_KIND_NOT_YOURS"})
    # A task always starts its own thread, even when it was prompted by another message.
    return delivery if kind == "TASK" else (row[2] or parent)


def sign(envelope: dict[str, Any], context: dict[str, Any], secret: bytes) -> str:
    bound = {key: envelope[key] for key in ("delivery_id", "initiative_id", "sender_id", "recipient_id", "body_sha256")}
    content = json.dumps({**bound, **context}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hmac.new(secret, b"docplane-inbox-context-v1\0" + content, hashlib.sha256).hexdigest()


def verified(envelope: Any, row: tuple, keys: dict[str, bytes]) -> tuple[dict[str, Any], bool]:
    """(public context, intact) for a (kind, in_reply_to, thread_id, kid, signature) row."""
    kind, parent, thread, kid, signature = row
    try:
        if kind is None:
            return {"kind": "NOTE", "in_reply_to": None, "thread_id": envelope["delivery_id"]}, True
        context = {"kind": kind, "in_reply_to": parent, "thread_id": thread}
        key = keys.get(kid)
        return context, key is not None and hmac.compare_digest(sign(envelope, context, key), signature)
    except (KeyError, TypeError, ValueError):
        return {}, False
