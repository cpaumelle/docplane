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
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

if not os.environ.get("DB_HOST"):
    pytest.skip("requires a PostgreSQL database (set DB_HOST etc.)", allow_module_level=True)

os.environ.setdefault("DOCPLANE_EVENT_CURSOR_SECRET", "e2e-cursor-secret-0123456789abcdef")
os.environ.setdefault("DOCPLANE_BOOTSTRAP_TOKEN", "e2e-bootstrap")

from fastapi.testclient import TestClient  # noqa: E402

from app.application import app  # noqa: E402
from app.db import get_conn  # noqa: E402

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
            (path, f"E2E {RUN}", f"E2E/{RUN}", f"# E2E {RUN}\n", revision, workspace_id, knowledge_class),
        )
        resource_id = cur.fetchone()[0]
        conn.commit()
    return {"resource_id": resource_id, "revision": revision, "path": path}


def _work_workspace_id() -> str:
    body = client.get("/api/v1/workspaces", headers=AGENT).json()
    return next(item["workspace_id"] for item in body["workspaces"] if item["workspace_key"] == "work")


def _key() -> str:
    return f"e2e-{uuid.uuid4()}"


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

    # A TEST observation carrying an unrelated fingerprint must not redefine freshness.
    unrelated = {"observations": [{"subject_entity_id": entity_id, "observation_kind": "TEST", "source_fingerprint": "1234abcd", "observed_at": (now + timedelta(seconds=2)).isoformat()}]}
    client.post("/api/v1/observations", json=unrelated, headers={**AGENT, "Idempotency-Key": _key()})
    assert client.get(f"/api/v1/model/artifacts/{artifact_id}/status", headers=AGENT).json()["freshness"]["source_fingerprint"] == "ffff0000"

    # A late-arriving OLDER observation never regresses the projection.
    late = {"observations": [{"subject_entity_id": entity_id, "observation_kind": "FRESHNESS_CHECK", "source_fingerprint": "ab12cd34", "observed_at": (now - timedelta(hours=1)).isoformat()}]}
    client.post("/api/v1/observations", json=late, headers={**AGENT, "Idempotency-Key": _key()})
    current = client.get(f"/api/v1/model/entities/{entity_id}/status", headers=AGENT).json()["current_status"]
    freshness_rows = [row for row in current if row["observation_kind"] == "FRESHNESS_CHECK"]
    assert freshness_rows[0]["source_fingerprint"] == "ffff0000"

    future = {"observations": [{"subject_entity_id": entity_id, "observation_kind": "TEST", "observed_at": (now + timedelta(hours=2)).isoformat()}]}
    rejected = client.post("/api/v1/observations", json=future, headers={**AGENT, "Idempotency-Key": _key()})
    assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "OBSERVATION_BATCH_REJECTED"
    secret = {"observations": [{"subject_entity_id": entity_id, "observation_kind": "TEST", "payload": {"api_key": "not-a-placeholder"}}]}
    rejected = client.post("/api/v1/observations", json=secret, headers={**AGENT, "Idempotency-Key": _key()})
    assert rejected.status_code == 422


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
    assert retired.status_code == 200 and retired.json()["status"] == "RETIRED"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT provenance FROM docs.pages WHERE resource_id = %s", (page["resource_id"],))
        assert cur.fetchone()[0] == "AUTHORED"  # retirement releases the page honestly


def test_a_retired_artifact_key_admits_a_successor_declaration():
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

    retired = client.post(f"/api/v1/model/artifacts/{first.json()['artifact_id']}/retire", json={"expected_version": 1}, headers={**AUTOMATION, "Idempotency-Key": _key()})
    assert retired.status_code == 200, retired.text

    # The successor takes the key back, gets a NEW artifact identity, and
    # re-arms provenance on its targets.
    successor = client.post("/api/v1/model/artifacts", json={**body, "generator_version": "2"}, headers={**AUTOMATION, "Idempotency-Key": _key()})
    assert successor.status_code == 201, successor.text
    assert successor.json()["artifact_id"] != first.json()["artifact_id"]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT provenance FROM docs.pages WHERE resource_id = %s", (page["resource_id"],))
        assert cur.fetchone()[0] == "GENERATED"
        cur.execute("SELECT count(*) FROM model.generated_artifacts WHERE artifact_key = %s", (artifact_key,))
        assert cur.fetchone()[0] == 2  # retired generation stays as audit history


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