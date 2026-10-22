"""Real PostgreSQL transaction, replay and concurrency acceptance for WORK briefs."""
from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

if not os.environ.get("DB_HOST"):
    pytest.skip("requires the migrated disposable PostgreSQL", allow_module_level=True)

from fastapi.testclient import TestClient
from app.application import app
from app.db import get_conn

client = TestClient(app)


@pytest.fixture
def work():
    token = "dp_work_brief_test_" + uuid4().hex
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO docplane.principals (display_name, principal_kind) VALUES ('work-brief-fixture', 'AGENT') RETURNING principal_id::text")
        principal = cur.fetchone()[0]
        cur.execute("INSERT INTO docplane.api_tokens (principal_id, token_hash, token_prefix) VALUES (%s, %s, %s)",
                    (principal, hashlib.sha256(token.encode()).hexdigest(), token[:12]))
        cur.execute("SELECT workspace_id::text FROM docplane.workspaces WHERE workspace_key = 'work'")
        workspace = cur.fetchone()[0]
        conn.commit()
    headers = {"Authorization": "Bearer " + token}
    created = client.post("/api/v1/initiatives", headers={**headers, "Idempotency-Key": str(uuid4())}, json={
        "initiative_key": "work-brief-" + uuid4().hex, "workspace_id": workspace,
        "title": "Synthetic acceptance initiative", "objective": "Preserve explicit holds and evidence", "scope": "Synthetic records only"})
    assert created.status_code == 201, created.text
    root = "/api/v1/initiatives/" + created.json()["initiative_id"]
    return root, headers, principal


def read(work):
    root, headers, _ = work
    response = client.get(root + "/brief", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def item(key="review", **values):
    return {"item_id": key, "title": "Review source and approval evidence", "state": "OPEN", "stance": "ACTIVE",
            "next_action": "Review the packet", "reason": "Review remains outstanding",
            "references": [{"reference_type": "PR", "reference_id": "example/project#1"}], **values}


def body(work, *items):
    return {"expected_context": read(work)["context_token"], "summary": "Synthetic recorded update",
            "reported_producer": "work-brief-fixture", "changes": [{"item": value} for value in items]}


def post(work, payload, key=None):
    root, headers, _ = work
    return client.post(root + "/updates", headers={**headers, "Idempotency-Key": key or str(uuid4())}, json=payload)


def test_brief_update_replay_and_existing_history(work):
    before = read(work)
    assert before["checkpoint_status"] == "NOT_SET"
    payload = body(work, item(), item("approval", stance="HELD", reason="Operator approval required"))
    key = str(uuid4())
    first = post(work, payload, key)
    assert first.status_code == 201, first.text
    after = read(work)
    assert after["context_token"] != before["context_token"]
    assert after["initiative"]["version"] == before["initiative"]["version"] + 1
    assert after["next_actions"] == ["review"]
    assert after["held"] == ["approval"]
    assert after["checkpoint"]["author_principal_id"] == work[2]
    assert after["checkpoint"]["reported_producer"] == "work-brief-fixture"
    assert after["activities_since_checkpoint"] == 0
    assert post(work, payload, key).json() == first.json()
    assert read(work)["counts"]["activities"] == 1
    changed = {**payload, "summary": "Changed intent"}
    refused = post(work, changed, key)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    history = client.get(work[0], headers=work[1], params={"activity_bodies": "redacted"}).json()
    assert "[OPEN; HELD]" in history["activities"][0]["body"]


def test_old_context_and_new_notes_refuse_stale_update(work):
    payload = body(work, item())
    response = client.post(work[0] + "/activities", headers={**work[1], "Idempotency-Key": str(uuid4())},
                           json={"activity_type": "NOTE", "body": "A new fact requires review"})
    assert response.status_code == 201
    result = post(work, payload)
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "WORK_CONTEXT_STALE"
    assert read(work)["checkpoint_status"] == "NOT_SET"


def test_updates_preserve_holds_and_need_local_decision_evidence(work):
    assert post(work, body(work, item(), item("approval", stance="HELD"))).status_code == 201
    assert post(work, body(work, item(state="DONE_IN_RECORD"))).status_code == 201
    assert read(work)["held"] == ["approval"]
    attempt = body(work, item("approval"))
    assert post(work, attempt).json()["detail"]["code"] == "WORK_DECISION_REQUIRED"
    note = client.post(work[0] + "/activities", headers={**work[1], "Idempotency-Key": str(uuid4())},
                       json={"activity_type": "NOTE", "body": "Fixture owner explicitly released the approval hold"})
    assert note.status_code == 201
    assert read(work)["activities_since_checkpoint"] == 1
    attempt = body(work, item("approval"))
    attempt["changes"][0]["decision"] = {"reason": "Fixture owner released the hold", "references": [
        {"reference_type": "ACTIVITY", "reference_id": note.json()["activity_id"]}]}
    assert post(work, attempt).status_code == 201
    assert read(work)["held"] == []


def test_failed_evidence_leaves_no_checkpoint_or_version_change(work):
    before = read(work)
    payload = body(work, item(references=[{"reference_type": "ACTIVITY", "reference_id": str(uuid4())}]))
    result = post(work, payload)
    assert result.status_code == 422
    assert read(work)["context_token"] == before["context_token"]
    assert read(work)["checkpoint_status"] == "NOT_SET"


def test_simultaneous_context_updates_have_one_winner(work):
    payload = body(work, item())
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: post(work, payload), range(2)))
    assert sorted(result.status_code for result in outcomes) == [201, 409]
    assert read(work)["counts"]["activities"] == 1


def test_simultaneous_identical_retry_returns_one_receipt(work):
    payload = body(work, item())
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: post(work, payload, key), range(2)))
    assert [result.status_code for result in outcomes] == [201, 201]
    assert outcomes[0].json() == outcomes[1].json()
    assert read(work)["counts"]["activities"] == 1
