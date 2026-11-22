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

NO_CONTEXT = (None, None, None, None, None)


class Threads:
    """One participant's intact messages on one initiative, and what they close.

    Closure is computed here rather than in SQL so only rows whose signature verifies can
    close work or appear as a reply. An altered row therefore leaves work open.
    """

    def __init__(self, cur, initiative: str, participant: str, keys: dict[str, bytes]):
        cur.execute("""SELECT d.delivery_id::text, d.sender_id::text, d.recipient_id::text, d.position,
              d.acknowledged_at, d.created_at, d.envelope->>'body_sha256',
              c.kind, c.in_reply_to::text, c.thread_id::text, c.kid, c.signature
            FROM work.inbox_deliveries d
            LEFT JOIN work.inbox_message_context c ON c.delivery_id = d.delivery_id
            WHERE d.initiative_id = %s AND (d.sender_id = %s OR d.recipient_id = %s)
            ORDER BY d.position""", (initiative, participant, participant))
        self.participant, self.rows, self.replies = participant, {}, {}
        for delivery, sender, recipient, position, ack, created, digest, *stored in cur.fetchall():
            # Bind to the row's own scope, not to what its stored envelope claims.
            bound = {"delivery_id": delivery, "initiative_id": initiative, "sender_id": sender,
                     "recipient_id": recipient, "body_sha256": digest}
            context, intact = verified(bound, tuple(stored), keys)
            row = {"id": delivery, "sender": sender, "recipient": recipient, "position": position,
                   "acknowledged": ack is not None, "created_at": created, "intact": intact, **context}
            self.rows[delivery] = row
            if intact and context["in_reply_to"]:
                for anchor in {context["in_reply_to"], context["thread_id"]} - {delivery}:
                    self.replies.setdefault(anchor, []).append(row)

    def task_closed(self, task: dict[str, Any]) -> bool:
        for reply in self.replies.get(task["id"], ()):
            if reply["thread_id"] != task["id"]:
                continue
            if reply["sender"] == task["sender"] and reply["kind"] == "CANCELLED":
                return True
            if reply["sender"] == task["recipient"]:
                if reply["kind"] in ("RESULT", "FAILED"):
                    return True
                # A decline closes what it answers, so declining a question keeps its task open.
                if reply["kind"] == "DECLINED" and reply["in_reply_to"] == task["id"]:
                    return True
        return False

    def is_open(self, row: dict[str, Any]) -> bool:
        if not row["intact"]:
            return True  # surfaces as an integrity failure instead of disappearing
        if row["kind"] == "TASK":
            return not self.task_closed(row)
        if row["kind"] == "QUESTION":
            for reply in self.replies.get(row["id"], ()):
                if (reply["in_reply_to"] == row["id"] and reply["sender"] == row["recipient"]
                        and reply["kind"] in ("ANSWER", "DECLINED")):
                    return False
            root = self.rows.get(row["thread_id"])
            return not (root and root["intact"] and root["kind"] == "TASK" and self.task_closed(root))
        return not row["acknowledged"]

    def open_after(self, position: int) -> list[str]:
        return [row["id"] for row in self.rows.values()
                if row["recipient"] == self.participant and row["position"] > position and self.is_open(row)]

    def latest_reply(self, delivery: str) -> dict[str, Any] | None:
        sent = self.rows.get(delivery)
        if sent is None or not sent["intact"]:
            return None
        found = [r for r in self.replies.get(delivery, ())
                 if r["sender"] == sent["recipient"] and r["recipient"] == sent["sender"]
                 and (r["in_reply_to"] == delivery or (sent["kind"] == "TASK" and r["thread_id"] == delivery))]
        if not found:
            return None
        last = max(found, key=lambda r: r["position"])
        return {"delivery_id": last["id"], "kind": last["kind"], "created_at": last["created_at"]}


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


def thread_for(cur, *, initiative: str, sender: str, recipient: str, kind: str, parent: str | None,
               delivery: str, keys: dict[str, bytes]) -> str:
    """The thread a new delivery joins. Threads have exactly two parties."""
    if parent is None:
        return delivery

    def load(ident: str) -> dict[str, Any] | None:
        cur.execute("""SELECT d.sender_id::text, d.recipient_id::text, d.envelope->>'body_sha256',
              c.kind, c.in_reply_to::text, c.thread_id::text, c.kid, c.signature
            FROM work.inbox_deliveries d
            LEFT JOIN work.inbox_message_context c ON c.delivery_id = d.delivery_id
            WHERE d.delivery_id = %s AND d.initiative_id = %s""", (ident, initiative))
        row = cur.fetchone()
        if row is None or sorted((row[0], row[1])) != sorted((sender, recipient)):
            return None
        context, intact = verified({"delivery_id": ident, "initiative_id": initiative, "sender_id": row[0],
                                    "recipient_id": row[1], "body_sha256": row[2]}, tuple(row[3:]), keys)
        return {"sender": row[0], "recipient": row[1], **context} if intact else None

    found = load(parent)
    root = None
    if found is not None:
        root = found if found["thread_id"] == parent else load(found["thread_id"])
    # One code for absent, damaged and someone else's delivery: do not reveal which.
    if found is None or root is None:
        raise HTTPException(status_code=422, detail={"code": "INBOX_REPLY_TARGET_INVALID"})
    if kind == "CANCELLED" and (root["kind"] != "TASK" or sender != root["sender"]):
        raise HTTPException(status_code=422, detail={"code": "INBOX_KIND_NOT_YOURS"})
    if kind in ("RESULT", "FAILED") and root["kind"] == "TASK" and sender != root["recipient"]:
        raise HTTPException(status_code=422, detail={"code": "INBOX_KIND_NOT_YOURS"})
    # A task always starts its own thread, even when it was prompted by another message.
    return delivery if kind == "TASK" else found["thread_id"]


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
