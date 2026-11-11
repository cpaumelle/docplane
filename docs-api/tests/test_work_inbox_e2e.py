"""Inbox acceptance against a migrated disposable synthetic database."""
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
if not os.environ.get("DB_HOST"):
    pytest.skip("requires disposable PostgreSQL", allow_module_level=True)

from fastapi.testclient import TestClient
from app.application import app
from app.db import get_conn

client = TestClient(app)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("DOCPLANE_WORK_INBOX_CURSOR_SECRET", "cursor-secret-for-synthetic-tests-12345")
    monkeypatch.setenv("DOCPLANE_WORK_INBOX_SIGNING_SECRET", "signing-secret-for-synthetic-tests-1234")
    tokens = ["synthetic-" + uuid4().hex for _ in range(3)]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT workspace_id FROM docplane.workspaces WHERE workspace_key='work'")
        workspace = cur.fetchone()[0]
        ids = []
        for token in tokens:
            cur.execute("INSERT INTO docplane.principals(display_name,principal_kind,authorization_mode) VALUES ('fixture','AGENT','WORK_COLLABORATOR') RETURNING principal_id::text")
            pid = cur.fetchone()[0]
            ids.append(pid)
            cur.execute("INSERT INTO docplane.api_tokens(principal_id,token_hash,token_prefix) VALUES (%s,%s,%s)", (pid, hashlib.sha256(token.encode()).hexdigest(), "fixture"))
        cur.execute("""INSERT INTO work.initiatives (initiative_key,workspace_id,title,objective,owner_principal_id,idempotency_key)
          VALUES (%s,%s,'synthetic','synthetic',%s,%s) RETURNING initiative_id::text""", ("inbox-"+uuid4().hex, workspace, ids[0], str(uuid4())))
        initiative = cur.fetchone()[0]
        for pid in ids[:2]:
            cur.execute("INSERT INTO work.collaboration_grants(initiative_id,principal_id,participant_role,can_contribute) VALUES (%s,%s,'AGENT',true)", (initiative,pid))
        conn.commit()
    return initiative, ids, [{"Authorization": "Bearer " + t} for t in tokens]


def test_delivery_replay_ack_isolation_and_integrity(setup):
    initiative, ids, headers = setup
    root = f"/api/v1/work-inbox/{initiative}/messages"
    key = str(uuid4())
    payload = {"recipient_id": ids[1], "body": "Synthetic request", "reported_producer": "test-agent"}
    first = client.post(root, headers={**headers[0], "Idempotency-Key": key}, json=payload)
    assert first.status_code == 201, first.text
    assert client.post(root, headers={**headers[0], "Idempotency-Key": key}, json=payload).json() == first.json()
    assert client.post(root, headers={**headers[0], "Idempotency-Key": key}, json={**payload, "body":"changed"}).status_code == 409
    assert client.post(root, headers={**headers[0], "Idempotency-Key": str(uuid4())}, json={**payload,"sender_role":"HUMAN"}).status_code == 422
    assert client.get(root, headers=headers[2]).status_code == 403
    assert client.get("/api/v1/workspaces", headers=headers[0]).status_code == 403
    read = client.get(root, headers=headers[1])
    assert read.status_code == 200, read.text
    item = read.json()["messages"][0]
    assert item["envelope"]["sender_id"] == ids[0]
    assert item["body"] == "Synthetic request"
    assert client.get(root, headers=headers[0], params={"cursor": read.json()["next_cursor"]}).status_code == 400
    ack_url = root + "/" + first.json()["delivery_id"] + "/ack"
    assert client.post(ack_url, headers=headers[0]).status_code == 404
    ack = client.post(ack_url, headers=headers[1])
    assert ack.status_code == 200 and ack.json()["meaning"] == "RECEIVED"
    assert client.post(ack_url, headers=headers[1]).json() == ack.json()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE work.initiative_activities SET body='tampered' WHERE activity_id=%s", (first.json()["activity_id"],))
        conn.commit()
    assert client.get(root, headers=headers[1]).status_code == 503


def test_concurrent_pagination_and_revocation(setup):
    initiative, ids, headers = setup
    root = f"/api/v1/work-inbox/{initiative}/messages"
    def send(n):
        return client.post(root, headers={**headers[0],"Idempotency-Key":str(uuid4())}, json={"recipient_id":ids[1],"body":f"synthetic {n}"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(send, range(8)))
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    cursor = None
    positions = []
    for _ in range(4):
        params = {"limit": 2}
        if cursor: params["cursor"] = cursor
        response = client.get(root, headers=headers[1], params=params)
        assert response.status_code == 200, response.text
        positions.extend(x["envelope"]["position"] for x in response.json()["messages"])
        cursor = response.json()["next_cursor"]
    assert positions == list(range(1, 9))
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE work.collaboration_grants SET revoked_at=now() WHERE initiative_id=%s AND principal_id=%s", (initiative, ids[1]))
        conn.commit()
    assert client.get(root, headers=headers[1], params={"cursor": cursor}).status_code == 403


def test_refusal_expiry_and_rollback(setup, monkeypatch):
    initiative, ids, headers = setup
    root = f"/api/v1/work-inbox/{initiative}/messages"
    key = str(uuid4())
    payload = {"recipient_id": ids[1], "body": "synthetic rollback"}
    monkeypatch.delenv("DOCPLANE_WORK_INBOX_SIGNING_SECRET")
    failed = client.post(root, headers={**headers[0], "Idempotency-Key": key}, json=payload)
    assert failed.status_code == 503
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM work.initiative_activities WHERE initiative_id=%s", (initiative,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM work.inbox_deliveries WHERE initiative_id=%s", (initiative,))
        assert cur.fetchone()[0] == 0
    monkeypatch.setenv("DOCPLANE_WORK_INBOX_SIGNING_SECRET", "signing-secret-for-synthetic-tests-1234")
    assert client.post(root, headers={**headers[0], "Idempotency-Key": key}, json=payload).status_code == 201
    unsafe = client.post(root, headers={**headers[0], "Idempotency-Key": str(uuid4())}, json={**payload, "body": "ghp_" + "a" * 36})
    assert unsafe.status_code == 422 and "ghp_" not in unsafe.text
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE work.collaboration_grants SET expires_at=now()-interval '1 minute' WHERE initiative_id=%s AND principal_id=%s", (initiative, ids[1]))
        conn.commit()
    assert client.get(root, headers=headers[1]).status_code == 403
    assert client.post(root, headers={**headers[0], "Idempotency-Key": str(uuid4())}, json=payload).status_code == 403


def test_bootstrap_participant_and_grant_administration(setup, monkeypatch):
    initiative, ids, headers = setup
    monkeypatch.setenv("DOCPLANE_BOOTSTRAP_TOKEN", "synthetic-bootstrap")
    endpoint = "/api/v1/bootstrap/work-participants"
    assert client.post(endpoint, json={"display_name": "architect"}).status_code == 403
    bootstrap = {"X-DocPlane-Bootstrap-Token": "synthetic-bootstrap"}
    created = client.post(endpoint, headers=bootstrap, json={"display_name": "architect"})
    assert created.status_code == 201
    participant = created.json()
    participant_headers = {"Authorization": "Bearer " + participant["token"]}
    root = f"/api/v1/work-inbox/{initiative}/messages"
    assert client.get(root, headers=participant_headers).status_code == 403
    grant = f"/api/v1/bootstrap/work-grants/{initiative}/{participant['principal_id']}"
    assert client.put(grant, headers=bootstrap, json={"role":"ARCHITECT", "can_contribute":False}).status_code == 200
    assert client.get(root, headers=participant_headers).status_code == 200
    assert client.post(root, headers={**participant_headers, "Idempotency-Key":str(uuid4())}, json={"recipient_id":ids[0],"body":"hello"}).status_code == 403
    assert client.delete(grant, headers=bootstrap).status_code == 200
    assert client.get(root, headers=participant_headers).status_code == 403


def test_late_commit_cannot_be_skipped(setup, monkeypatch):
    from threading import Event
    from app import work_inbox_api
    initiative, ids, headers = setup
    root = f"/api/v1/work-inbox/{initiative}/messages"
    entered, release = Event(), Event()
    original = work_inbox_api.append_event
    def delayed(*args, **kwargs):
        if kwargs.get("event_type") == "WORK_INBOX_MESSAGE" and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(work_inbox_api, "append_event", delayed)
    def send(body):
        return client.post(root, headers={**headers[0], "Idempotency-Key":str(uuid4())}, json={"recipient_id":ids[1], "body":body})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(send, "first")
        assert entered.wait(10)
        initial = client.get(root, headers=headers[1]).json()
        assert initial["messages"] == []
        second = pool.submit(send, "second")
        assert not second.done()
        release.set()
        assert first.result(timeout=10).status_code == 201
        assert second.result(timeout=10).status_code == 201
    replay = client.get(root, headers=headers[1], params={"cursor": initial["next_cursor"]})
    assert [m["body"] for m in replay.json()["messages"]] == ["first", "second"]


def test_signed_envelope_cannot_be_replayed_into_another_delivery(setup):
    initiative, ids, headers = setup
    root = f"/api/v1/work-inbox/{initiative}/messages"
    def send(body):
        return client.post(root, headers={**headers[0], "Idempotency-Key":str(uuid4())}, json={"recipient_id":ids[1], "body":body}).json()
    first, second = send("first"), send("second")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""UPDATE work.inbox_deliveries SET envelope=source.envelope, signature=source.signature
          FROM work.inbox_deliveries source WHERE work.inbox_deliveries.delivery_id=%s AND source.delivery_id=%s""", (second["delivery_id"], first["delivery_id"]))
        conn.commit()
    response = client.get(root, headers=headers[1])
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "INBOX_INTEGRITY_FAILURE"
