from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.work_inbox_cursor import decode_cursor, encode_cursor


def test_cursor_is_bound_to_principal_and_initiative(monkeypatch):
    monkeypatch.setenv("DOCPLANE_WORK_INBOX_CURSOR_SECRET", "s" * 32)
    principal, initiative = str(uuid4()), str(uuid4())
    cursor = encode_cursor(37, principal_id=principal, initiative_id=initiative)
    assert decode_cursor(cursor, principal_id=principal, initiative_id=initiative) == 37
    for other_principal, other_initiative in ((str(uuid4()), initiative), (principal, str(uuid4()))):
        with pytest.raises(HTTPException) as exc:
            decode_cursor(cursor, principal_id=other_principal, initiative_id=other_initiative)
        assert exc.value.status_code == 400
    changed = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    with pytest.raises(HTTPException):
        decode_cursor(changed, principal_id=principal, initiative_id=initiative)


def test_missing_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("DOCPLANE_WORK_INBOX_CURSOR_SECRET", raising=False)
    with pytest.raises(HTTPException) as exc:
        encode_cursor(0, principal_id=str(uuid4()), initiative_id=str(uuid4()))
    assert exc.value.status_code == 503
