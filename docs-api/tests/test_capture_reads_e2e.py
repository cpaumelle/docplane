"""Real PostgreSQL proof of capture keyset SQL; runs in fresh-instance CI."""
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

if not os.environ.get('DB_HOST'):
    pytest.skip('requires migrated PostgreSQL (DB_HOST etc.)', allow_module_level=True)

from app.db import get_conn
from app.work_api import get_capture, list_captures


def test_real_keyset_ties_new_arrival_and_triage():
    actor = None
    ids = sorted([str(uuid4()) for _ in range(7)], reverse=True)
    stamp = datetime(2199, 1, 1, tzinfo=timezone.utc)
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO docplane.principals (display_name, principal_kind) VALUES (%s, 'AGENT') RETURNING principal_id::text", ('capture-read-e2e-' + uuid4().hex,))
            actor = cur.fetchone()[0]
            for capture_id in ids[:6]:
                cur.execute("INSERT INTO work.captures (capture_id, body, kind, author_principal_id, idempotency_key, created_at) VALUES (%s, %s, 'FINDING', %s, %s, %s)",
                            (capture_id, 'synthetic capture evidence', actor, str(uuid4()), stamp))
            conn.commit()
        first = list_captures(status='INBOX', limit=2, view='metadata', cursor=None, principal=None)
        assert [v['capture_id'] for v in first['captures']] == ids[:2]
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE work.captures SET status='DISCARDED', triaged_by_principal_id=%s, triaged_at=now() WHERE capture_id=%s", (actor, ids[0]))
            cur.execute("INSERT INTO work.captures (capture_id, body, author_principal_id, idempotency_key, created_at) VALUES (%s, %s, %s, %s, %s)",
                        (ids[6], 'later arrival', actor, str(uuid4()), stamp + timedelta(days=1)))
            conn.commit()
        second = list_captures(status='INBOX', limit=2, view='metadata', cursor=first['next_cursor'], principal=None)
        third = list_captures(status='INBOX', limit=2, view='metadata', cursor=second['next_cursor'], principal=None)
        assert [v['capture_id'] for v in second['captures'] + third['captures']] == ids[2:6]
        assert get_capture(UUID(ids[0]), principal=None)['status'] == 'DISCARDED'
    finally:
        if actor:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute('DELETE FROM work.captures WHERE author_principal_id=%s', (actor,))
                cur.execute('DELETE FROM docplane.principals WHERE principal_id=%s', (actor,))
                conn.commit()
