from __future__ import annotations

import hashlib
import os
import uuid

import pytest

if not os.environ.get("DB_HOST"):
    pytest.skip("requires a PostgreSQL database (set DB_HOST etc.)", allow_module_level=True)

os.environ.setdefault("DOCPLANE_EVENT_CURSOR_SECRET", "catalogues-e2e-cursor-secret-0123456789abcdef")
os.environ.setdefault("DOCPLANE_BOOTSTRAP_TOKEN", "catalogues-e2e-bootstrap")

from fastapi.testclient import TestClient  # noqa: E402

from app.application import app  # noqa: E402
from app.db import get_conn  # noqa: E402

RUN = uuid.uuid4().hex[:8]
client = TestClient(app)


def _mint_contributor() -> tuple[str, dict[str, str]]:
    token = f"dp_catalogues_e2e_{uuid.uuid4().hex}"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO docplane.principals (display_name, principal_kind) VALUES (%s, 'AGENT') RETURNING principal_id::text",
            (f"catalogues-e2e-{RUN}",),
        )
        principal_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO docplane.api_tokens (principal_id, token_hash, token_prefix, description) VALUES (%s, %s, %s, 'catalogues e2e')",
            (principal_id, hashlib.sha256(token.encode()).hexdigest(), token[:12]),
        )
        conn.commit()
    return principal_id, {"Authorization": f"Bearer {token}"}


def _seed_entity(principal_id: str) -> str:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO model.entities
                (entity_kind, entity_key, display_name, attributes, created_by, idempotency_key)
            VALUES ('SERVICE', %s, %s, '{}'::jsonb, %s, %s)
            RETURNING entity_id::text
            """,
            (f"catalogues-e2e-{RUN}", f"Catalogues E2E {RUN}", principal_id, f"entity-{RUN}"),
        )
        entity_id = cur.fetchone()[0]
        conn.commit()
    return entity_id


def _seed_page(path: str, *, status: str = "active") -> str:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT workspace_id FROM docplane.workspaces WHERE workspace_key = 'reference'")
        workspace_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO docs.pages
                (path, title, nav_path, content, workspace_id, publication_state, knowledge_class, status, updated_by)
            VALUES (%s, %s, %s, %s, %s, 'PUBLISHED', 'REFERENCE', %s, 'catalogues-e2e')
            RETURNING resource_id::text
            """,
            (path, f"Catalogues E2E {RUN}", f"Catalogues/{RUN}", f"# Catalogues E2E {RUN}\n", workspace_id, status),
        )
        resource_id = cur.fetchone()[0]
        conn.commit()
    return resource_id


def _event_count(entity_id: str) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM docplane.events WHERE event_type = 'MODEL_ENTITY_PAGE_CATALOGUES_RECONCILED' AND resource_id = %s",
            (entity_id,),
        )
        return int(cur.fetchone()[0])


def test_catalogues_exact_set_transaction_replay_and_relation_isolation():
    principal_id, headers = _mint_contributor()
    entity_id = _seed_entity(principal_id)
    page_one = _seed_page(f"reference/catalogues-e2e-{RUN}-one.md")
    page_two = _seed_page(f"reference/catalogues-e2e-{RUN}-two.md")
    archived = _seed_page(f"reference/catalogues-e2e-{RUN}-archived.md", status="archived")

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO model.entity_page_links (entity_id, relation, page_resource_id, created_by) VALUES (%s, 'DESCRIBES', %s, %s)",
            (entity_id, page_one, principal_id),
        )
        conn.commit()

    first_key = f"catalogues-e2e-first-{RUN}"
    first = client.put(
        f"/api/v1/model/entities/{entity_id}/page-links/catalogues",
        json={"page_resource_ids": [page_one, page_two]},
        headers={**headers, "Idempotency-Key": first_key},
    )
    assert first.status_code == 200, first.text
    assert first.json()["changed"] is True
    assert first.json()["entity_status"] == "ACTIVE"
    assert set(first.json()["added"]) == {page_one, page_two}
    assert first.json()["removed"] == []
    assert _event_count(entity_id) == 1

    replay = client.put(
        f"/api/v1/model/entities/{entity_id}/page-links/catalogues",
        json={"page_resource_ids": [page_two, page_one]},
        headers={**headers, "Idempotency-Key": first_key},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert _event_count(entity_id) == 1

    misuse = client.put(
        f"/api/v1/model/entities/{entity_id}/page-links/catalogues",
        json={"page_resource_ids": [page_two]},
        headers={**headers, "Idempotency-Key": first_key},
    )
    assert misuse.status_code == 409
    assert misuse.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    second = client.put(
        f"/api/v1/model/entities/{entity_id}/page-links/catalogues",
        json={"page_resource_ids": [page_two]},
        headers={**headers, "Idempotency-Key": f"catalogues-e2e-second-{RUN}"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["added"] == []
    assert second.json()["removed"] == [page_one]
    assert second.json()["continuing"] == [page_two]
    assert second.json()["changed"] is True
    assert _event_count(entity_id) == 2

    no_op = client.put(
        f"/api/v1/model/entities/{entity_id}/page-links/catalogues",
        json={"page_resource_ids": [page_two]},
        headers={**headers, "Idempotency-Key": f"catalogues-e2e-noop-{RUN}"},
    )
    assert no_op.status_code == 200, no_op.text
    assert no_op.json()["changed"] is False
    assert _event_count(entity_id) == 2

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT relation, page_resource_id::text FROM model.entity_page_links WHERE entity_id = %s ORDER BY relation, page_resource_id",
            (entity_id,),
        )
        rows = cur.fetchall()
    assert ("DESCRIBES", page_one) in rows
    assert ("CATALOGUES", page_two) in rows
    assert ("CATALOGUES", page_one) not in rows

    rejected = client.put(
        f"/api/v1/model/entities/{entity_id}/page-links/catalogues",
        json={"page_resource_ids": [archived]},
        headers={**headers, "Idempotency-Key": f"catalogues-e2e-archived-{RUN}"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "CATALOGUES_PAGE_NOT_ACTIVE"

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE model.entities SET status = 'RETIRED', retired_at = now(), version = version + 1 WHERE entity_id = %s",
            (entity_id,),
        )
        conn.commit()

    retired_add = client.put(
        f"/api/v1/model/entities/{entity_id}/page-links/catalogues",
        json={"page_resource_ids": [page_two]},
        headers={**headers, "Idempotency-Key": f"catalogues-e2e-retired-add-{RUN}"},
    )
    assert retired_add.status_code == 409
    assert retired_add.json()["detail"]["code"] == "MODEL_ENTITY_NOT_ACTIVE"

    retired_cleanup = client.put(
        f"/api/v1/model/entities/{entity_id}/page-links/catalogues",
        json={"page_resource_ids": []},
        headers={**headers, "Idempotency-Key": f"catalogues-e2e-retired-cleanup-{RUN}"},
    )
    assert retired_cleanup.status_code == 200, retired_cleanup.text
    assert retired_cleanup.json()["entity_status"] == "RETIRED"
    assert retired_cleanup.json()["removed"] == [page_two]
    assert retired_cleanup.json()["changed"] is True

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT relation, page_resource_id::text FROM model.entity_page_links WHERE entity_id = %s ORDER BY relation, page_resource_id",
            (entity_id,),
        )
        rows = cur.fetchall()
    assert ("DESCRIBES", page_one) in rows
    assert all(relation != "CATALOGUES" for relation, _ in rows)
