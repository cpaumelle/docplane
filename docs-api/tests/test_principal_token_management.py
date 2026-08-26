"""Governed credential rotation for an existing named principal."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

if not os.environ.get("DB_HOST"):
    pytest.skip("requires a PostgreSQL database (set DB_HOST etc.)", allow_module_level=True)

os.environ.setdefault("DOCPLANE_BOOTSTRAP_TOKEN", "token-management-bootstrap")

from fastapi.testclient import TestClient  # noqa: E402

from app.application import app  # noqa: E402
from app.db import get_conn  # noqa: E402

client = TestClient(app)
BOOTSTRAP = {"X-DocPlane-Bootstrap-Token": os.environ["DOCPLANE_BOOTSTRAP_TOKEN"]}


def _key() -> str:
    return f"token-management-{uuid.uuid4()}"


def _create_principal(kind: str = "AUTOMATION") -> dict[str, str]:
    response = client.post(
        "/api/v1/bootstrap/principals",
        json={"display_name": f"token-management-{uuid.uuid4()}", "principal_kind": kind},
        headers=BOOTSTRAP,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _issue(principal_id: str, *, key: str | None = None, description: str = "rotation candidate", expires_at=None):
    payload = {"description": description, "expires_at": expires_at}
    return client.post(
        f"/api/v1/bootstrap/principals/{principal_id}/tokens",
        json=payload,
        headers={**BOOTSTRAP, "Idempotency-Key": key or _key()},
    )


def test_existing_principal_safe_rotation_and_one_time_bearer_contract():
    created = _create_principal()
    principal_id = created["principal_id"]
    token_a = created["token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Custody is identity-bound and must survive credential rotation unchanged.
    source = client.post(
        "/api/v1/model/entities",
        json={
            "entity_kind": "DATABASE",
            "entity_key": f"token-source-{uuid.uuid4().hex}",
            "display_name": "Token rotation source",
            "attributes": {"engine": "postgres"},
        },
        headers={**headers_a, "Idempotency-Key": _key()},
    )
    assert source.status_code == 201, source.text
    artifact = client.post(
        "/api/v1/model/artifacts",
        json={
            "artifact_key": f"token-artifact-{uuid.uuid4().hex}",
            "generator_name": "token-test",
            "generator_version": "1",
            "source_entity_id": source.json()["entity_id"],
        },
        headers={**headers_a, "Idempotency-Key": _key()},
    )
    assert artifact.status_code == 201, artifact.text
    artifact_id = artifact.json()["artifact_id"]

    issue_key = _key()
    issued = _issue(principal_id, key=issue_key)
    assert issued.status_code == 201, issued.text
    body = issued.json()
    token_b = body["token"]
    token_b_id = body["token_id"]
    assert token_b.startswith("dp_")
    assert body["bearer_returned"] is True
    assert body["replayed"] is False
    assert body["principal_id"] == principal_id

    # Simulate a lost first response: retry recovers the committed token ID but
    # never turns the audit receipt into a bearer-recovery service.
    replay = _issue(principal_id, key=issue_key)
    assert replay.status_code == 201, replay.text
    assert replay.json()["token_id"] == token_b_id
    assert replay.json()["token"] is None
    assert replay.json()["bearer_returned"] is False
    assert replay.json()["replayed"] is True
    conflict = _issue(principal_id, key=issue_key, description="different intent")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    headers_b = {"Authorization": f"Bearer {token_b}"}
    assert client.get("/api/v1/me", headers=headers_a).json()["principal_id"] == principal_id
    assert client.get("/api/v1/me", headers=headers_b).json()["principal_id"] == principal_id

    listing = client.get(f"/api/v1/bootstrap/principals/{principal_id}/tokens", headers=BOOTSTRAP)
    assert listing.status_code == 200, listing.text
    listed = listing.json()
    assert listed["principal_status"] == "ACTIVE"
    assert len([item for item in listed["tokens"] if item["status"] == "ACTIVE"]) == 2
    serialized = json.dumps(listed)
    assert token_a not in serialized and token_b not in serialized
    assert "token_hash" not in serialized
    assert all(set(item) == {
        "token_id", "token_prefix", "description", "issued_at", "expires_at",
        "last_used_at", "revoked_at", "status",
    } for item in listed["tokens"])

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT token_hash FROM docplane.api_tokens WHERE token_id = %s", (token_b_id,))
        assert cur.fetchone()[0] == hashlib.sha256(token_b.encode()).hexdigest()
        cur.execute(
            "SELECT count(*) FROM docplane.events WHERE event_type = 'AUTH_PRINCIPAL_TOKEN_ISSUED' AND resource_id = %s",
            (token_b_id,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT metadata::text FROM docplane.events WHERE event_type = 'AUTH_PRINCIPAL_TOKEN_ISSUED' AND resource_id = %s",
            (token_b_id,),
        )
        event_metadata = cur.fetchone()[0]
        assert token_b not in event_metadata
        cur.execute("SELECT response::text FROM docplane.mutation_receipts")
        assert all(token_b not in row[0] for row in cur.fetchall())
        cur.execute("SELECT declared_by::text, version FROM model.generated_artifacts WHERE artifact_id = %s", (artifact_id,))
        assert cur.fetchone() == (principal_id, 1)

    # Revoke A only: B and the principal remain active.
    token_a_id = next(item["token_id"] for item in listed["tokens"] if item["token_id"] != token_b_id)
    revoke_key = _key()
    revoked_a = client.post(
        f"/api/v1/bootstrap/principals/{principal_id}/tokens/{token_a_id}/revoke",
        headers={**BOOTSTRAP, "Idempotency-Key": revoke_key},
    )
    assert revoked_a.status_code == 200, revoked_a.text
    assert revoked_a.json()["status"] == "REVOKED"
    assert revoked_a.json()["replayed"] is False
    replayed_revoke = client.post(
        f"/api/v1/bootstrap/principals/{principal_id}/tokens/{token_a_id}/revoke",
        headers={**BOOTSTRAP, "Idempotency-Key": revoke_key},
    )
    assert replayed_revoke.status_code == 200
    assert replayed_revoke.json()["replayed"] is True
    conflicting_revoke = client.post(
        f"/api/v1/bootstrap/principals/{principal_id}/tokens/{token_b_id}/revoke",
        headers={**BOOTSTRAP, "Idempotency-Key": revoke_key},
    )
    assert conflicting_revoke.status_code == 409
    assert client.get("/api/v1/me", headers=headers_a).status_code == 403
    assert client.get("/api/v1/me", headers=headers_b).status_code == 200

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM docplane.principals WHERE principal_id = %s", (principal_id,))
        assert cur.fetchone()[0] == "ACTIVE"
        cur.execute(
            "SELECT count(*) FROM docplane.events WHERE event_type = 'AUTH_PRINCIPAL_TOKEN_REVOKED' AND resource_id = %s",
            (token_a_id,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT declared_by::text, version FROM model.generated_artifacts WHERE artifact_id = %s", (artifact_id,))
        assert cur.fetchone() == (principal_id, 1)

    revoked_b = client.post(
        f"/api/v1/bootstrap/principals/{principal_id}/tokens/{token_b_id}/revoke",
        headers={**BOOTSTRAP, "Idempotency-Key": _key()},
    )
    assert revoked_b.status_code == 200
    assert client.get("/api/v1/me", headers=headers_b).status_code == 403
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM docplane.principals WHERE principal_id = %s", (principal_id,))
        assert cur.fetchone()[0] == "ACTIVE"


def test_bootstrap_authority_principal_state_expiry_and_token_ownership():
    created = _create_principal()
    principal_id = created["principal_id"]
    contributor = {"Authorization": f"Bearer {created['token']}"}

    denied = client.post(
        f"/api/v1/bootstrap/principals/{principal_id}/tokens",
        json={"description": "not operator-authorized"},
        headers={**contributor, "Idempotency-Key": _key()},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "BOOTSTRAP_TOKEN_INVALID"
    denied_list = client.get(f"/api/v1/bootstrap/principals/{principal_id}/tokens", headers=contributor)
    assert denied_list.status_code == 403

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    finite = _issue(principal_id, expires_at=future.isoformat())
    assert finite.status_code == 201, finite.text
    assert finite.json()["expires_at"] is not None
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {finite.json()['token']}"}).status_code == 200

    past = _issue(principal_id, expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    assert past.status_code == 422
    naive = _issue(principal_id, expires_at="2030-01-01T00:00:00")
    assert naive.status_code == 422

    suspended = _create_principal()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE docplane.principals SET status = 'SUSPENDED' WHERE principal_id = %s", (suspended["principal_id"],))
        conn.commit()
    refused = _issue(suspended["principal_id"])
    assert refused.status_code == 409
    assert refused.json()["detail"] == {"code": "PRINCIPAL_NOT_ACTIVE", "status": "SUSPENDED"}

    revoked = _create_principal()
    whole = client.post(f"/api/v1/bootstrap/principals/{revoked['principal_id']}/revoke", headers=BOOTSTRAP)
    assert whole.status_code == 200
    refused = _issue(revoked["principal_id"])
    assert refused.status_code == 409
    assert refused.json()["detail"] == {"code": "PRINCIPAL_NOT_ACTIVE", "status": "REVOKED"}

    other = _create_principal()
    wrong_owner = client.post(
        f"/api/v1/bootstrap/principals/{other['principal_id']}/tokens/{finite.json()['token_id']}/revoke",
        headers={**BOOTSTRAP, "Idempotency-Key": _key()},
    )
    assert wrong_owner.status_code == 404
    assert wrong_owner.json()["detail"]["code"] == "PRINCIPAL_TOKEN_NOT_FOUND"
