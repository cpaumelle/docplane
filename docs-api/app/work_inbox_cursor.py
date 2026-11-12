"""Authenticated, scope-bound cursors for the addressed WORK inbox.

A cursor is a position, not a grant. Every use must additionally recheck the
current recipient and initiative grants in the same request. Publication must
serialize allocation through commit; signing a pre-commit sequence cannot make
an out-of-order sequence safe.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from uuid import UUID

from fastapi import HTTPException
from app.inbox_secrets import InboxSecretError, read_secret


def _secret() -> bytes:
    try:
        value = read_secret("DOCPLANE_WORK_INBOX_CURSOR_SECRET")
    except InboxSecretError as exc:
        raise HTTPException(status_code=503, detail={"code": "INBOX_CURSOR_SECRET_UNAVAILABLE"}) from exc
    if len(value.encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail={"code": "INBOX_CURSOR_SECRET_UNAVAILABLE"})
    return value.encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(data: str) -> bytes:
    return base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)


def encode_cursor(sequence: int, *, principal_id: str, initiative_id: str) -> str:
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise ValueError("invalid sequence")
    payload = json.dumps({"v": 1, "seq": sequence, "principal": str(UUID(principal_id)),
                          "initiative": str(UUID(initiative_id))},
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(_secret(), b"docplane-work-inbox-cursor-v1\0" + payload, hashlib.sha256).digest()
    return _b64(payload) + "." + _b64(signature)


def decode_cursor(cursor: str | None, *, principal_id: str, initiative_id: str) -> int:
    if cursor is None:
        return 0
    secret = _secret()  # Missing configuration is a service error, not a bad cursor.
    try:
        if not isinstance(cursor, str) or len(cursor) > 1024:
            raise ValueError("size")
        encoded, encoded_signature = cursor.split(".")
        payload, signature = _decode(encoded), _decode(encoded_signature)
        if _b64(payload) != encoded or _b64(signature) != encoded_signature:
            raise ValueError("non-canonical")
        expected = hmac.new(secret, b"docplane-work-inbox-cursor-v1\0" + payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        value = json.loads(payload)
        if not isinstance(value, dict) or set(value) != {"v", "seq", "principal", "initiative"}:
            raise ValueError("shape")
        if (value["v"] != 1 or isinstance(value["v"], bool)
            or value["principal"] != str(UUID(principal_id))
            or value["initiative"] != str(UUID(initiative_id))
            or isinstance(value["seq"], bool) or not isinstance(value["seq"], int)
            or value["seq"] < 0):
            raise ValueError("scope")
        return value["seq"]
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail={"code": "INBOX_CURSOR_INVALID"}) from exc
