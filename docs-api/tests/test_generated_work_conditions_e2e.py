from __future__ import annotations

import hashlib
import os
import uuid

import pytest

if not os.environ.get("DB_HOST"):
    pytest.skip("requires a PostgreSQL database (set DB_HOST etc.)", allow_module_level=True)

os.environ.setdefault("DOCPLANE_EVENT_CURSOR_SECRET", "work-condition-e2e-secret-0123456789abcdef")
os.environ.setdefault("DOCPLANE_BOOTSTRAP_TOKEN", "work-condition-e2e-bootstrap")

from fastapi.testclient import TestClient  # noqa: E402

from app.application import app  # noqa: E402
from app.db import get_conn  # noqa: E402

RUN = uuid.uuid4().hex[:8]
client = TestClient(app)


def _mint_contributor() -> tuple[str, dict[str, str]]:
    token = f"dp_work_condition_{uuid.uuid4().hex}"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO docplane.principals (display_name, principal_kind) VALUES (%s, 'AUTOMATION') RETURNING principal_id::text",
            (f"work-condition-e2e-{RUN}",),
        )
        principal_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO docplane.api_tokens (principal_id, token_hash, token_prefix, description) VALUES (%s, %s, %s, 'work condition e2e')",
            (principal_id, hashlib.sha256(token.encode()).hexdigest(), token[:12]),
        )
        conn.commit()
    return principal_id, {"Authorization": f"Bearer {token}"}


def _seed_artifact(principal_id: str) -> str:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO model.entities
                (entity_kind, entity_key, display_name, attributes, created_by, idempotency_key)
            VALUES ('SYSTEM', %s, %s, '{}'::jsonb, %s, %s)
            RETURNING entity_id::text
            """,
            (f"work-condition-source-{RUN}", f"Work condition source {RUN}", principal_id, f"source-{RUN}"),
        )
        source_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO model.generated_artifacts
                (artifact_key, generator_name, generator_version, source_entity_id, declared_by, idempotency_key)
            VALUES (%s, 'work-condition-e2e', '1', %s, %s, %s)
            RETURNING artifact_id::text
            """,
            (f"work-condition-artifact-{RUN}", source_id, principal_id, f"artifact-{RUN}"),
        )
        artifact_id = cur.fetchone()[0]
        conn.commit()
    return artifact_id


def _rows(artifact_id: str) -> dict[str, dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT condition_id::text, condition_kind, briefing, status, version, resolved_at
              FROM work.generated_artifact_conditions
             WHERE artifact_id = %s
             ORDER BY condition_kind
            """,
            (artifact_id,),
        )
        return {
            row[1]: {
                "condition_id": row[0], "briefing": row[2], "status": row[3],
                "version": int(row[4]), "resolved_at": row[5],
            }
            for row in cur.fetchall()
        }


def _event_count(artifact_id: str) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM docplane.events WHERE event_type = 'WORK_GENERATED_ARTIFACT_CONDITIONS_RECONCILED' AND resource_id = %s",
            (artifact_id,),
        )
        return int(cur.fetchone()[0])


def _put(artifact_id: str, headers: dict[str, str], conditions: list[dict], key: str):
    return client.put(
        f"/api/v1/work/generated-artifacts/{artifact_id}/conditions",
        json={"conditions": conditions},
        headers={**headers, "Idempotency-Key": key},
    )


def test_generated_conditions_open_refresh_resolve_reopen_and_zero_write():
    principal_id, headers = _mint_contributor()
    artifact_id = _seed_artifact(principal_id)

    opened = _put(
        artifact_id, headers,
        [
            {"condition_kind": "DRIFTED", "briefing": {"reason": "SOURCE_CHANGED"}},
            {"condition_kind": "EXECUTION_CONTRACT_MISSING", "briefing": {"status": "UNDECLARED_TRANSITIONAL"}},
        ],
        f"work-condition-open-{RUN}",
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["opened"] == ["DRIFTED", "EXECUTION_CONTRACT_MISSING"]
    assert opened.json()["changed"] is True
    initial = _rows(artifact_id)
    drifted_id = initial["DRIFTED"]["condition_id"]
    assert initial["DRIFTED"]["version"] == 1
    assert _event_count(artifact_id) == 1

    noop = _put(
        artifact_id, headers,
        [
            {"condition_kind": "EXECUTION_CONTRACT_MISSING", "briefing": {"status": "UNDECLARED_TRANSITIONAL"}},
            {"condition_kind": "DRIFTED", "briefing": {"reason": "SOURCE_CHANGED"}},
        ],
        f"work-condition-noop-{RUN}",
    )
    assert noop.status_code == 200, noop.text
    assert noop.json()["changed"] is False
    assert set(noop.json()["continuing"]) == {"DRIFTED", "EXECUTION_CONTRACT_MISSING"}
    assert _rows(artifact_id)["DRIFTED"]["version"] == 1
    assert _event_count(artifact_id) == 1

    refreshed = _put(
        artifact_id, headers,
        [
            {"condition_kind": "DRIFTED", "briefing": {"reason": "SOURCE_CHANGED", "projection_correspondence": "MISMATCH"}},
            {"condition_kind": "EXECUTION_CONTRACT_MISSING", "briefing": {"status": "UNDECLARED_TRANSITIONAL"}},
        ],
        f"work-condition-refresh-{RUN}",
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["refreshed"] == ["DRIFTED"]
    assert _rows(artifact_id)["DRIFTED"]["version"] == 2
    assert _event_count(artifact_id) == 2

    resolved = _put(
        artifact_id, headers,
        [{"condition_kind": "EXECUTION_CONTRACT_MISSING", "briefing": {"status": "UNDECLARED_TRANSITIONAL"}}],
        f"work-condition-resolve-{RUN}",
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["resolved"] == ["DRIFTED"]
    after_resolve = _rows(artifact_id)
    assert after_resolve["DRIFTED"]["status"] == "RESOLVED"
    assert after_resolve["DRIFTED"]["resolved_at"] is not None
    assert after_resolve["DRIFTED"]["version"] == 3

    reopened = _put(
        artifact_id, headers,
        [
            {"condition_kind": "DRIFTED", "briefing": {"reason": "SOURCE_CHANGED_AGAIN"}},
            {"condition_kind": "EXECUTION_CONTRACT_MISSING", "briefing": {"status": "UNDECLARED_TRANSITIONAL"}},
        ],
        f"work-condition-reopen-{RUN}",
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["reopened"] == ["DRIFTED"]
    after_reopen = _rows(artifact_id)
    assert after_reopen["DRIFTED"]["condition_id"] == drifted_id
    assert after_reopen["DRIFTED"]["status"] == "OPEN"
    assert after_reopen["DRIFTED"]["resolved_at"] is None
    assert after_reopen["DRIFTED"]["version"] == 4

    listing = client.get(f"/api/v1/work/generated-artifacts/{artifact_id}/conditions", headers=headers)
    assert listing.status_code == 200, listing.text
    assert {item["condition_kind"] for item in listing.json()["conditions"]} == {"DRIFTED", "EXECUTION_CONTRACT_MISSING"}

    secret = _put(
        artifact_id, headers,
        [{"condition_kind": "GENERATION_FAILED", "briefing": {"password": "super-secret-value"}}],
        f"work-condition-secret-{RUN}",
    )
    assert secret.status_code == 422
    assert secret.json()["detail"]["code"] == "WORK_CONDITIONS_REJECTED"


def test_generated_condition_replay_and_conflicting_reuse():
    principal_id, headers = _mint_contributor()
    artifact_id = _seed_artifact(principal_id)
    key = f"work-condition-replay-{RUN}-{uuid.uuid4().hex[:6]}"
    body = [{"condition_kind": "GENERATION_FAILED", "briefing": {"reason": "LATEST_GENERATION_FAILED"}}]
    first = _put(artifact_id, headers, body, key)
    replay = _put(artifact_id, headers, body, key)
    assert first.status_code == 200 and replay.status_code == 200
    assert replay.json() == first.json()
    assert _event_count(artifact_id) == 1

    conflict = _put(
        artifact_id, headers,
        [{"condition_kind": "DRIFTED", "briefing": {"reason": "SOURCE_CHANGED"}}],
        key,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"
