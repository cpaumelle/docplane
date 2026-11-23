"""Inbox contract 2 against a migrated disposable synthetic database."""
import hashlib
import os
from uuid import uuid4

import pytest
if not os.environ.get("DB_HOST"):
    pytest.skip("requires disposable PostgreSQL", allow_module_level=True)

from fastapi.testclient import TestClient
from app.application import app
from app.db import get_conn

client = TestClient(app)
V1_ITEM = {"delivery_id", "position", "integrity_failed", "envelope", "signature", "body", "body_withheld", "acknowledged"}
V1_ENVELOPE = {"version", "kid", "delivery_id", "initiative_id", "recipient_id", "position", "activity_id",
               "sender_id", "sender_role", "reported_producer", "body_sha256"}


class Lane:
    """Four participants: two architects, two agents. One outsider without a grant."""

    def __init__(self):
        self.ids, self.headers = {}, {}
        names = ("arch", "arch2", "agent", "agent2", "outsider")
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT workspace_id FROM docplane.workspaces WHERE workspace_key='work'")
            workspace = cur.fetchone()[0]
            for name in names:
                token = "synthetic-" + uuid4().hex
                cur.execute("INSERT INTO docplane.principals(display_name,principal_kind,authorization_mode) VALUES (%s,'AGENT','WORK_COLLABORATOR') RETURNING principal_id::text", (name,))
                self.ids[name] = cur.fetchone()[0]
                cur.execute("INSERT INTO docplane.api_tokens(principal_id,token_hash,token_prefix) VALUES (%s,%s,'fixture')", (self.ids[name], hashlib.sha256(token.encode()).hexdigest()))
                self.headers[name] = {"Authorization": "Bearer " + token}
            cur.execute("""INSERT INTO work.initiatives (initiative_key,workspace_id,title,objective,owner_principal_id,idempotency_key)
                VALUES (%s,%s,'synthetic','synthetic',%s,%s) RETURNING initiative_id::text""",
                        ("inbox2-" + uuid4().hex, workspace, self.ids["arch"], str(uuid4())))
            self.initiative = cur.fetchone()[0]
            for name in names[:4]:
                cur.execute("INSERT INTO work.collaboration_grants(initiative_id,principal_id,participant_role,can_contribute) VALUES (%s,%s,%s,true)",
                            (self.initiative, self.ids[name], "ARCHITECT" if name.startswith("arch") else "AGENT"))
            conn.commit()
        self.root = f"/api/v1/work-inbox/{self.initiative}"

    def post(self, sender, to, body="synthetic", key=None, **extra):
        return client.post(self.root + "/messages", headers={**self.headers[sender], "Idempotency-Key": key or str(uuid4())},
                           json={"recipient_id": self.ids[to], "body": body, **extra})

    def send(self, *args, **kwargs):
        response = self.post(*args, **kwargs)
        assert response.status_code == 201, response.text
        return response.json()

    def get(self, who, path="/messages", **params):
        response = client.get(self.root + path, headers=self.headers[who], params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def open(self, who):
        return [m["delivery_id"] for m in self.get(who, contract=2, view="open")["messages"]]

    def ack(self, who, delivery):
        assert client.post(f"{self.root}/messages/{delivery}/ack", headers=self.headers[who]).status_code == 200

    def sql(self, statement, params):
        with get_conn() as conn:
            conn.cursor().execute(statement, params)
            conn.commit()


@pytest.fixture
def lane(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCPLANE_WORK_INBOX_CURSOR_SECRET", "cursor-secret-for-synthetic-tests-12345")
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "v1").write_text("signing-secret-for-synthetic-tests-1234")
    (keys / "v1").chmod(0o400)
    monkeypatch.setenv("DOCPLANE_WORK_INBOX_SIGNING_KEYS_DIR", str(keys))
    monkeypatch.setenv("DOCPLANE_WORK_INBOX_ACTIVE_KID", "v1")
    return Lane()


def test_contract_one_shapes_are_unchanged_even_for_contract_two_messages(lane):
    plain = lane.send("arch", "agent")
    assert set(plain) == {"delivery_id", "activity_id", "position"}
    lane.send("arch", "agent", kind="TASK")
    page = lane.get("agent")
    assert set(page) == {"messages", "next_cursor"}
    for item in page["messages"]:
        assert set(item) == V1_ITEM and set(item["envelope"]) == V1_ENVELOPE and item["envelope"]["version"] == 1


def test_question_then_answer_then_result(lane):
    task = lane.send("arch", "agent", kind="TASK")
    assert task["thread_id"] == task["delivery_id"]
    lane.ack("agent", task["delivery_id"])
    assert lane.open("agent") == [task["delivery_id"]], "an acknowledgement is not completion"
    question = lane.send("agent", "arch", kind="QUESTION", in_reply_to=task["delivery_id"])
    assert question["thread_id"] == task["delivery_id"]
    assert task["delivery_id"] in lane.open("agent"), "a question is not completion"
    lane.ack("arch", question["delivery_id"])
    assert lane.open("arch") == [question["delivery_id"]], "a read question is not an answered one"
    answer = lane.send("arch", "agent", kind="ANSWER", in_reply_to=question["delivery_id"])
    assert lane.open("arch") == []
    lane.ack("agent", answer["delivery_id"])
    result = lane.send("agent", "arch", kind="RESULT", in_reply_to=answer["delivery_id"])
    assert result["thread_id"] == task["delivery_id"] and lane.open("agent") == []
    item = lane.get("arch", contract=2)["messages"][-1]
    assert item["context"] == {"kind": "RESULT", "in_reply_to": answer["delivery_id"], "thread_id": task["delivery_id"]}


@pytest.mark.parametrize("kind", ["RESULT", "FAILED", "DECLINED"])
def test_each_terminal_report_closes_a_task(lane, kind):
    task = lane.send("arch", "agent", kind="TASK")
    lane.send("agent", "arch", kind=kind, in_reply_to=task["delivery_id"])
    assert lane.open("agent") == []


def test_closing_a_task_also_closes_its_question(lane):
    task = lane.send("arch", "agent", kind="TASK")
    lane.send("agent", "arch", kind="QUESTION", in_reply_to=task["delivery_id"])
    result = lane.send("agent", "arch", kind="RESULT", in_reply_to=task["delivery_id"])
    assert lane.open("arch") == [result["delivery_id"]]


def test_only_the_task_sender_cancels_and_only_the_worker_reports(lane):
    task = lane.send("arch", "agent", kind="TASK")["delivery_id"]
    assert lane.post("agent", "arch", kind="CANCELLED", in_reply_to=task).json()["detail"]["code"] == "INBOX_KIND_NOT_YOURS"
    assert lane.post("arch", "agent", kind="RESULT", in_reply_to=task).json()["detail"]["code"] == "INBOX_KIND_NOT_YOURS"
    cancel = lane.send("arch", "agent", kind="CANCELLED", in_reply_to=task)
    assert lane.open("agent") == [cancel["delivery_id"]]


def test_one_blocked_task_does_not_hide_later_work_from_any_sender(lane):
    first = lane.send("arch", "agent", kind="TASK")["delivery_id"]
    second = lane.send("arch2", "agent", kind="TASK")["delivery_id"]
    assert lane.open("agent") == [first, second]
    lane.send("agent", "arch2", kind="RESULT", in_reply_to=second)
    assert lane.open("agent") == [first]


def test_threads_have_two_parties(lane):
    task = lane.send("arch", "agent", kind="TASK")["delivery_id"]
    for sender, to, parent in (("agent2", "arch", task), ("agent", "arch2", task), ("agent", "arch", str(uuid4()))):
        refused = lane.post(sender, to, kind="RESULT", in_reply_to=parent)
        assert refused.json()["detail"]["code"] == "INBOX_REPLY_TARGET_INVALID"
    assert lane.open("agent") == [task]


def test_a_task_prompted_by_another_message_has_its_own_thread(lane):
    note = lane.send("agent", "arch")["delivery_id"]
    task = lane.send("arch", "agent", kind="TASK", in_reply_to=note)
    assert task["thread_id"] == task["delivery_id"]


def test_reply_to_a_contract_one_message(lane):
    old = lane.send("arch", "agent")["delivery_id"]
    assert lane.send("agent", "arch", kind="RESULT", in_reply_to=old)["thread_id"] == old


@pytest.mark.parametrize("extra,code", [
    ({"kind": "RESULT"}, "INBOX_REPLY_TARGET_REQUIRED"),
    ({"kind": "GRANT"}, "INBOX_KIND_INVALID"),
    ({"kind": ["TASK"]}, "INBOX_KIND_INVALID"),
    ({"in_reply_to": "not-a-uuid"}, "INBOX_REPLY_TARGET_INVALID"),
    ({"in_reply_to": 7}, "INBOX_REPLY_TARGET_INVALID"),
])
def test_invalid_context_is_refused_with_its_reason(lane, extra, code):
    response = lane.post("arch", "agent", **extra)
    assert response.status_code == 422 and response.json()["detail"]["code"] == code


def test_altered_context_fails_integrity_for_both_contracts(lane):
    task = lane.send("arch", "agent", kind="TASK")["delivery_id"]
    lane.sql("UPDATE work.inbox_message_context SET kind='NOTE' WHERE delivery_id=%s", (task,))
    for contract in (1, 2):
        item = lane.get("agent", contract=contract)["messages"][0]
        assert item["integrity_failed"] and item["body"] is None and item["envelope"] is None


def test_committed_send_replays_after_recipient_revocation(lane):
    key = str(uuid4())
    first = lane.send("arch", "agent", key=key, kind="TASK")
    lane.sql("UPDATE work.collaboration_grants SET revoked_at=now() WHERE initiative_id=%s AND principal_id=%s", (lane.initiative, lane.ids["agent"]))
    assert lane.send("arch", "agent", key=key, kind="TASK") == first
    assert lane.post("arch", "agent").json()["detail"]["code"] == "RECIPIENT_NOT_GRANTED"


def test_revoked_sender_gets_no_receipt(lane):
    key = str(uuid4())
    lane.send("arch", "agent", key=key)
    lane.sql("UPDATE work.collaboration_grants SET revoked_at=now() WHERE initiative_id=%s AND principal_id=%s", (lane.initiative, lane.ids["arch"]))
    assert lane.post("arch", "agent", key=key).json()["detail"]["code"] == "COLLABORATION_SCOPE_DENIED"


def test_context_is_part_of_the_idempotent_intent(lane):
    key = str(uuid4())
    lane.send("arch", "agent", key=key)
    assert lane.post("arch", "agent", key=key, kind="TASK").status_code == 409


def test_pages_are_bounded_by_bytes_and_lose_nothing(lane):
    body = "word \"quoted\" café\n" * 900
    sent = [lane.send("arch", "agent", body=f"{n} {body}")["delivery_id"] for n in range(45)]
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 100, "contract": 2, **({"cursor": cursor} if cursor else {})}
        response = client.get(lane.root + "/messages", headers=lane.headers["agent"], params=params)
        assert response.status_code == 200 and len(response.content) < 1048576
        page = response.json()
        if not page["messages"]:
            break
        seen += [m["delivery_id"] for m in page["messages"]]
        assert page["remaining"] == len(sent) - len(seen)
        cursor = page["next_cursor"]
        pages += 1
    assert seen == sent and pages > 1


def test_backlog_beyond_one_page_is_signalled(lane):
    for n in range(7):
        lane.send("arch", "agent", body=f"m{n}")
    assert lane.get("agent", contract=2, limit=5)["remaining"] == 2


def test_sent_shows_receipt_and_latest_reply_without_bodies(lane):
    task = lane.send("arch", "agent", body="private synthetic body", kind="TASK")["delivery_id"]
    question = lane.send("agent", "arch", kind="QUESTION", in_reply_to=task)["delivery_id"]
    answer = lane.send("arch", "agent", kind="ANSWER", in_reply_to=question)["delivery_id"]
    lane.send("agent", "arch", kind="RESULT", in_reply_to=answer)
    lane.ack("agent", task)
    page = lane.get("arch", "/sent", delivery_id=task)
    row, = page["sent"]
    assert row["acknowledged_at"] and row["latest_reply"]["kind"] == "RESULT" and row["context"]["kind"] == "TASK"
    assert "private synthetic body" not in str(page)
    assert lane.get("agent2", "/sent")["sent"] == [] and lane.get("agent2", "/sent", delivery_id=task)["sent"] == []
    assert lane.get("arch", "/sent", limit=1)["more"] is True


def test_discovery_is_limited_to_live_grants(lane):
    people = lane.get("agent", "/participants")
    assert people["participant_id"] == lane.ids["agent"]
    assert {p["name"] for p in people["participants"]} == {"arch", "arch2", "agent", "agent2"}
    for path in ("/participants", "/sent"):
        assert client.get(lane.root + path, headers=lane.headers["outsider"]).status_code == 403
    lane.send("arch", "agent")
    mine = client.get("/api/v1/work-inbox", headers=lane.headers["agent"]).json()
    row, = mine["initiatives"]
    assert row["initiative_id"] == lane.initiative and (row["unacknowledged"], row["latest_position"]) == (1, 1)
    assert client.get("/api/v1/work-inbox", headers=lane.headers["outsider"]).json()["initiatives"] == []
    lane.sql("UPDATE work.collaboration_grants SET expires_at=now()-interval '1 minute' WHERE initiative_id=%s AND principal_id=%s", (lane.initiative, lane.ids["arch2"]))
    assert "arch2" not in {p["name"] for p in lane.get("agent", "/participants")["participants"]}


def test_open_view_requires_contract_two(lane):
    response = client.get(lane.root + "/messages", headers=lane.headers["agent"], params={"view": "open"})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "INBOX_CONTRACT_REQUIRED"


def test_send_does_not_move_the_initiative_version(lane):
    def state():
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT version, updated_at FROM work.initiatives WHERE initiative_id=%s", (lane.initiative,))
            return tuple(cur.fetchone())
    before = state()
    lane.send("arch", "agent", kind="TASK")
    assert state() == before


def test_declining_a_question_leaves_the_task_open(lane):
    task = lane.send("arch", "agent", kind="TASK")["delivery_id"]
    asked = lane.send("arch", "agent", kind="QUESTION", in_reply_to=task)["delivery_id"]
    sibling = lane.send("arch", "agent", kind="QUESTION", in_reply_to=task)["delivery_id"]
    lane.send("agent", "arch", kind="DECLINED", in_reply_to=asked)
    assert lane.open("agent") == [task, sibling]


def test_altered_reply_neither_closes_work_nor_reports_a_result(lane):
    task = lane.send("arch", "agent", kind="TASK")["delivery_id"]
    progress = lane.send("agent", "arch", kind="PROGRESS", in_reply_to=task)["delivery_id"]
    lane.sql("UPDATE work.inbox_message_context SET kind='RESULT' WHERE delivery_id=%s", (progress,))
    assert lane.open("agent") == [task]
    assert lane.get("arch", "/sent", delivery_id=task)["sent"][0]["latest_reply"] is None


def test_altered_task_stays_visible_and_takes_no_replies(lane):
    task = lane.send("arch", "agent", kind="TASK")["delivery_id"]
    lane.ack("agent", task)
    lane.sql("UPDATE work.inbox_message_context SET kind='NOTE' WHERE delivery_id=%s", (task,))
    assert lane.open("agent") == [task]
    assert lane.get("agent", contract=2, view="open")["messages"][0]["integrity_failed"]
    assert lane.post("agent", "arch", kind="RESULT", in_reply_to=task).status_code == 422


def test_open_and_full_cursors_are_not_interchangeable(lane):
    lane.send("arch", "agent", kind="TASK")
    opened = lane.get("agent", contract=2, view="open")["next_cursor"]
    full = lane.get("agent", contract=2)["next_cursor"]
    for params in ({"cursor": opened}, {"cursor": full, "view": "open"}):
        response = client.get(lane.root + "/messages", headers=lane.headers["agent"], params={"contract": 2, **params})
        assert response.status_code == 400 and response.json()["detail"]["code"] == "INBOX_CURSOR_INVALID"


def test_open_view_pages_without_skipping(lane):
    tasks = [lane.send("arch", "agent", kind="TASK")["delivery_id"] for _ in range(5)]
    lane.send("agent", "arch", kind="RESULT", in_reply_to=tasks[1])
    first = lane.get("agent", contract=2, view="open", limit=2)
    second = lane.get("agent", contract=2, view="open", limit=2, cursor=first["next_cursor"])
    assert [m["delivery_id"] for m in first["messages"] + second["messages"]] == [tasks[0], *tasks[2:]]
    assert (first["remaining"], second["remaining"]) == (2, 0)


def test_replay_is_visible_only_when_asked_for(lane):
    key = str(uuid4())
    request = dict(headers={**lane.headers["arch"], "Idempotency-Key": key}, json={"recipient_id": lane.ids["agent"], "body": "continue"})
    url = lane.root + "/messages"
    assert client.post(url, params={"contract": 2}, **request).json()["replayed"] is False
    assert client.post(url, params={"contract": 2}, **request).json()["replayed"] is True
    assert "replayed" not in client.post(url, **request).json()


def test_every_sent_row_can_be_paged_to(lane):
    sent = [lane.send("arch", "agent", body=f"s{n}")["delivery_id"] for n in range(7)]
    seen, before = [], None
    while True:
        page = lane.get("arch", "/sent", limit=3, **({"before": before} if before else {}))
        seen += [r["delivery_id"] for r in page["sent"]]
        if not page["more"]:
            break
        before = page["sent"][-1]["delivery_id"]
    assert seen == sent[::-1]


def test_a_reply_whose_body_fails_integrity_neither_closes_work_nor_reports(lane):
    """Review of #241: correlation intact, stored body altered."""
    task = lane.send("arch", "agent", kind="TASK")["delivery_id"]
    result = lane.send("agent", "arch", kind="RESULT", in_reply_to=task)
    lane.sql("UPDATE work.initiative_activities SET body='altered after send' WHERE activity_id=%s", (result["activity_id"],))
    flagged = [m for m in lane.get("arch", contract=2)["messages"] if m["delivery_id"] == result["delivery_id"]]
    assert flagged[0]["integrity_failed"]
    assert lane.open("agent") == [task]
    assert lane.get("arch", "/sent", delivery_id=task)["sent"][0]["latest_reply"] is None
    assert lane.post("arch", "agent", kind="ANSWER", in_reply_to=result["delivery_id"]).status_code == 422


def test_a_reply_whose_envelope_fails_integrity_neither_closes_work_nor_reports(lane):
    task = lane.send("arch", "agent", kind="TASK")["delivery_id"]
    result = lane.send("agent", "arch", kind="RESULT", in_reply_to=task)["delivery_id"]
    lane.sql("UPDATE work.inbox_deliveries SET signature=repeat('0',64) WHERE delivery_id=%s", (result,))
    assert lane.open("agent") == [task]
    assert lane.get("arch", "/sent", delivery_id=task)["sent"][0]["latest_reply"] is None


def test_a_question_answered_by_an_altered_reply_stays_open(lane):
    task = lane.send("arch", "agent", kind="TASK")["delivery_id"]
    question = lane.send("agent", "arch", kind="QUESTION", in_reply_to=task)["delivery_id"]
    answer = lane.send("arch", "agent", kind="ANSWER", in_reply_to=question)
    lane.sql("UPDATE work.initiative_activities SET body='altered' WHERE activity_id=%s", (answer["activity_id"],))
    assert lane.open("arch") == [question]
