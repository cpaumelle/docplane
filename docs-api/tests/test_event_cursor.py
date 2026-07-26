import os

import pytest
from fastapi import HTTPException

from app import event_cursor


def test_cursor_is_signed_scoped_and_round_trips(monkeypatch):
    monkeypatch.setenv("DOCPLANE_EVENT_CURSOR_SECRET", "test-only-event-cursor-secret")
    cursor = event_cursor.encode_cursor(42, principal_id="principal-a")
    assert event_cursor.decode_cursor(cursor, principal_id="principal-a") == 42
    with pytest.raises(HTTPException) as wrong_principal:
        event_cursor.decode_cursor(cursor, principal_id="principal-b")
    assert wrong_principal.value.status_code == 400


def test_cursor_tampering_and_missing_secret_fail_closed(monkeypatch):
    monkeypatch.setenv("DOCPLANE_EVENT_CURSOR_SECRET", "test-only-event-cursor-secret")
    cursor = event_cursor.encode_cursor(7, principal_id="principal-a")
    payload, signature = cursor.split(".", 1)
    tampered = payload + "." + ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(HTTPException) as invalid:
        event_cursor.decode_cursor(tampered, principal_id="principal-a")
    assert invalid.value.status_code == 400

    monkeypatch.delenv("DOCPLANE_EVENT_CURSOR_SECRET", raising=False)
    with pytest.raises(HTTPException) as unavailable:
        event_cursor.encode_cursor(1, principal_id="principal-a")
    assert unavailable.value.status_code == 503
