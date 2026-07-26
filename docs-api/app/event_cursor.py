"""Opaque signed cursors for permission-filtered event streams."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os

from fastapi import HTTPException


def _secret() -> bytes:
    value = os.environ.get("DOCPLANE_EVENT_CURSOR_SECRET", "")
    if len(value) < 24:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "EVENT_CURSOR_SECRET_UNAVAILABLE",
                "message": "DOCPLANE_EVENT_CURSOR_SECRET must contain at least 24 characters",
            },
        )
    return value.encode("utf-8")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def encode_cursor(event_seq: int, *, principal_id: str) -> str:
    payload = json.dumps(
        {"v": 1, "seq": int(event_seq), "principal": principal_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return _b64encode(payload) + "." + _b64encode(signature)


def decode_cursor(cursor: str | None, *, principal_id: str) -> int:
    if not cursor:
        return 0
    try:
        encoded_payload, encoded_signature = cursor.split(".", 1)
        payload = _b64decode(encoded_payload)
        signature = _b64decode(encoded_signature)
        expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        value = json.loads(payload)
        if value.get("v") != 1 or value.get("principal") != principal_id:
            raise ValueError("scope")
        sequence = int(value["seq"])
        if sequence < 0:
            raise ValueError("sequence")
        return sequence
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "EVENT_CURSOR_INVALID", "message": "event cursor is invalid or belongs to another principal"},
        ) from exc
