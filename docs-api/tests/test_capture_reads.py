"""Redacted capture reads and stable keyset traversal, with no database needed."""
import base64
import json
from datetime import datetime, timezone
from uuid import UUID

import pytest
from fastapi import HTTPException
from app import work_api as api
from migration.redaction import DocumentRefusedError

STAMP = datetime(2026, 10, 20, tzinfo=timezone.utc)
ACTOR = '10000000-0000-0000-0000-000000000001'


def record(n, status='INBOX'):
    return (str(UUID(int=n)), 'ordinary evidence', 'FINDING', {'source': 'test'}, status,
            ACTOR, None, None, None, None, None, STAMP)


class Store:
    def __init__(self, rows):
        self.rows = rows
        self.query = ''
        self.params = []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cursor(self): return self
    def execute(self, query, params=()):
        self.query, self.params = query, params
    def selected(self):
        rows = sorted(self.rows, key=lambda r: (r[-1], r[0]), reverse=True)
        params = list(self.params)
        if 'status = %s' in self.query:
            status = params.pop(0)
            rows = [r for r in rows if r[4] == status]
        if '(created_at, capture_id) <=' in self.query:
            boundary = tuple(params[:2]); params = params[2:]
            rows = [r for r in rows if (r[-1], r[0]) <= boundary]
        if '(created_at, capture_id) < (' in self.query:
            before = tuple(params[:2]); params = params[2:]
            rows = [r for r in rows if (r[-1], r[0]) < before]
        return rows
    def fetchone(self):
        if self.query.startswith('SELECT created_at'):
            rows = self.selected()
            return (rows[0][-1], rows[0][0]) if rows else None
        if 'count(*)' in self.query: return (len(self.selected()),)
        return next((r for r in self.rows if r[0] == self.params[0]), None)
    def fetchall(self):
        assert 'ORDER BY created_at DESC, capture_id DESC' in self.query
        return self.selected()[:self.params[-1]]


def page(store, monkeypatch, **kwargs):
    monkeypatch.setattr(api, 'get_conn', lambda: store)
    return api.list_captures(status=kwargs.get('status', 'all'), limit=2,
                             view='metadata', cursor=kwargs.get('cursor'), principal=None)


def test_pages_tied_timestamps_and_new_arrivals(monkeypatch):
    store = Store([record(n) for n in range(1, 7)])
    first = page(store, monkeypatch)
    assert first['total'] == 6 and first['truncated'] is True
    store.rows.append(record(7))  # above initial upper boundary; excluded
    ids = [v['capture_id'] for v in first['captures']]
    cursor = first['next_cursor']
    while cursor:
        result = page(store, monkeypatch, cursor=cursor)
        ids.extend(v['capture_id'] for v in result['captures'])
        cursor = result['next_cursor']
    assert ids == [str(UUID(int=n)) for n in range(6, 0, -1)]
    assert 'body' not in first['captures'][0] and 'origin' not in first['captures'][0]


def test_status_changes_do_not_shift_next_page(monkeypatch):
    store = Store([record(n) for n in range(1, 7)])
    first = page(store, monkeypatch, status='INBOX')
    store.rows[-1] = record(6, 'DISCARDED')
    second = page(store, monkeypatch, status='INBOX', cursor=first['next_cursor'])
    assert [v['capture_id'] for v in second['captures']] == [str(UUID(int=4)), str(UUID(int=3))]
    with pytest.raises(HTTPException) as err:
        page(store, monkeypatch, status='all', cursor=first['next_cursor'])
    assert err.value.status_code == 422


@pytest.mark.parametrize('cursor', ['bad', '!!!!', base64.urlsafe_b64encode(b'[]').decode(),
    base64.urlsafe_b64encode(json.dumps({'v':1,'status':'all','boundary':[None,None],'before':[]}).encode()).decode()])
def test_bad_cursor_refused(monkeypatch, cursor):
    with pytest.raises(HTTPException) as err:
        page(Store([]), monkeypatch, cursor=cursor)
    assert err.value.detail['code'] == 'CAPTURE_CURSOR_INVALID'


def test_empty_list(monkeypatch):
    result = page(Store([]), monkeypatch)
    assert result['captures'] == [] and result['next_cursor'] is None


def test_capture_identity_and_canonical_redaction(monkeypatch):
    row = list(record(1))
    row[1] = 'ghp_' + 'A' * 36  # synthetic fixture only
    row[3] = {'source': 'ghp_' + 'B' * 36}
    row[10] = 'ghp_' + 'C' * 36
    store = Store([tuple(row)])
    monkeypatch.setattr(api, 'get_conn', lambda: store)
    result = api.get_capture(UUID(int=1), principal=None)
    encoded = json.dumps(result, default=str)
    assert all('ghp_' + c * 36 not in encoded for c in 'ABC')
    assert result['capture_bodies'] == 'redacted'
    assert 'origin' not in result
    assert '<REDACTED:' in encoded


def test_refused_body_withheld_and_missing_capture(monkeypatch):
    monkeypatch.setattr(api, 'get_conn', lambda: Store([record(1)]))
    def refuse(*args, **kwargs): raise DocumentRefusedError(())
    monkeypatch.setattr(api, 'redact', refuse)
    result = api.get_capture(UUID(int=1), principal=None)
    assert result['body'] is None and result['body_withheld'] is True
    assert result['origin_text'] is None and result['origin_text_withheld'] is True
    with pytest.raises(HTTPException) as err:
        api.get_capture(UUID(int=2), principal=None)
    assert err.value.status_code == 404


def test_capture_read_requires_contributor_and_valid_query():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    assert client.get('/api/v1/work/captures/' + str(UUID(int=1))).status_code == 401
    app.dependency_overrides[api.require_contributor] = lambda: None
    assert client.get('/api/v1/work/captures?view=unexpected').status_code == 422
    assert client.get('/api/v1/work/captures?limit=0').status_code == 422
    assert client.get('/api/v1/work/captures?cursor=' + 'x'*1025).status_code == 422
