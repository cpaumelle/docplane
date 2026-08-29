"""PostgreSQL-backed end-to-end tests for the four-domain verbs.

These run against a real migrated database (CI provides one; locally set
DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASS). They prove the new verbs work
TRANSACTIONALLY: replay after a lost response returns the original receipt,
projections update inside the ingest transaction, gates refuse with
structured errors, ripple mints deduplicated requests, and the generated-
page guard fails closed at publish evaluation.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import psycopg2.errors

if not os.environ.get("DB_HOST"):
    pytest.skip("requires a PostgreSQL database (set DB_HOST etc.)", allow_module_level=True)

os.environ.setdefault("DOCPLANE_EVENT_CURSOR_SECRET", "e2e-cursor-secret-0123456789abcdef")
os.environ.setdefault("DOCPLANE_BOOTSTRAP_TOKEN", "e2e-bootstrap")

from fastapi.testclient import TestClient  # noqa: E402

from app.application import app  # noqa: E402
from app import observe_api  # noqa: E402
from app.db import get_conn  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import work_catalogue  # noqa: E402

RUN = uuid.uuid4().hex[:8]
client = TestClient(app)


def _mint_principal(kind: str, name: str) -> tuple[str, dict[str, str]]:
    token = f"dp_e2e_{uuid.uuid4().hex}"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO docplane.principals (display_name, principal_kind) VALUES (%s, %s) RETURNING principal_id::text",
            (name, kind),
        )
        principal_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO docplane.api_tokens (principal_id, token_hash, token_prefix, description) VALUES (%s, %s, %s, 'e2e')",
            (principal_id, hashlib.sha256(token.encode()).hexdigest(), token[:8]),
        )
        conn.commit()
    return principal_id, {"Authorization": f"Bearer {token}"}


AGENT_ID, AGENT = _mint_principal("AGENT", f"e2e-agent-{RUN}")
OTHER_ID, OTHER = _mint_principal("AGENT", f"e2e-other-{RUN}")
AUTOMATION_ID, AUTOMATION = _mint_principal("AUTOMATION", f"e2e-automation-{RUN}")


def _seed_page(path: str, knowledge_class: str = "REFERENCE") -> dict[str, str]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT workspace_id FROM docplane.workspaces WHERE workspace_key = 'reference'")
        workspace_id = cur.fetchone()[0]
        revision = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO docs.pages (path, title, nav_path, content, revision, workspace_id,
                                    publication_state, knowledge_class, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, 'PUBLISHED', %s, 'e2e')
            RETURNING resource_id::text
            """,
            (path, f"E2E {RUN}", f"E2E/{path.rsplit('/', 1)[-1].removesuffix('.md')}", f"# E2E {RUN}\n", revision, workspace_id, knowledge_class),
        )
        resource_id = cur.fetchone()[0]
        conn.commit()
    return {"resource_id": resource_id, "revision": revision, "path": path}


def _work_workspace_id() -> str:
    body = client.get("/api/v1/workspaces", headers=AGENT).json()
    return next(item["workspace_id"] for item in body["workspaces"] if item["workspace_key"] == "work")


def _key() -> str:
    return f"e2e-{uuid.uuid4()}"


def _seed_initiative(label: str) -> dict:
    response = client.post(
        "/api/v1/initiatives",
        json={
            "initiative_key": f"e2e-activity-{label}-{RUN}",
            "workspace_id": _work_workspace_id(),
            "title": f"E2E activity {label}",
            "objective": "prove bounded activity evidence",
            "work_state": "ACTIVE",
        },
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_activity_append_is_secret_safe_attributable_and_replay_exact():
    initiative = _seed_initiative("contract")
    initiative_id = initiative["initiative_id"]
    key = str(uuid.uuid4())
    body = {
        "activity_type": "OBSERVATION",
        "title": "Bounded receiver proof",
        "classification": "MILESTONE / ACHIEVED",
        "body": "Receiver validation and exact replay passed.",
        "references": [
            {"reference_type": "PR", "reference_id": "cpaumelle/docplane#probe"},
            {"reference_type": "COMMIT", "reference_id": "commit:e49bee6"},
        ],
    }
    before = client.get(f"/api/v1/initiatives/{initiative_id}", headers=AGENT).json()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM docplane.events WHERE resource_type = 'INITIATIVE' AND resource_id = %s", (initiative_id,))
        event_count_before = cur.fetchone()[0]
    first = client.post(
        f"/api/v1/initiatives/{initiative_id}/activities",
        json=body, headers={**AGENT, "Idempotency-Key": key},
    )
    assert first.status_code == 201, first.text
    receipt = first.json()
    assert set(receipt) == {
        "activity_id", "initiative_id", "activity_type", "created_at",
        "author_principal_id",
    }
    assert receipt["author_principal_id"] == AGENT_ID
    assert receipt["initiative_id"] == initiative_id

    replay = client.post(
        f"/api/v1/initiatives/{initiative_id}/activities",
        json=body, headers={**AGENT, "Idempotency-Key": key},
    )
    assert replay.status_code == 201
    assert replay.json() == receipt
    changed = client.post(
        f"/api/v1/initiatives/{initiative_id}/activities",
        json={**body, "body": "different intent"},
        headers={**AGENT, "Idempotency-Key": key},
    )
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    after = client.get(f"/api/v1/initiatives/{initiative_id}", headers=AGENT).json()
    matching = [item for item in after["activities"] if item["activity_id"] == receipt["activity_id"]]
    assert len(matching) == 1
    assert matching[0]["author_principal_id"] == AGENT_ID
    assert matching[0]["metadata"] == {
        "title": body["title"],
        "classification": body["classification"],
        "references": body["references"],
    }
    for field in ("work_state", "version", "updated_at"):
        assert after[field] == before[field]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM work.initiative_activities WHERE initiative_id = %s AND author_principal_id = %s AND idempotency_key = %s",
            (initiative_id, AGENT_ID, key),
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT count(*) FROM docplane.mutation_receipts WHERE principal_id = %s AND idempotency_key = %s AND operation_type = 'INITIATIVE_ACTIVITY_APPEND'",
            (AGENT_ID, key),
        )
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM docplane.events WHERE resource_type = 'INITIATIVE' AND resource_id = %s", (initiative_id,))
        assert cur.fetchone()[0] == event_count_before


@pytest.mark.parametrize("secret_payload", [
    {"body": "a" * 64},
    {"body": "sha256:" + "b" * 64},
    {"body": "safe", "title": "c" * 64},
    {"body": "safe", "classification": "d" * 64},
    {"body": "safe", "references": [
        {"reference_type": "ARTIFACT", "reference_id": "e" * 64},
    ]},
])
def test_activity_append_rejects_secret_shaped_digest_without_echo(secret_payload):
    initiative_id = _seed_initiative(uuid.uuid4().hex[:6])["initiative_id"]
    payload = {"activity_type": "NOTE", **secret_payload}
    response = client.post(
        f"/api/v1/initiatives/{initiative_id}/activities",
        json=payload,
        headers={**AGENT, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ACTIVITY_BODY_SECRET_SHAPED"
    assert "HEX_SECRET" in response.json()["detail"]["classes"]
    for value in secret_payload.values():
        if isinstance(value, str):
            assert value not in response.text


def test_activity_append_validation_fails_before_mutation_and_never_echoes_body(caplog):
    initiative_id = _seed_initiative("refusals")["initiative_id"]
    oversized = "bounded-marker-" + "x" * 20000
    whitespace_cases = [
        {"activity_type": "NOTE", "body": "   "},
        {"activity_type": "NOTE", "body": "valid", "title": "   "},
        {"activity_type": "NOTE", "body": "valid", "classification": "   "},
        {"activity_type": "NOTE", "body": "valid", "references": [
            {"reference_type": "PR", "reference_id": "   "},
        ]},
    ]
    cases = [
        ({"activity_type": "NOTE", "body": oversized}, str(uuid.uuid4())),
        ({"activity_type": "NOTE", "body": "ok", "unknown": oversized}, str(uuid.uuid4())),
        *((payload, str(uuid.uuid4())) for payload in whitespace_cases),
    ]
    for payload, key in cases:
        response = client.post(
            f"/api/v1/initiatives/{initiative_id}/activities",
            json=payload, headers={**AGENT, "Idempotency-Key": key},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "ACTIVITY_REQUEST_INVALID"
        assert "bounded-marker" not in response.text
    canonical = str(uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"))
    invalid_keys = [
        "not-a-uuid",
        canonical.upper(),
        "{" + canonical + "}",
        " " + canonical,
        canonical + " ",
    ]
    for invalid_key in invalid_keys:
        malformed = client.post(
            f"/api/v1/initiatives/{initiative_id}/activities",
            json={"activity_type": "NOTE", "body": "safe"},
            headers={**AGENT, "Idempotency-Key": invalid_key},
        )
        assert malformed.status_code == 422
        assert malformed.json()["detail"] == {"code": "IDEMPOTENCY_KEY_INVALID"}
        assert invalid_key not in malformed.text
        assert invalid_key not in caplog.text
    missing_key = client.post(
        f"/api/v1/initiatives/{initiative_id}/activities",
        json={"activity_type": "NOTE", "body": "safe"}, headers=AGENT,
    )
    assert missing_key.status_code == 428
    assert missing_key.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    malformed_initiative = client.post(
        "/api/v1/initiatives/not-a-uuid/activities",
        json={"activity_type": "NOTE", "body": "safe"},
        headers={**AGENT, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert malformed_initiative.status_code == 422
    missing = client.post(
        f"/api/v1/initiatives/{uuid.uuid4()}/activities",
        json={"activity_type": "NOTE", "body": "safe"},
        headers={**AGENT, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert missing.status_code == 404
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM work.initiative_activities WHERE initiative_id = %s", (initiative_id,))
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT count(*) FROM docplane.mutation_receipts WHERE principal_id = %s AND idempotency_key = ANY(%s)",
            (AGENT_ID, [key for _, key in cases]),
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT count(*) FROM docplane.mutation_receipts "
            "WHERE principal_id = %s AND operation_type = 'INITIATIVE_ACTIVITY_APPEND' "
            "AND resource_ref = %s",
            (AGENT_ID, initiative_id),
        )
        assert cur.fetchone()[0] == 0


def test_activity_append_openapi_retains_the_structured_contract():
    paths = app.openapi()["paths"]
    operation = paths["/api/v1/initiatives/{initiative_id}/activities"]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert set(schema["properties"]) == {
        "activity_type", "title", "classification", "body", "references",
    }
    assert set(schema["required"]) == {"activity_type", "body"}
    headers = [
        parameter for parameter in operation["parameters"]
        if parameter.get("in") == "header" and parameter.get("name", "").lower() == "idempotency-key"
    ]
    assert headers == [{
        "name": "Idempotency-Key",
        "in": "header",
        "required": True,
        "schema": {"type": "string", "format": "uuid"},
    }]
    assert "caller-supplied UUID" in operation["description"]
    assert "whitespace-only" in operation["description"]

    # The explicit activity-only contract must not alter unrelated routes.
    workspace = paths["/api/v1/workspaces"]["post"]
    workspace_headers = [
        parameter for parameter in workspace["parameters"]
        if parameter.get("in") == "header" and parameter.get("name", "").lower() == "idempotency-key"
    ]
    assert len(workspace_headers) == 1
    assert workspace_headers[0]["required"] is False
    assert workspace_headers[0]["schema"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "title": "Idempotency-Key",
    }


def test_activity_append_concurrent_same_key_creates_at_most_one_activity():
    initiative_id = _seed_initiative("concurrent")["initiative_id"]
    key = str(uuid.uuid4())
    body = {"activity_type": "NOTE", "body": "one logical delivery"}

    def submit():
        return client.post(
            f"/api/v1/initiatives/{initiative_id}/activities",
            json=body, headers={**AGENT, "Idempotency-Key": key},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit(), range(2)))
    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json() == responses[1].json()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM work.initiative_activities WHERE initiative_id = %s AND idempotency_key = %s",
            (initiative_id, key),
        )
        assert cur.fetchone()[0] == 1


def test_capture_triage_replay_and_key_misuse():
    key = _key()
    body = {"body": f"replay-proof idea {RUN}", "kind": "IDEA"}
    first = client.post("/api/v1/work/captures", json=body, headers={**AGENT, "Idempotency-Key": key})
    assert first.status_code == 201, first.text
    replay = client.post("/api/v1/work/captures", json=body, headers={**AGENT, "Idempotency-Key": key})
    assert replay.status_code == 201
    assert replay.json()["capture_id"] == first.json()["capture_id"]
    misuse = client.post("/api/v1/work/captures", json={"body": "different intent", "kind": "IDEA"}, headers={**AGENT, "Idempotency-Key": key})
    assert misuse.status_code == 409
    assert misuse.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    capture_id = first.json()["capture_id"]
    promote_key = _key()
    promoted = client.post(f"/api/v1/work/captures/{capture_id}/promote", json={}, headers={**AGENT, "Idempotency-Key": promote_key})
    assert promoted.status_code == 200, promoted.text
    initiative_id = promoted.json()["initiative"]["initiative_id"]
    # The review's exact scenario: replay after a lost response must return
    # the original promotion receipt, NOT CAPTURE_ALREADY_TRIAGED.
    replayed = client.post(f"/api/v1/work/captures/{capture_id}/promote", json={}, headers={**AGENT, "Idempotency-Key": promote_key})
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["initiative"]["initiative_id"] == initiative_id
    # A FRESH key against the already-triaged capture is real misuse and
    # still gets the state-based refusal.
    fresh = client.post(f"/api/v1/work/captures/{capture_id}/promote", json={}, headers={**AGENT, "Idempotency-Key": _key()})
    assert fresh.status_code == 409
    assert fresh.json()["detail"]["code"] == "CAPTURE_ALREADY_TRIAGED"


def test_model_entity_lifecycle_checklists_secrets_and_ripple():
    key = _key()
    vm = {
        "entity_kind": "VM",
        "entity_key": f"e2e-vm-{RUN}",
        "display_name": "E2E VM",
        "attributes": {"vmid": 9913, "host_node": "px5-lemans", "ip": "10.35.1.213", "hostname": f"e2e-vm-{RUN}"},
    }
    created = client.post("/api/v1/model/entities", json=vm, headers={**AGENT, "Idempotency-Key": key})
    assert created.status_code == 201, created.text
    entity_id = created.json()["entity_id"]
    replay = client.post("/api/v1/model/entities", json=vm, headers={**AGENT, "Idempotency-Key": key})
    assert replay.status_code == 201 and replay.json()["entity_id"] == entity_id
    misuse = client.post("/api/v1/model/entities", json={**vm, "display_name": "changed"}, headers={**AGENT, "Idempotency-Key": key})
    assert misuse.status_code == 409 and misuse.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    incomplete = client.post("/api/v1/model/entities", json={**vm, "entity_key": f"e2e-vm2-{RUN}", "attributes": {"vmid": 1}}, headers={**AGENT, "Idempotency-Key": _key()})
    assert incomplete.status_code == 422 and incomplete.json()["detail"]["code"] == "MODEL_ATTRIBUTES_CHECKLIST_FAILED"
    secret = client.post("/api/v1/model/entities", json={**vm, "entity_key": f"e2e-vm3-{RUN}", "attributes": {**vm["attributes"], "db_password": "real-value"}}, headers={**AGENT, "Idempotency-Key": _key()})
    assert secret.status_code == 422 and secret.json()["detail"]["code"] == "MODEL_ATTRIBUTES_SECRET_SHAPED"

    page = _seed_page(f"reference/e2e-{RUN}-described.md")
    linked = client.post(f"/api/v1/model/entities/{entity_id}/page-links", json={"relation": "DESCRIBES", "page_resource_id": page["resource_id"]}, headers=AGENT)
    assert linked.status_code == 201, linked.text

    update = client.post(f"/api/v1/model/entities/{entity_id}/update", json={"expected_version": 1, "attributes": {**vm["attributes"], "ip": "10.35.1.214"}}, headers={**AGENT, "Idempotency-Key": _key()})
    assert update.status_code == 200, update.text
    ripples = [item for item in client.get("/api/v1/verification-requests?status=all", headers=AGENT).json()["requests"] if item["entity_id"] == entity_id and item["reason"] == "RIPPLE"]
    assert len(ripples) == 1
    # Reality moving again while the request is live must not mint a second.
    update2 = client.post(f"/api/v1/model/entities/{entity_id}/update", json={"expected_version": 2, "attributes": {**vm["attributes"], "ip": "10.35.1.215"}}, headers={**AGENT, "Idempotency-Key": _key()})
    assert update2.status_code == 200, update2.text
    ripples = [item for item in client.get("/api/v1/verification-requests?status=all", headers=AGENT).json()["requests"] if item["entity_id"] == entity_id and item["reason"] == "RIPPLE"]
    assert len(ripples) == 1


def test_observe_ingest_projection_freshness_and_safety():
    entity = client.post("/api/v1/model/entities", json={"entity_kind": "DATABASE", "entity_key": f"e2e-db-{RUN}", "display_name": "E2E DB", "attributes": {"engine": "postgres"}}, headers={**AGENT, "Idempotency-Key": _key()})
    entity_id = entity.json()["entity_id"]

    artifact = client.post("/api/v1/model/artifacts", json={"artifact_key": f"e2e-cat-{RUN}", "generator_name": "tbls", "generator_version": "1", "source_entity_id": entity_id}, headers={**AUTOMATION, "Idempotency-Key": _key()})
    assert artifact.status_code == 201, artifact.text
    assert artifact.json()["execution_contract"] is None
    assert artifact.json()["execution_contract_status"] == "UNDECLARED_TRANSITIONAL"
    artifact_id = artifact.json()["artifact_id"]
    denied = client.post("/api/v1/model/artifacts", json={"artifact_key": f"e2e-cat2-{RUN}", "generator_name": "tbls", "generator_version": "1", "source_entity_id": entity_id}, headers={**AGENT, "Idempotency-Key": _key()})
    assert denied.status_code == 422 and denied.json()["detail"]["code"] == "MODEL_ARTIFACT_REQUIRES_AUTOMATION"

    now = datetime.now(timezone.utc)
    batch = {"observations": [
        {"subject_entity_id": entity_id, "observation_kind": "FRESHNESS_CHECK", "source_fingerprint": "ab12cd34", "observed_at": now.isoformat()},
        {"subject_artifact_id": artifact_id, "observation_kind": "GENERATION", "source_fingerprint": "ab12cd34", "observed_at": now.isoformat()},
    ]}
    recorded = client.post("/api/v1/observations", json=batch, headers={**AGENT, "Idempotency-Key": _key()})
    assert recorded.status_code == 201, recorded.text
    status = client.get(f"/api/v1/model/artifacts/{artifact_id}/status", headers=AGENT).json()
    assert status["freshness"]["state"] == "FRESH"

    drift = {"observations": [{"subject_entity_id": entity_id, "observation_kind": "FRESHNESS_CHECK", "source_fingerprint": "ffff0000", "observed_at": (now + timedelta(seconds=1)).isoformat()}]}
    client.post("/api/v1/observations", json=drift, headers={**AGENT, "Idempotency-Key": _key()})
    assert client.get(f"/api/v1/model/artifacts/{artifact_id}/status", headers=AGENT).json()["freshness"]["state"] == "DRIFTED"

    # Regression control: the old query skipped this newer null fingerprint
    # and silently reused the preceding successful probe as current truth.
    failed_probe = {"observations": [{"subject_entity_id": entity_id, "observation_kind": "FRESHNESS_CHECK", "outcome": "FAILED", "observed_at": (now + timedelta(seconds=2)).isoformat()}]}
    client.post("/api/v1/observations", json=failed_probe, headers={**AGENT, "Idempotency-Key": _key()})
    freshness = client.get(f"/api/v1/model/artifacts/{artifact_id}/status", headers=AGENT).json()["freshness"]
    assert freshness["state"] == "UNKNOWN"
    assert freshness["source_fingerprint"] is None
    assert freshness["source_observation"]["outcome"] == "FAILED"
    history = client.get(
        f"/api/v1/observations?subject_entity_id={entity_id}&observation_kind=FRESHNESS_CHECK",
        headers=AGENT,
    ).json()["observations"]
    assert any(item["outcome"] == "NOMINAL" and item["source_fingerprint"] == "ffff0000" for item in history)

    for seconds, outcome in ((3, "UNKNOWN"), (4, "DEGRADED")):
        probe = {"observations": [{"subject_entity_id": entity_id, "observation_kind": "FRESHNESS_CHECK", "outcome": outcome, "observed_at": (now + timedelta(seconds=seconds)).isoformat()}]}
        client.post("/api/v1/observations", json=probe, headers={**AGENT, "Idempotency-Key": _key()})
        freshness = client.get(f"/api/v1/model/artifacts/{artifact_id}/status", headers=AGENT).json()["freshness"]
        assert freshness["state"] == "UNKNOWN"
        assert freshness["source_observation"]["outcome"] == outcome

    invalid_failed = {"observations": [{"subject_entity_id": entity_id, "observation_kind": "FRESHNESS_CHECK", "outcome": "FAILED", "source_fingerprint": "1234abcd"}]}
    assert client.post("/api/v1/observations", json=invalid_failed, headers={**AGENT, "Idempotency-Key": _key()}).status_code == 422

    restored_probe = {"observations": [{"subject_entity_id": entity_id, "observation_kind": "FRESHNESS_CHECK", "source_fingerprint": "ffff0000", "observed_at": (now + timedelta(seconds=5)).isoformat()}]}
    replay_key = _key()
    first = client.post("/api/v1/observations", json=restored_probe, headers={**AGENT, "Idempotency-Key": replay_key})
    replay = client.post("/api/v1/observations", json=restored_probe, headers={**AGENT, "Idempotency-Key": replay_key})
    assert first.status_code == 201 and replay.status_code == 201
    assert replay.json() == first.json()

    # TEST, SOAK_READING and other observation kinds cannot redefine freshness.
    unrelated = {"observations": [
        {"subject_entity_id": entity_id, "observation_kind": "TEST", "source_fingerprint": "1234abcd", "observed_at": (now + timedelta(seconds=6)).isoformat()},
        {"subject_entity_id": entity_id, "observation_kind": "SOAK_READING", "source_fingerprint": "5678abcd", "observed_at": (now + timedelta(seconds=7)).isoformat()},
        {"subject_entity_id": entity_id, "observation_kind": "DEPLOYED_VERSION", "source_fingerprint": "9abc1234", "observed_at": (now + timedelta(seconds=8)).isoformat()},
    ]}
    client.post("/api/v1/observations", json=unrelated, headers={**AGENT, "Idempotency-Key": _key()})
    assert client.get(f"/api/v1/model/artifacts/{artifact_id}/status", headers=AGENT).json()["freshness"]["source_fingerprint"] == "ffff0000"

    # A late-arriving OLDER observation never regresses the projection.
    late = {"observations": [{"subject_entity_id": entity_id, "observation_kind": "FRESHNESS_CHECK", "source_fingerprint": "ab12cd34", "observed_at": (now - timedelta(hours=1)).isoformat()}]}
    client.post("/api/v1/observations", json=late, headers={**AGENT, "Idempotency-Key": _key()})
    current = client.get(f"/api/v1/model/entities/{entity_id}/status", headers=AGENT).json()["current_status"]
    freshness_rows = [row for row in current if row["observation_kind"] == "FRESHNESS_CHECK"]
    assert freshness_rows[0]["source_fingerprint"] == "ffff0000"

    failed_generation = {"observations": [{"subject_artifact_id": artifact_id, "observation_kind": "GENERATION", "outcome": "FAILED", "source_fingerprint": "ffff0000", "observed_at": (now + timedelta(seconds=9)).isoformat()}]}
    client.post("/api/v1/observations", json=failed_generation, headers={**AGENT, "Idempotency-Key": _key()})
    freshness = client.get(f"/api/v1/model/artifacts/{artifact_id}/status", headers=AGENT).json()["freshness"]
    assert freshness["state"] == "FAILED"
    assert freshness["generation"]["outcome"] == "FAILED"
    assert freshness["generated_fingerprint"] == "ab12cd34"

    never_artifact = client.post("/api/v1/model/artifacts", json={"artifact_key": f"e2e-never-{RUN}", "generator_name": "tbls", "generator_version": "1", "source_entity_id": entity_id}, headers={**AUTOMATION, "Idempotency-Key": _key()})
    assert never_artifact.status_code == 201, never_artifact.text
    never = client.get(f"/api/v1/model/artifacts/{never_artifact.json()['artifact_id']}/status", headers=AGENT).json()["freshness"]
    assert never["state"] == "NEVER_GENERATED"

    future = {"observations": [{"subject_entity_id": entity_id, "observation_kind": "TEST", "observed_at": (now + timedelta(hours=2)).isoformat()}]}
    rejected = client.post("/api/v1/observations", json=future, headers={**AGENT, "Idempotency-Key": _key()})
    assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "OBSERVATION_BATCH_REJECTED"
    secret = {"observations": [{"subject_entity_id": entity_id, "observation_kind": "TEST", "payload": {"api_key": "not-a-placeholder"}}]}
    rejected = client.post("/api/v1/observations", json=secret, headers={**AGENT, "Idempotency-Key": _key()})
    assert rejected.status_code == 422


def test_execution_contract_api_expiry_drift_and_mutation_isolation(monkeypatch):
    source = client.post(
        "/api/v1/model/entities",
        json={"entity_kind": "SYSTEM", "entity_key": f"e2e-contract-{RUN}", "display_name": "E2E contract source"},
        headers={**AUTOMATION, "Idempotency-Key": _key()},
    )
    assert source.status_code == 201, source.text
    source_id = source.json()["entity_id"]
    contract = {
        "observation_owner_principal_id": AUTOMATION_ID,
        "observation_trigger": "MANUAL",
        "observation_max_age_seconds": 300,
        "generation_owner_principal_id": AUTOMATION_ID,
        "generation_trigger": "MANUAL",
        "exclusion_domain": f"e2e-contract-{RUN}",
    }
    declaration_key = _key()
    declaration_body = {
        "artifact_key": f"e2e-contract-{RUN}", "generator_name": "e2e-contract",
        "generator_version": "1", "source_entity_id": source_id,
        "execution_contract": contract,
    }
    artifact = client.post(
        "/api/v1/model/artifacts", json=declaration_body,
        headers={**AUTOMATION, "Idempotency-Key": declaration_key},
    )
    assert artifact.status_code == 201, artifact.text
    artifact_id = artifact.json()["artifact_id"]
    assert artifact.json()["execution_contract_status"] == "DECLARED"
    with get_conn() as conn:
        cur = conn.cursor()
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(
                "UPDATE model.generated_artifact_execution_contracts SET observation_trigger = 'CRON' WHERE artifact_id = %s",
                (artifact_id,),
            )
        conn.rollback()
    replay = client.post(
        "/api/v1/model/artifacts", json=declaration_body,
        headers={**AUTOMATION, "Idempotency-Key": declaration_key},
    )
    assert replay.json() == artifact.json()

    invalid_owner = client.post(
        "/api/v1/model/artifacts",
        json={**declaration_body, "artifact_key": f"e2e-invalid-owner-{RUN}",
              "execution_contract": {**contract, "observation_owner_principal_id": AGENT_ID}},
        headers={**AUTOMATION, "Idempotency-Key": _key()},
    )
    assert invalid_owner.status_code == 422
    assert invalid_owner.json()["detail"]["code"] == "EXECUTION_OWNER_NOT_ACTIVE_AUTOMATION"

    observed = datetime.now(timezone.utc) - timedelta(hours=1)
    observations = client.post(
        "/api/v1/observations",
        json={"observations": [
            {"subject_artifact_id": artifact_id, "observation_kind": "GENERATION",
             "source_fingerprint": "aaaaaaaa", "observed_at": observed.isoformat()},
            {"subject_entity_id": source_id, "observation_kind": "FRESHNESS_CHECK",
             "source_fingerprint": "aaaaaaaa", "observed_at": observed.isoformat()},
        ]},
        headers={**AUTOMATION, "Idempotency-Key": _key()},
    )
    assert observations.status_code == 201, observations.text
    monkeypatch.setattr(observe_api, "_utcnow", lambda: observed + timedelta(seconds=300))
    status = client.get(f"/api/v1/model/artifacts/{artifact_id}/status", headers=AUTOMATION).json()
    assert (status["freshness"]["state"], status["freshness"]["source_observation_status"]) == ("FRESH", "CURRENT")
    assert status["execution_contract_status"] == "DECLARED"

    monkeypatch.setattr(observe_api, "_utcnow", lambda: observed + timedelta(seconds=301))
    expired = client.get(f"/api/v1/model/artifacts/{artifact_id}/status", headers=AUTOMATION).json()
    assert (expired["freshness"]["state"], expired["freshness"]["reason"]) == ("UNKNOWN", "SOURCE_OBSERVATION_EXPIRED")

    generation_b = client.post(
        "/api/v1/observations",
        json={"observations": [{"subject_artifact_id": artifact_id,
            "observation_kind": "GENERATION", "source_fingerprint": "bbbbbbbb",
            "observed_at": (observed + timedelta(seconds=1)).isoformat()}]},
        headers={**AUTOMATION, "Idempotency-Key": _key()},
    )
    assert generation_b.status_code == 201, generation_b.text
    drift = client.get(f"/api/v1/model/artifacts/{artifact_id}/status", headers=AUTOMATION).json()["freshness"]
    assert (drift["state"], drift["reason"]) == ("DRIFTED", "SOURCE_CHANGED")
    assert (drift["projection_correspondence"], drift["source_observation_status"]) == ("MISMATCH", "EXPIRED")

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM model.artifact_targets WHERE artifact_id = %s", (artifact_id,))
        targets_before = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM observe.observations")
        observations_before = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM docs.pages")
        pages_before = cur.fetchone()[0]

    update_key = _key()
    update_body = {**contract, "expected_version": 1, "observation_max_age_seconds": 600}
    update = client.put(
        f"/api/v1/model/artifacts/{artifact_id}/execution-contract", json=update_body,
        headers={**AUTOMATION, "Idempotency-Key": update_key},
    )
    assert update.status_code == 200, update.text
    assert update.json()["version"] == 2
    assert update.json()["execution_contract"]["observation_max_age_seconds"] == 600
    exact_replay = client.put(
        f"/api/v1/model/artifacts/{artifact_id}/execution-contract", json=update_body,
        headers={**AUTOMATION, "Idempotency-Key": update_key},
    )
    assert exact_replay.json() == update.json()
    conflict = client.put(
        f"/api/v1/model/artifacts/{artifact_id}/execution-contract",
        json={**update_body, "observation_max_age_seconds": 601},
        headers={**AUTOMATION, "Idempotency-Key": update_key},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    read = client.get(f"/api/v1/model/artifacts/{artifact_id}", headers=AUTOMATION)
    assert read.status_code == 200
    assert read.json()["execution_contract"]["observation_max_age_seconds"] == 600
    entity_status = client.get(f"/api/v1/model/entities/{source_id}/status", headers=AUTOMATION).json()
    projected = next(item for item in entity_status["generated_artifacts"] if item["artifact_id"] == artifact_id)
    artifact_status = client.get(f"/api/v1/model/artifacts/{artifact_id}/status", headers=AUTOMATION).json()
    assert projected["freshness"] == artifact_status["freshness"]
    assert projected["execution_contract"] == artifact_status["execution_contract"]

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM model.artifact_targets WHERE artifact_id = %s", (artifact_id,))
        assert cur.fetchone()[0] == targets_before
        cur.execute("SELECT count(*) FROM observe.observations")
        assert cur.fetchone()[0] == observations_before
        cur.execute("SELECT count(*) FROM docs.pages")
        assert cur.fetchone()[0] == pages_before
        cur.execute("UPDATE docplane.principals SET status = 'SUSPENDED' WHERE principal_id = %s", (AUTOMATION_ID,))
        conn.commit()
    try:
        inactive = client.get(f"/api/v1/model/artifacts/{artifact_id}/status", headers=AGENT).json()
        assert inactive["execution_contract_status"] == "OWNER_INACTIVE"
        assert inactive["freshness"]["state"] == artifact_status["freshness"]["state"]
    finally:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE docplane.principals SET status = 'ACTIVE' WHERE principal_id = %s", (AUTOMATION_ID,))
            conn.commit()


def test_entity_status_preserves_all_artifacts_during_freshness_lookup():
    entity = client.post(
        "/api/v1/model/entities",
        json={
            "entity_kind": "DATABASE",
            "entity_key": f"e2e-status-db-{RUN}",
            "display_name": "E2E status DB",
            "attributes": {"engine": "postgres"},
        },
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert entity.status_code == 201, entity.text
    entity_id = entity.json()["entity_id"]

    expected = {}
    for number in (1, 2):
        fingerprint = f"abcd123{number}"
        artifact = client.post(
            "/api/v1/model/artifacts",
            json={
                "artifact_key": f"e2e-status-artifact-{RUN}-{number}",
                "generator_name": "status-regression",
                "generator_version": "1",
                "source_entity_id": entity_id,
            },
            headers={**AUTOMATION, "Idempotency-Key": _key()},
        )
        assert artifact.status_code == 201, artifact.text
        artifact_id = artifact.json()["artifact_id"]
        expected[artifact_id] = fingerprint
        generation = client.post(
            "/api/v1/observations",
            json={"observations": [{
                "subject_artifact_id": artifact_id,
                "observation_kind": "GENERATION",
                "source_fingerprint": fingerprint,
            }]},
            headers={**AGENT, "Idempotency-Key": _key()},
        )
        assert generation.status_code == 201, generation.text

    entity_status = client.get(f"/api/v1/model/entities/{entity_id}/status", headers=AGENT)
    assert entity_status.status_code == 200, entity_status.text
    returned = entity_status.json()["generated_artifacts"]
    assert {item["artifact_id"] for item in returned} == set(expected)
    assert [item["artifact_key"] for item in returned] == sorted(item["artifact_key"] for item in returned)
    for item in returned:
        freshness = item["freshness"]
        assert freshness["state"] == "UNKNOWN"
        assert freshness["reason"] == "SOURCE_UNOBSERVED"
        assert freshness["source_observation"] is None
        assert freshness["generated_fingerprint"] == expected[item["artifact_id"]]

        artifact_status = client.get(
            f"/api/v1/model/artifacts/{item['artifact_id']}/status",
            headers=AGENT,
        )
        assert artifact_status.status_code == 200, artifact_status.text
        assert artifact_status.json()["freshness"] == freshness


def test_work_catalogue_probe_drives_freshness_without_other_domain_mutation(monkeypatch):
    """Exercise the canary probe through the real API and prove OBSERVE is its
    only write domain. Source fixture changes between probes are explicit WORK
    setup, not probe behavior.
    """
    source_listing = client.get(
        "/api/v1/model/entities?entity_kind=SYSTEM&limit=1000", headers=AUTOMATION
    ).json()
    sources = [
        item for item in source_listing["entities"]
        if item["entity_key"] == work_catalogue.SOURCE_ENTITY_KEY
    ]
    if sources:
        assert len(sources) == 1
        source = sources[0]
    else:
        made = client.post(
            "/api/v1/model/entities",
            json={
                "entity_kind": "SYSTEM",
                "entity_key": work_catalogue.SOURCE_ENTITY_KEY,
                "display_name": "DocPlane work domain",
                "attributes": {"description": "Source of the generated work catalogue"},
            },
            headers={**AUTOMATION, "Idempotency-Key": _key()},
        )
        assert made.status_code == 201, made.text
        source = made.json()

    class ApiClient:
        def call(self, method, path, body=None, key=None):
            headers = dict(AUTOMATION)
            if key is not None:
                headers["Idempotency-Key"] = key
            response = client.request(method, path, json=body, headers=headers)
            if not response.is_success:
                raise RuntimeError(f"DocPlane API returned {response.status_code}")
            return response.json()

    api = ApiClient()
    initial_state = work_catalogue.fetch_state(api)
    generation_fingerprint = work_catalogue.fingerprint(initial_state)
    artifact = client.post(
        "/api/v1/model/artifacts",
        json={
            "artifact_key": f"e2e-work-probe-{RUN}",
            "generator_name": work_catalogue.GENERATOR_NAME,
            "generator_version": work_catalogue.GENERATOR_VERSION,
            "source_entity_id": source["entity_id"],
        },
        headers={**AUTOMATION, "Idempotency-Key": _key()},
    )
    assert artifact.status_code == 201, artifact.text
    artifact_id = artifact.json()["artifact_id"]
    generation = client.post(
        "/api/v1/observations",
        json={"observations": [{
            "subject_artifact_id": artifact_id,
            "observation_kind": "GENERATION",
            "outcome": "NOMINAL",
            "source_fingerprint": generation_fingerprint,
        }]},
        headers={**AUTOMATION, "Idempotency-Key": _key()},
    )
    assert generation.status_code == 201, generation.text

    def generation_ids():
        values = client.get(
            f"/api/v1/observations?subject_artifact_id={artifact_id}&observation_kind=GENERATION",
            headers=AUTOMATION,
        ).json()["observations"]
        return [item["observation_id"] for item in values]

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT resource_id::text, revision::text FROM docs.pages ORDER BY resource_id")
        page_revisions_before = cur.fetchall()
        cur.execute("SELECT count(*) FROM model.entities")
        entity_count_before = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM model.generated_artifacts")
        artifact_count_before = cur.fetchone()[0]
    generation_ids_before = generation_ids()

    unobserved = client.get(
        f"/api/v1/model/artifacts/{artifact_id}/status", headers=AUTOMATION
    ).json()["freshness"]
    assert (unobserved["state"], unobserved["reason"]) == ("UNKNOWN", "SOURCE_UNOBSERVED")

    matching_probe_id = str(uuid.uuid4())
    matched, succeeded = work_catalogue.observe_source(api, matching_probe_id)
    assert succeeded is True
    assert matched["source_fingerprint"] == generation_fingerprint
    fresh = client.get(
        f"/api/v1/model/artifacts/{artifact_id}/status", headers=AUTOMATION
    ).json()["freshness"]
    assert (fresh["state"], fresh["reason"]) == ("FRESH", "FINGERPRINTS_MATCH")

    source_observations_before_replay = client.get(
        f"/api/v1/observations?subject_entity_id={source['entity_id']}&observation_kind=FRESHNESS_CHECK",
        headers=AUTOMATION,
    ).json()["observations"]
    replay, succeeded = work_catalogue.observe_source(api, matching_probe_id)
    assert succeeded is True
    assert replay == matched
    source_observations_after_replay = client.get(
        f"/api/v1/observations?subject_entity_id={source['entity_id']}&observation_kind=FRESHNESS_CHECK",
        headers=AUTOMATION,
    ).json()["observations"]
    assert source_observations_after_replay == source_observations_before_replay

    changed = client.post(
        "/api/v1/work/captures",
        json={"body": f"Disposable probe drift fixture {RUN}", "kind": "IDEA"},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert changed.status_code == 201, changed.text
    changed_fingerprint = work_catalogue.fingerprint(work_catalogue.fetch_state(api))
    assert changed_fingerprint != generation_fingerprint
    with pytest.raises(RuntimeError):
        work_catalogue.observe_source(api, matching_probe_id)

    drifted, succeeded = work_catalogue.observe_source(api, str(uuid.uuid4()))
    assert succeeded is True
    assert drifted["source_fingerprint"] == changed_fingerprint
    drift = client.get(
        f"/api/v1/model/artifacts/{artifact_id}/status", headers=AUTOMATION
    ).json()["freshness"]
    assert (drift["state"], drift["reason"]) == ("DRIFTED", "SOURCE_CHANGED")

    class SourceReadFailure(Exception):
        pass

    with monkeypatch.context() as patch:
        patch.setattr(
            work_catalogue, "fetch_state",
            lambda probe_client: (_ for _ in ()).throw(SourceReadFailure()),
        )
        failed, succeeded = work_catalogue.observe_source(api, str(uuid.uuid4()))
    assert succeeded is False
    assert failed["outcome"] == "FAILED"
    unknown = client.get(
        f"/api/v1/model/artifacts/{artifact_id}/status", headers=AUTOMATION
    ).json()["freshness"]
    assert (unknown["state"], unknown["reason"]) == (
        "UNKNOWN", "SOURCE_OBSERVATION_FAILED",
    )

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT resource_id::text, revision::text FROM docs.pages ORDER BY resource_id")
        assert cur.fetchall() == page_revisions_before
        cur.execute("SELECT count(*) FROM model.entities")
        assert cur.fetchone()[0] == entity_count_before
        cur.execute("SELECT count(*) FROM model.generated_artifacts")
        assert cur.fetchone()[0] == artifact_count_before
        cur.execute(
            "SELECT status FROM model.generated_artifacts WHERE artifact_id = %s",
            (artifact_id,),
        )
        assert cur.fetchone()[0] == "DECLARED"
    assert generation_ids() == generation_ids_before


def test_closure_and_soak_gates_are_evidence_bound():
    workspace_id = _work_workspace_id()
    made = client.post("/api/v1/initiatives", json={"initiative_key": f"e2e-close-{RUN}", "workspace_id": workspace_id, "title": "E2E closure", "objective": "prove gates", "work_state": "ACTIVE"}, headers={**AGENT, "Idempotency-Key": _key()})
    assert made.status_code == 201, made.text
    initiative = made.json()

    blocked = client.post(f"/api/v1/initiatives/{initiative['initiative_id']}/transition", json={"expected_version": 1, "work_state": "COMPLETE"}, headers={**AGENT, "Idempotency-Key": _key()})
    assert blocked.status_code == 409
    missing = blocked.json()["detail"]["missing"]
    assert {item["domain"] for item in missing} == {"know", "model", "observe"}

    promo = client.put(f"/api/v1/initiatives/{initiative['initiative_id']}/promotion", json={"expected_version": 1, "promotion_state": "NOT_REQUIRED"}, headers=AGENT)
    assert promo.status_code == 200, promo.text

    # model=UPDATED without a resolvable evidence link stays blocked.
    still = client.post(f"/api/v1/initiatives/{initiative['initiative_id']}/transition", json={"expected_version": 2, "work_state": "COMPLETE", "model_disposition": "UPDATED", "observe_disposition": "NOT_REQUIRED", "observe_disposition_note": "no runtime surface"}, headers={**AGENT, "Idempotency-Key": _key()})
    assert still.status_code == 409
    assert any(item["code"] == "DISPOSITION_EVIDENCE_REQUIRED" for item in still.json()["detail"]["missing"])

    entity = client.post("/api/v1/model/entities", json={"entity_kind": "SERVICE", "entity_key": f"e2e-svc-{RUN}", "display_name": "E2E svc", "attributes": {}}, headers={**AGENT, "Idempotency-Key": _key()})
    entity_id = entity.json()["entity_id"]
    ghost = client.post(f"/api/v1/initiatives/{initiative['initiative_id']}/links", json={"relation": "UPDATES", "resource_type": "MODEL_ENTITY", "resource_id": str(uuid.uuid4())}, headers=AGENT)
    assert ghost.status_code == 404 and ghost.json()["detail"]["code"] == "LINK_RESOURCE_NOT_FOUND"
    link = client.post(f"/api/v1/initiatives/{initiative['initiative_id']}/links", json={"relation": "UPDATES", "resource_type": "MODEL_ENTITY", "resource_id": entity_id}, headers=AGENT)
    assert link.status_code == 201, link.text

    done = client.post(f"/api/v1/initiatives/{initiative['initiative_id']}/transition", json={"expected_version": 2, "work_state": "COMPLETE", "model_disposition": "UPDATED", "observe_disposition": "DEFERRED", "observe_disposition_note": "monitoring lands with the meter-list sprint"}, headers={**AGENT, "Idempotency-Key": _key()})
    assert done.status_code == 200, done.text
    inbox = client.get("/api/v1/work/captures?status=INBOX&limit=1000", headers=AGENT).json()["captures"]
    assert any("Deferred observe disposition" in item["body"] and initiative["initiative_key"] in item["body"] for item in inbox)

    soak = client.post("/api/v1/initiatives", json={"initiative_key": f"e2e-soak-{RUN}", "workspace_id": workspace_id, "title": "E2E soak", "objective": "prove soak gate", "work_state": "ACTIVE"}, headers={**AGENT, "Idempotency-Key": _key()})
    soak_id = soak.json()["initiative_id"]
    now = datetime.now(timezone.utc)
    soak_fields = {"soak_started_at": now.isoformat(), "soak_review_at": (now + timedelta(days=14)).isoformat(), "soak_success_criteria": "alert stays quiet", "soak_failure_conditions": "alert fires"}
    refused = client.post(f"/api/v1/initiatives/{soak_id}/transition", json={"expected_version": 1, "work_state": "SOAKING", **soak_fields}, headers={**AGENT, "Idempotency-Key": _key()})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "WORK_SOAK_REQUIRES_OBSERVABILITY"
    ok = client.post(f"/api/v1/initiatives/{soak_id}/transition", json={"expected_version": 1, "work_state": "SOAKING", "soak_monitoring_ref": "alert:e2e-disk-full", **soak_fields}, headers={**AGENT, "Idempotency-Key": _key()})
    assert ok.status_code == 200, ok.text


def test_meter_list_resolves_soak_refs_and_populates_coverage():
    """Sprint 6: MONITOR_RULE entities give soak references stable identity
    and drive the coverage view — gaps derived from the graph, never stubs."""
    from datetime import datetime, timedelta, timezone

    watched = client.post("/api/v1/model/entities", json={"entity_kind": "SERVICE", "entity_key": f"e2e-svc-watched-{RUN}", "display_name": "watched", "attributes": {}}, headers={**AGENT, "Idempotency-Key": _key()})
    unwatched = client.post("/api/v1/model/entities", json={"entity_kind": "SERVICE", "entity_key": f"e2e-svc-unwatched-{RUN}", "display_name": "unwatched", "attributes": {}}, headers={**AGENT, "Idempotency-Key": _key()})
    rule_key = f"rule.e2e-backupstale-{RUN}"
    rule = client.post(
        "/api/v1/model/entities",
        json={"entity_kind": "MONITOR_RULE", "entity_key": rule_key, "display_name": "BackupStale", "attributes": {"rule_kind": "alert", "expr": "up == 0", "has_description": False}},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert rule.status_code == 201, rule.text
    linked = client.post(
        f"/api/v1/model/entities/{rule.json()['entity_id']}/links",
        json={"relation": "WATCHES", "to_entity_id": watched.json()["entity_id"]},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert linked.status_code == 201, linked.text

    coverage = client.get("/api/v1/observe/coverage", headers=AGENT).json()
    by_key = {item["entity_key"]: item["watching_rules"] for item in coverage["services"]}
    assert by_key[f"e2e-svc-watched-{RUN}"] == 1
    assert f"e2e-svc-unwatched-{RUN}" in coverage["unwatched_services"]
    assert rule_key in coverage["rules_without_description"]

    initiative = client.post("/api/v1/initiatives", json={"initiative_key": f"e2e-resolve-{RUN}", "workspace_id": _work_workspace_id(), "title": "E2E soak resolution", "objective": "prove ref resolution", "work_state": "ACTIVE"}, headers={**AGENT, "Idempotency-Key": _key()})
    initiative_id = initiative.json()["initiative_id"]
    now = datetime.now(timezone.utc)
    soaked = client.post(
        f"/api/v1/initiatives/{initiative_id}/transition",
        json={"expected_version": 1, "work_state": "SOAKING", "soak_monitoring_ref": rule_key, "soak_started_at": now.isoformat(), "soak_review_at": (now + timedelta(days=7)).isoformat(), "soak_success_criteria": "quiet", "soak_failure_conditions": "fires"},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert soaked.status_code == 200, soaked.text
    fetched = client.get(f"/api/v1/initiatives/{initiative_id}", headers=AGENT).json()
    # A reference matching an ACTIVE meter-list rule is verified identity.
    assert fetched["soak_monitoring_ref_status"] == "RESOLVED_MONITOR_RULE"

    # Lifecycle, not just genesis: a service-label change removes the stale
    # WATCHES wire, and a rule removed from git retires — coverage and soak
    # resolution must both follow the graph, not history.
    removed = client.post(
        f"/api/v1/model/entities/{rule.json()['entity_id']}/links/remove",
        json={"relation": "WATCHES", "to_entity_id": watched.json()["entity_id"]},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert removed.status_code == 200 and removed.json()["removed"] is True, removed.text
    again = client.post(
        f"/api/v1/model/entities/{rule.json()['entity_id']}/links/remove",
        json={"relation": "WATCHES", "to_entity_id": watched.json()["entity_id"]},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    # A fresh removal of an absent link converges instead of failing.
    assert again.status_code == 200 and again.json()["removed"] is False
    coverage = client.get("/api/v1/observe/coverage", headers=AGENT).json()
    assert f"e2e-svc-watched-{RUN}" in coverage["unwatched_services"]

    retired = client.post(
        f"/api/v1/model/entities/{rule.json()['entity_id']}/retire",
        json={"expected_version": 1, "note": "absent from rule set"},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert retired.status_code == 200 and retired.json()["status"] == "RETIRED", retired.text
    coverage = client.get("/api/v1/observe/coverage", headers=AGENT).json()
    assert rule_key not in coverage["rules_without_description"]
    # The obsolete reference no longer resolves — it reads as a named
    # reference again, admitting SOAKING but never observe=UPDATED.
    fetched = client.get(f"/api/v1/initiatives/{initiative_id}", headers=AGENT).json()
    assert fetched["soak_monitoring_ref_status"] == "UNRESOLVED_NAMED_REFERENCE"

    # A rule restored in git reactivates its existing identity ((kind, key)
    # is unique across statuses — no duplicate card), and resolution returns.
    revived = client.post(
        f"/api/v1/model/entities/{rule.json()['entity_id']}/reactivate",
        json={"expected_version": 2, "note": "restored in rule set"},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert revived.status_code == 200 and revived.json()["status"] == "ACTIVE", revived.text
    fetched = client.get(f"/api/v1/initiatives/{initiative_id}", headers=AGENT).json()
    assert fetched["soak_monitoring_ref_status"] == "RESOLVED_MONITOR_RULE"


def test_coverage_ranks_by_criticality_and_surfaces_runbook_gaps():
    """Sprint 6 ratified contract: paging alerts without runbooks are a
    coverage subset, and gaps rank by criticality derived from the pages a
    service DESCRIBES. The scope block versions the surface explicitly."""
    paging = client.post(
        "/api/v1/model/entities",
        json={"entity_kind": "MONITOR_RULE", "entity_key": f"rule.e2e-paging-{RUN}", "display_name": "DiskFull", "attributes": {"rule_kind": "alert", "severity": "critical", "expr": "disk_free == 0", "has_description": True}},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert paging.status_code == 201, paging.text
    covered = client.post(
        "/api/v1/model/entities",
        json={"entity_kind": "MONITOR_RULE", "entity_key": f"rule.e2e-covered-{RUN}", "display_name": "DiskSlow", "attributes": {"rule_kind": "alert", "severity": "critical", "expr": "disk_slow == 1", "has_description": True, "runbook_url": "https://docs/runbooks/disk"}},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert covered.status_code == 201, covered.text

    plain_svc = client.post("/api/v1/model/entities", json={"entity_kind": "SERVICE", "entity_key": f"e2e-svc-plain-{RUN}", "display_name": "plain svc", "attributes": {}}, headers={**AGENT, "Idempotency-Key": _key()})
    assert plain_svc.status_code == 201, plain_svc.text
    critical_svc = client.post("/api/v1/model/entities", json={"entity_kind": "SERVICE", "entity_key": f"e2e-svc-critical-{RUN}", "display_name": "critical svc", "attributes": {}}, headers={**AGENT, "Idempotency-Key": _key()})
    page = _seed_page(f"reference/e2e-{RUN}-critical-svc.md")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE docs.pages SET criticality = 'OPERATIONAL_CRITICAL' WHERE resource_id = %s", (page["resource_id"],))
        conn.commit()
    linked = client.post(
        f"/api/v1/model/entities/{critical_svc.json()['entity_id']}/page-links",
        json={"relation": "DESCRIBES", "page_resource_id": page["resource_id"]},
        headers=AGENT,
    )
    assert linked.status_code == 201, linked.text

    coverage = client.get("/api/v1/observe/coverage", headers=AGENT).json()
    assert f"rule.e2e-paging-{RUN}" in coverage["paging_alerts_without_runbook"]
    assert f"rule.e2e-covered-{RUN}" not in coverage["paging_alerts_without_runbook"]
    by_key = {item["entity_key"]: item for item in coverage["services"]}
    assert by_key[f"e2e-svc-critical-{RUN}"]["criticality"] == "OPERATIONAL_CRITICAL"
    # The critical unwatched service outranks NORMAL/unclassified ones.
    unwatched = coverage["unwatched_services"]
    assert unwatched.index(f"e2e-svc-critical-{RUN}") < unwatched.index(f"e2e-svc-plain-{RUN}")
    # A rule with no WATCHES wire is a labelling gap in the rule files —
    # surfaced explicitly (the first fabric run counted 164 of 256).
    assert f"rule.e2e-paging-{RUN}" in coverage["rules_without_service"]
    assert "rules_without_service" in coverage["scope"]["implemented"]
    assert "paging_alerts_without_runbook" in coverage["scope"]["implemented"]
    assert "work_inbox_feed" in coverage["scope"]["follow_up"]


def test_verification_request_lifecycle_reconciles_evidence():
    page = _seed_page(f"reference/e2e-{RUN}-verify.md")
    made = client.post("/api/v1/verification-requests", json={"page_resource_id": page["resource_id"]}, headers={**AGENT, "Idempotency-Key": _key()})
    assert made.status_code == 201, made.text
    request_id = made.json()["request_id"]
    briefing_page = made.json()["briefing"]["pages"][0]
    assert briefing_page["revision"] == page["revision"]
    assert briefing_page["correction_policy"] == "DIRECT_PUBLISH"

    claimed = client.post(f"/api/v1/verification-requests/{request_id}/claim", headers=OTHER)
    assert claimed.status_code == 200
    hijack = client.post(f"/api/v1/verification-requests/{request_id}/complete", json={"outcome": "VERIFIED", "summary": "not mine"}, headers={**AGENT, "Idempotency-Key": _key()})
    assert hijack.status_code == 409 and hijack.json()["detail"]["code"] == "VERIFICATION_REQUEST_CLAIMED_BY_OTHER"

    hollow = client.post(f"/api/v1/verification-requests/{request_id}/complete", json={"outcome": "VERIFIED", "summary": "checked nothing"}, headers={**OTHER, "Idempotency-Key": _key()})
    assert hollow.status_code == 422 and hollow.json()["detail"]["code"] == "VERIFICATION_COMPLETION_UNRECONCILED"

    verified = client.post(f"/api/v1/pages/{page['resource_id']}/verify", json={"expected_revision": page["revision"], "notes": "e2e: compared quick-reference against fixture; values hold"}, headers={**OTHER, "Idempotency-Key": _key()})
    assert verified.status_code in (200, 201), verified.text
    verification_id = verified.json()["verification_receipt"]["verification_id"]

    complete = client.post(f"/api/v1/verification-requests/{request_id}/complete", json={"outcome": "VERIFIED", "summary": "revision-exact receipt attached", "verification_ids": [verification_id]}, headers={**OTHER, "Idempotency-Key": _key()})
    assert complete.status_code == 200, complete.text
    assert complete.json()["status"] == "COMPLETED"


def test_generated_page_guard_fails_closed_at_publish_evaluation():
    page = _seed_page(f"reference/e2e-{RUN}-generated.md")
    entity = client.post("/api/v1/model/entities", json={"entity_kind": "SCHEMA", "entity_key": f"e2e-schema-{RUN}", "display_name": "E2E schema", "attributes": {}}, headers={**AGENT, "Idempotency-Key": _key()})
    declared = client.post("/api/v1/model/artifacts", json={"artifact_key": f"e2e-gen-{RUN}", "generator_name": "tbls", "generator_version": "1", "source_entity_id": entity.json()["entity_id"], "target_page_resource_ids": [page["resource_id"]]}, headers={**AUTOMATION, "Idempotency-Key": _key()})
    assert declared.status_code == 201, declared.text
    # The declaration itself must arm the guard — no manual provenance
    # UPDATE here. (A hand-set flag is exactly how the Sprint 5 canary found
    # production pages declared-but-unprotected.)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT provenance FROM docs.pages WHERE resource_id = %s", (page["resource_id"],))
        assert cur.fetchone()[0] == "GENERATED"

    change = client.post("/api/v1/changes", json={"title": "E2E tamper", "purpose": "prove the guard", "workspace_key": "reference"}, headers={**AGENT, "Idempotency-Key": _key()})
    change_id = change.json()["change_id"]
    op = client.post(f"/api/v1/changes/{change_id}/operations", json={"operation_type": "REPLACE_DOCUMENT", "page_resource_id": page["resource_id"], "expected_revision": page["revision"], "payload": {"content": "# hand edit", "title": "tampered", "nav_path": "E2E/Tampered"}}, headers={**AGENT, "Idempotency-Key": _key()})
    assert op.status_code == 201, op.text
    validated = client.post(f"/api/v1/changes/{change_id}/validate", json={}, headers=AGENT)
    assert validated.status_code == 200
    warnings = validated.json()["validation_summary"]["warnings"]
    assert any(item.get("code") == "PROVENANCE_GENERATED_PAGE_PROTECTED" for item in warnings)
    published = client.post(f"/api/v1/changes/{change_id}/publish", json={}, headers={**AGENT, "Idempotency-Key": _key()})
    assert published.status_code == 409, published.text
    errors = published.json()["detail"]["validation"]["errors"]
    assert any(item.get("code") == "PROVENANCE_GENERATED_PAGE_PROTECTED" for item in errors)


def test_archived_page_requires_restore_and_restore_replace_publishes_atomically():
    page = _seed_page(f"reference/e2e-{RUN}-reopen.md")

    archive = client.post(
        "/api/v1/changes",
        json={"title": "Archive E2E page", "purpose": "exercise reopen lifecycle", "workspace_key": "reference"},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    archive_id = archive.json()["change_id"]
    operation = client.post(
        f"/api/v1/changes/{archive_id}/operations",
        json={
            "operation_type": "ARCHIVE_PAGE", "page_resource_id": page["resource_id"],
            "expected_revision": page["revision"], "payload": {},
        },
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert operation.status_code == 201, operation.text
    assert client.post(f"/api/v1/changes/{archive_id}/validate", json={}, headers=AGENT).status_code == 200
    archived = client.post(
        f"/api/v1/changes/{archive_id}/publish", json={},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert archived.status_code == 200, archived.text

    archived_page = client.get(
        f"/api/v1/pages/{page['resource_id']}", headers=AGENT,
    ).json()
    assert archived_page["status"] == "archived"

    duplicate = client.post(
        "/api/v1/changes",
        json={"title": "Invalid duplicate", "purpose": "prove structured refusal", "workspace_key": "reference"},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    duplicate_id = duplicate.json()["change_id"]
    duplicate_op = client.post(
        f"/api/v1/changes/{duplicate_id}/operations",
        json={
            "operation_type": "CREATE_PAGE",
            "payload": {
                "path": page["path"], "title": "Duplicate", "nav_path": "E2E/Duplicate",
                "content": "# duplicate must fail\n",
            },
        },
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert duplicate_op.status_code == 201, duplicate_op.text
    duplicate_validation = client.post(
        f"/api/v1/changes/{duplicate_id}/validate", json={}, headers=AGENT,
    )
    assert duplicate_validation.status_code == 200, duplicate_validation.text
    errors = duplicate_validation.json()["validation_summary"]["errors"]
    assert errors[0] == {
        "code": "CREATE_PATH_ARCHIVED_RESTORE_REQUIRED",
        "detail": page["resource_id"],
    }
    refused_publish = client.post(
        f"/api/v1/changes/{duplicate_id}/publish", json={},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert refused_publish.status_code == 409, refused_publish.text

    restore = client.post(
        "/api/v1/changes",
        json={"title": "Restore E2E page", "purpose": "prove atomic reopen", "workspace_key": "reference"},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    restore_id = restore.json()["change_id"]
    for operation_type, payload in (
        ("RESTORE_PAGE", {}),
        ("REPLACE_DOCUMENT", {
            "path": page["path"], "title": "Restored E2E", "nav_path": "E2E/Restored",
            "content": "# restored and replaced\n", "knowledge_class": "REFERENCE",
        }),
    ):
        response = client.post(
            f"/api/v1/changes/{restore_id}/operations",
            json={
                "operation_type": operation_type, "page_resource_id": page["resource_id"],
                "expected_revision": archived_page["revision"], "payload": payload,
            },
            headers={**AGENT, "Idempotency-Key": _key()},
        )
        assert response.status_code == 201, response.text
    assert client.post(f"/api/v1/changes/{restore_id}/validate", json={}, headers=AGENT).status_code == 200
    restored = client.post(
        f"/api/v1/changes/{restore_id}/publish", json={},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert restored.status_code == 200, restored.text
    current = client.get(f"/api/v1/pages/{page['resource_id']}", headers=AGENT).json()
    assert current["status"] == "active"
    assert current["content"] == "# restored and replaced\n"


def test_concurrent_same_key_promotions_serialize_to_one_mutation():
    """Concurrent duplicate delivery, not just sequential replay: two
    simultaneous same-key requests must both return the winner's response,
    with exactly one domain mutation and one triage event."""
    from concurrent.futures import ThreadPoolExecutor

    capture = client.post("/api/v1/work/captures", json={"body": f"concurrency-proof {RUN}", "kind": "IDEA"}, headers={**AGENT, "Idempotency-Key": _key()})
    capture_id = capture.json()["capture_id"]
    promote_key = _key()

    def promote():
        return client.post(f"/api/v1/work/captures/{capture_id}/promote", json={}, headers={**AGENT, "Idempotency-Key": promote_key})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: promote(), range(2)))
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["initiative"]["initiative_id"] == second.json()["initiative"]["initiative_id"]

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM work.initiatives WHERE idempotency_key = %s", (f"capture-promote:{promote_key}",))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM docplane.events WHERE idempotency_key = %s", (f"CAPTURE_TRIAGED:{AGENT_ID}:{promote_key}",))
        assert cur.fetchone()[0] == 1
    # Altered-intent reuse of the same key still refuses.
    misuse = client.post(f"/api/v1/work/captures/{capture_id}/promote", json={"title": "different intent"}, headers={**AGENT, "Idempotency-Key": promote_key})
    assert misuse.status_code == 409 and misuse.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_only_the_declaring_automation_can_retire_a_declaration():
    entity = client.post("/api/v1/model/entities", json={"entity_kind": "SCHEMA", "entity_key": f"e2e-retire-{RUN}", "display_name": "E2E retire", "attributes": {}}, headers={**AGENT, "Idempotency-Key": _key()})
    page = _seed_page(f"reference/e2e-{RUN}-retire-target.md")
    declared = client.post("/api/v1/model/artifacts", json={"artifact_key": f"e2e-ret-{RUN}", "generator_name": "tbls", "generator_version": "1", "source_entity_id": entity.json()["entity_id"], "target_page_resource_ids": [page["resource_id"]]}, headers={**AUTOMATION, "Idempotency-Key": _key()})
    artifact_id = declared.json()["artifact_id"]

    # An ordinary contributor must not be able to dismantle provenance
    # protection by retiring another principal's declaration.
    hijack = client.post(f"/api/v1/model/artifacts/{artifact_id}/retire", json={"expected_version": 1}, headers={**AGENT, "Idempotency-Key": _key()})
    assert hijack.status_code == 403 and hijack.json()["detail"]["code"] == "MODEL_ARTIFACT_RETIRE_FORBIDDEN"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM model.artifact_targets WHERE artifact_id = %s", (artifact_id,))
        assert cur.fetchone()[0] == 1  # targets intact, pages still protected
        cur.execute("SELECT provenance FROM docs.pages WHERE resource_id = %s", (page["resource_id"],))
        assert cur.fetchone()[0] == "GENERATED"  # declaration armed it; refused retire left it armed

    retired = client.post(f"/api/v1/model/artifacts/{artifact_id}/retire", json={"expected_version": 1}, headers={**AUTOMATION, "Idempotency-Key": _key()})
    assert retired.status_code == 409
    assert retired.json()["detail"]["code"] == "MODEL_ARTIFACT_RETIRE_HAS_ACTIVE_TARGETS"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT provenance FROM docs.pages WHERE resource_id = %s", (page["resource_id"],))
        assert cur.fetchone()[0] == "GENERATED"


def test_atomic_handoff_replaces_a_live_artifact_without_releasing_targets():
    """The full regeneration lifecycle (Sprint 5 canary, fourth finding):
    retire must release the key so a successor generation can take it back,
    while a key with a standing ACTIVE declaration keeps refusing."""
    entity = client.post("/api/v1/model/entities", json={"entity_kind": "SCHEMA", "entity_key": f"e2e-succ-{RUN}", "display_name": "E2E successor", "attributes": {}}, headers={**AGENT, "Idempotency-Key": _key()})
    page = _seed_page(f"reference/e2e-{RUN}-successor-target.md")
    artifact_key = f"e2e-succ-{RUN}"
    body = {"artifact_key": artifact_key, "generator_name": "tbls", "generator_version": "1", "source_entity_id": entity.json()["entity_id"], "target_page_resource_ids": [page["resource_id"]]}

    first = client.post("/api/v1/model/artifacts", json=body, headers={**AUTOMATION, "Idempotency-Key": _key()})
    assert first.status_code == 201, first.text

    # A second ACTIVE declaration on the same key must refuse.
    duplicate = client.post("/api/v1/model/artifacts", json={**body, "generator_version": "2"}, headers={**AUTOMATION, "Idempotency-Key": _key()})
    assert duplicate.status_code == 409 and duplicate.json()["detail"]["code"] == "MODEL_ARTIFACT_KEY_EXISTS"

    successor = client.post(
        f"/api/v1/model/artifacts/{first.json()['artifact_id']}/handoff",
        json={"expected_version": 1, "successor": {**body, "generator_version": "2", "projection_contract_version": 2, "target_page_paths": [page["path"]]}},
        headers={**AUTOMATION, "Idempotency-Key": _key()},
    )
    assert successor.status_code == 201, successor.text
    successor_artifact = successor.json()["successor"]
    assert successor_artifact["artifact_id"] != first.json()["artifact_id"]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT provenance FROM docs.pages WHERE resource_id = %s", (page["resource_id"],))
        assert cur.fetchone()[0] == "GENERATED"
        cur.execute("SELECT count(*) FROM model.generated_artifacts WHERE artifact_key = %s", (artifact_key,))
        assert cur.fetchone()[0] == 2  # retired generation stays as audit history


def test_generated_publication_owns_new_page_in_the_same_commit_and_archives_tombstone():
    entity = client.post(
        "/api/v1/model/entities",
        json={"entity_kind": "SYSTEM", "entity_key": f"e2e-atomic-{RUN}", "display_name": "atomic source", "attributes": {}},
        headers={**AGENT, "Idempotency-Key": _key()},
    ).json()
    artifact = client.post(
        "/api/v1/model/artifacts",
        json={"artifact_key": f"e2e-atomic-{RUN}", "generator_name": "e2e", "generator_version": "1", "projection_contract_version": 1, "source_entity_id": entity["entity_id"], "target_page_resource_ids": [], "target_page_paths": []},
        headers={**AUTOMATION, "Idempotency-Key": _key()},
    ).json()
    page_id = str(uuid.uuid4())
    path = f"reference/e2e-{RUN}-atomic-generated.md"
    plan = {
        "mode": "IN_PLACE", "artifact_id": artifact["artifact_id"], "expected_version": 1,
        "target_page_resource_ids": [page_id], "target_page_paths": [path], "generator_version": "1",
    }
    change = client.post(
        "/api/v1/changes",
        json={"title": "atomic generated create", "purpose": "prove page and ownership share a commit", "workspace_key": "reference", "generated_ownership_plan": plan},
        headers={**AUTOMATION, "Idempotency-Key": _key()},
    ).json()
    operation = client.post(
        f"/api/v1/changes/{change['change_id']}/operations",
        json={"operation_type": "CREATE_PAGE", "payload": {"resource_id": page_id, "path": path, "title": "Atomic", "nav_path": "E2E/Atomic", "content": "# atomic\n", "knowledge_class": "REFERENCE"}},
        headers={**AUTOMATION, "Idempotency-Key": _key()},
    )
    assert operation.status_code == 201, operation.text
    assert client.post(f"/api/v1/changes/{change['change_id']}/validate", json={}, headers=AUTOMATION).status_code == 200
    published = client.post(f"/api/v1/changes/{change['change_id']}/publish", json={}, headers={**AUTOMATION, "Idempotency-Key": _key()})
    assert published.status_code == 200, published.text
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT provenance, status FROM docs.pages WHERE resource_id = %s", (page_id,))
        assert cur.fetchone() == ("GENERATED", "active")
        cur.execute("SELECT artifact_id::text FROM model.artifact_targets WHERE page_resource_id = %s", (page_id,))
        assert cur.fetchone()[0] == artifact["artifact_id"]

    current = client.get(f"/api/v1/pages/{page_id}", headers=AUTOMATION).json()
    change2 = client.post(
        "/api/v1/changes",
        json={"title": "atomic generated archive", "purpose": "prove tombstone disposition", "workspace_key": "reference", "generated_ownership_plan": {**plan, "expected_version": 2, "target_page_resource_ids": [], "target_page_paths": []}},
        headers={**AUTOMATION, "Idempotency-Key": _key()},
    ).json()
    client.post(
        f"/api/v1/changes/{change2['change_id']}/operations",
        json={"operation_type": "ARCHIVE_PAGE", "page_resource_id": page_id, "expected_revision": current["revision"], "payload": {}},
        headers={**AUTOMATION, "Idempotency-Key": _key()},
    )
    assert client.post(f"/api/v1/changes/{change2['change_id']}/validate", json={}, headers=AUTOMATION).status_code == 200
    removed = client.post(f"/api/v1/changes/{change2['change_id']}/publish", json={}, headers={**AUTOMATION, "Idempotency-Key": _key()})
    assert removed.status_code == 200, removed.text
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT provenance, status FROM docs.pages WHERE resource_id = %s", (page_id,))
        assert cur.fetchone() == ("GENERATED", "archived")
        cur.execute("SELECT count(*) FROM model.artifact_targets WHERE page_resource_id = %s", (page_id,))
        assert cur.fetchone()[0] == 0


def test_rollback_image_tolerates_upgraded_database():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import migrate

    migrations = migrate.discover(Path(__file__).resolve().parents[2] / "db" / "migrations")
    with migrate.connect() as conn:
        migrate.ensure_ledger(conn)
        history = migrate.applied(conn)
        # The rollback scenario: an image that knows ONLY the genesis runs
        # against this fully-migrated database and tolerates what is ahead.
        ahead = migrate.verify_history(migrations[:1], history)
        assert len(ahead) == len(history) - 1
        # And the current image sees clean history with nothing ahead.
        assert migrate.verify_history(migrations, history) == []


def test_existing_principal_token_rotation_is_one_time_and_identity_preserving():
    bootstrap = {"X-DocPlane-Bootstrap-Token": os.environ["DOCPLANE_BOOTSTRAP_TOKEN"]}
    created = client.post(
        "/api/v1/bootstrap/principals",
        json={"display_name": f"e2e-token-rotation-{RUN}", "principal_kind": "AUTOMATION"},
        headers=bootstrap,
    )
    assert created.status_code == 201, created.text
    original = created.json()
    principal_id = original["principal_id"]
    original_auth = {"Authorization": f"Bearer {original['token']}"}

    source = client.post(
        "/api/v1/model/entities",
        json={
            "entity_kind": "DATABASE",
            "entity_key": f"e2e-token-source-{RUN}",
            "display_name": "E2E token source",
            "attributes": {"engine": "postgres"},
        },
        headers={**original_auth, "Idempotency-Key": _key()},
    )
    assert source.status_code == 201, source.text
    artifact = client.post(
        "/api/v1/model/artifacts",
        json={
            "artifact_key": f"e2e-token-artifact-{RUN}",
            "generator_name": "token-e2e",
            "generator_version": "1",
            "source_entity_id": source.json()["entity_id"],
        },
        headers={**original_auth, "Idempotency-Key": _key()},
    )
    assert artifact.status_code == 201, artifact.text

    issue_key = _key()
    issue_payload = {"description": "e2e rotation candidate", "expires_at": None}
    issued = client.post(
        f"/api/v1/bootstrap/principals/{principal_id}/tokens",
        json=issue_payload,
        headers={**bootstrap, "Idempotency-Key": issue_key},
    )
    assert issued.status_code == 201, issued.text
    replacement = issued.json()
    replacement_auth = {"Authorization": f"Bearer {replacement['token']}"}
    assert replacement["principal_id"] == principal_id
    assert replacement["bearer_returned"] is True and replacement["replayed"] is False
    assert client.get("/api/v1/me", headers=replacement_auth).json()["principal_id"] == principal_id

    replay = client.post(
        f"/api/v1/bootstrap/principals/{principal_id}/tokens",
        json=issue_payload,
        headers={**bootstrap, "Idempotency-Key": issue_key},
    )
    assert replay.status_code == 201
    assert replay.json()["token_id"] == replacement["token_id"]
    assert replay.json()["token"] is None
    assert replay.json()["bearer_returned"] is False and replay.json()["replayed"] is True
    conflict = client.post(
        f"/api/v1/bootstrap/principals/{principal_id}/tokens",
        json={"description": "conflicting intent"},
        headers={**bootstrap, "Idempotency-Key": issue_key},
    )
    assert conflict.status_code == 409

    listing = client.get(f"/api/v1/bootstrap/principals/{principal_id}/tokens", headers=bootstrap)
    assert listing.status_code == 200, listing.text
    listed = listing.json()
    assert len([item for item in listed["tokens"] if item["status"] == "ACTIVE"]) == 2
    listing_text = json.dumps(listed)
    assert original["token"] not in listing_text and replacement["token"] not in listing_text
    assert "token_hash" not in listing_text

    original_id = next(item["token_id"] for item in listed["tokens"] if item["token_id"] != replacement["token_id"])
    revoke_key = _key()
    revoked = client.post(
        f"/api/v1/bootstrap/principals/{principal_id}/tokens/{original_id}/revoke",
        headers={**bootstrap, "Idempotency-Key": revoke_key},
    )
    assert revoked.status_code == 200 and revoked.json()["status"] == "REVOKED"
    replayed = client.post(
        f"/api/v1/bootstrap/principals/{principal_id}/tokens/{original_id}/revoke",
        headers={**bootstrap, "Idempotency-Key": revoke_key},
    )
    assert replayed.status_code == 200 and replayed.json()["replayed"] is True
    assert client.get("/api/v1/me", headers=original_auth).status_code == 403
    assert client.get("/api/v1/me", headers=replacement_auth).status_code == 200

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM docplane.principals WHERE principal_id = %s", (principal_id,))
        assert cur.fetchone()[0] == "ACTIVE"
        cur.execute(
            "SELECT declared_by::text, version FROM model.generated_artifacts WHERE artifact_id = %s",
            (artifact.json()["artifact_id"],),
        )
        assert cur.fetchone() == (principal_id, 1)
        cur.execute(
            "SELECT event_type, metadata::text FROM docplane.events WHERE resource_id IN (%s, %s) AND event_type LIKE 'AUTH_PRINCIPAL_TOKEN_%%' ORDER BY event_seq",
            (replacement["token_id"], original_id),
        )
        events = cur.fetchall()
        assert [row[0] for row in events] == ["AUTH_PRINCIPAL_TOKEN_ISSUED", "AUTH_PRINCIPAL_TOKEN_REVOKED"]
        assert all(replacement["token"] not in row[1] for row in events)
