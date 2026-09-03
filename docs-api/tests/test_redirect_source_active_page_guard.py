"""PostgreSQL-backed cover for the redirect / active-page publication wedge.

A redirect source may never also be an active page path: mkdocs-redirects writes
its stub exactly where the live page renders, so the page vanishes from the built
site while every authored surface still reports success. generator.validate_redirects
enforced this only at DEPLOY, and the authored commit is a separate transaction from
the whole-corpus deploy — so an operation that produced the collision committed
authored state and then wedged every subsequent deploy (2026-09-03 incident, root
cause RESTORE_PAGE re-activating a page whose path was still a redirect source).

These tests prove the invariant is now enforced at CHANGE EVALUATION, before the
authored write:
  * RESTORE_PAGE over a redirect is rejected before commit (operation-local guard);
  * MOVE_PAGE / CREATE_PAGE onto a redirect source are rejected by the corpus-wide
    backstop, so no operation type can bypass the invariant;
  * a rejected change leaves authored corpus state (pages, redirects) unchanged;
  * the sanctioned remediation — remove the redirect, then restore — validates;
  * a redirect from a non-active (archived) path still validates.

They run against a real migrated database (CI provides one; locally set
DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASS). Rejection is asserted at both validate and
publish, and publish rejects before deploy_current_state, so nothing here needs mkdocs.
"""
from __future__ import annotations

import os
import uuid

import pytest

if not os.environ.get("DB_HOST"):
    pytest.skip("requires a PostgreSQL database (set DB_HOST etc.)", allow_module_level=True)

os.environ.setdefault("DOCPLANE_EVENT_CURSOR_SECRET", "guard-cursor-secret-0123456789abcdef")
os.environ.setdefault("DOCPLANE_BOOTSTRAP_TOKEN", "guard-bootstrap")

import hashlib  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.application import app  # noqa: E402
from app.db import get_conn  # noqa: E402

client = TestClient(app)
RUN = uuid.uuid4().hex[:8]


def _mint_contributor() -> dict[str, str]:
    token = f"dp_guard_{uuid.uuid4().hex}"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO docplane.principals (display_name, principal_kind) VALUES (%s, 'AGENT') RETURNING principal_id::text",
            (f"guard-agent-{RUN}",),
        )
        principal_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO docplane.api_tokens (principal_id, token_hash, token_prefix, description) VALUES (%s, %s, %s, 'guard')",
            (principal_id, hashlib.sha256(token.encode()).hexdigest(), token[:8]),
        )
        conn.commit()
    return {"Authorization": f"Bearer {token}"}


AUTH = _mint_contributor()


def _key() -> str:
    return f"guard-{uuid.uuid4()}"


def _seed_page(path: str, *, status: str = "active") -> dict[str, str]:
    publication_state = "PUBLISHED" if status == "active" else "ARCHIVED"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT workspace_id FROM docplane.workspaces WHERE workspace_key = 'reference'")
        workspace_id = cur.fetchone()[0]
        revision = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO docs.pages
                (path, title, nav_path, content, revision, workspace_id, status,
                 publication_state, knowledge_class, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'REFERENCE', 'guard-test')
            RETURNING resource_id::text
            """,
            (path, f"Guard {path}", f"Reference/Guard/{path.rsplit('/', 1)[-1].removesuffix('.md')}",
             f"# Guard {path}\n\nbody\n", revision, workspace_id, status, publication_state),
        )
        resource_id = cur.fetchone()[0]
        conn.commit()
    return {"resource_id": resource_id, "revision": revision, "path": path}


def _seed_redirect(from_path: str, to_path: str) -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO docs.redirects (from_path, to_path, revision, version, updated_by)
            VALUES (%s, %s, %s, 1, 'guard-test')
            ON CONFLICT (from_path) DO UPDATE SET to_path = EXCLUDED.to_path
            """,
            (from_path, to_path, str(uuid.uuid4())),
        )
        conn.commit()


def _create_change() -> str:
    response = client.post(
        "/api/v1/changes",
        json={"workspace_key": "reference", "title": f"guard {RUN}", "purpose": "redirect/active-page guard test"},
        headers={**AUTH, "Idempotency-Key": _key()},
    )
    assert response.status_code == 201, response.text
    return response.json()["change_id"]


def _add_op(change_id: str, operation: dict) -> None:
    response = client.post(
        f"/api/v1/changes/{change_id}/operations",
        json=operation,
        headers={**AUTH, "Idempotency-Key": _key()},
    )
    assert response.status_code == 201, response.text


def _validate(change_id: str) -> dict:
    response = client.post(f"/api/v1/changes/{change_id}/validate", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()["validation_summary"]


def _publish(change_id: str):
    return client.post(f"/api/v1/changes/{change_id}/publish", headers={**AUTH, "Idempotency-Key": _key()})


def _error_codes(summary: dict) -> set[str]:
    return {error["code"] for error in summary["errors"]}


def _page_status(resource_id: str) -> str:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM docs.pages WHERE resource_id = %s", (resource_id,))
        return cur.fetchone()[0]


def _page_path(resource_id: str) -> str:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT path FROM docs.pages WHERE resource_id = %s", (resource_id,))
        return cur.fetchone()[0]


def _redirect_exists(from_path: str) -> bool:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM docs.redirects WHERE from_path = %s", (from_path,))
        return cur.fetchone() is not None


# --------------------------------------------------------------------------
# RESTORE_PAGE — the exact incident shape — is rejected before commit
# --------------------------------------------------------------------------

def test_restoring_a_page_over_an_existing_redirect_is_rejected_before_commit():
    target = _seed_page(f"reference/guard-restore-target-{RUN}.md")
    archived = _seed_page(f"reference/guard-restore-{RUN}.md", status="archived")
    _seed_redirect(archived["path"], target["path"])

    change_id = _create_change()
    _add_op(change_id, {
        "operation_type": "RESTORE_PAGE",
        "page_resource_id": archived["resource_id"],
        "expected_revision": archived["revision"],
        "payload": {},
    })

    summary = _validate(change_id)
    assert summary["passed"] is False
    assert "RESTORE_TARGET_HAS_REDIRECT" in _error_codes(summary)
    # the operation receipt carries an actionable remediation, not just a code
    restore_op = next(op for op in client.post(
        f"/api/v1/changes/{change_id}/validate", headers=AUTH).json()["operations"]
        if op["operation_type"] == "RESTORE_PAGE")
    assert any("REMOVE_REDIRECT" in (err.get("remedy") or "") for err in restore_op["validation_result"]["errors"])

    # publish also fails closed, before deploy
    rejected = _publish(change_id)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "CHANGE_VALIDATION_FAILED"

    # authored corpus is untouched: page still archived, redirect still present
    assert _page_status(archived["resource_id"]) == "archived"
    assert _redirect_exists(archived["path"]) is True


# --------------------------------------------------------------------------
# The corpus-wide backstop covers MOVE_PAGE and CREATE_PAGE (no per-op guard)
# --------------------------------------------------------------------------

def test_moving_a_page_onto_a_redirect_source_is_rejected_by_the_backstop():
    dest_target = _seed_page(f"reference/guard-move-target-{RUN}.md")
    mover = _seed_page(f"reference/guard-move-src-{RUN}.md")
    collision_path = f"reference/guard-move-onto-{RUN}.md"
    _seed_redirect(collision_path, dest_target["path"])  # collision_path is a redirect source

    change_id = _create_change()
    _add_op(change_id, {
        "operation_type": "MOVE_PAGE",
        "page_resource_id": mover["resource_id"],
        "expected_revision": mover["revision"],
        "payload": {"to_path": collision_path},
    })

    summary = _validate(change_id)
    assert summary["passed"] is False
    assert "REDIRECT_SOURCE_IS_ACTIVE_PAGE" in _error_codes(summary)

    rejected = _publish(change_id)
    assert rejected.status_code == 409, rejected.text

    # nothing moved, redirect intact
    assert _page_path(mover["resource_id"]) == mover["path"]
    assert _redirect_exists(collision_path) is True


def test_creating_a_page_on_a_redirect_source_is_rejected_by_the_backstop():
    create_target = _seed_page(f"reference/guard-create-target-{RUN}.md")
    collision_path = f"reference/guard-create-onto-{RUN}.md"
    _seed_redirect(collision_path, create_target["path"])

    change_id = _create_change()
    _add_op(change_id, {
        "operation_type": "CREATE_PAGE",
        "payload": {
            "path": collision_path,
            "title": "Guard create collision",
            "nav_path": "Reference/Guard/Create Collision",
            "content": "# collision\n",
        },
    })

    summary = _validate(change_id)
    assert summary["passed"] is False
    assert "REDIRECT_SOURCE_IS_ACTIVE_PAGE" in _error_codes(summary)

    rejected = _publish(change_id)
    assert rejected.status_code == 409, rejected.text

    # the collision page was never created
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM docs.pages WHERE path = %s", (collision_path,))
        assert cur.fetchone()[0] == 0


# --------------------------------------------------------------------------
# The sanctioned remediation and legitimate shapes still validate
# --------------------------------------------------------------------------

def test_removing_the_redirect_then_restoring_the_page_is_valid():
    target = _seed_page(f"reference/guard-heal-target-{RUN}.md")
    archived = _seed_page(f"reference/guard-heal-{RUN}.md", status="archived")
    _seed_redirect(archived["path"], target["path"])

    change_id = _create_change()
    _add_op(change_id, {
        "operation_type": "REMOVE_REDIRECT",
        "payload": {"from_path": archived["path"]},
        "sequence": 0,
    })
    _add_op(change_id, {
        "operation_type": "RESTORE_PAGE",
        "page_resource_id": archived["resource_id"],
        "expected_revision": archived["revision"],
        "payload": {},
        "sequence": 1,
    })

    summary = _validate(change_id)
    assert summary["passed"] is True, summary["errors"]


def test_a_redirect_from_an_archived_page_path_still_validates():
    """Guard the guard: only an ACTIVE page path is the conflict. An archived page
    with a redirect at its path is a legitimate, publishable shape."""
    target = _seed_page(f"reference/guard-archived-target-{RUN}.md")
    archived = _seed_page(f"reference/guard-archived-src-{RUN}.md", status="archived")

    change_id = _create_change()
    _add_op(change_id, {
        "operation_type": "ADD_REDIRECT",
        "payload": {"from_path": archived["path"], "to_path": target["path"]},
    })

    summary = _validate(change_id)
    assert summary["passed"] is True, summary["errors"]
    assert "REDIRECT_SOURCE_IS_ACTIVE_PAGE" not in _error_codes(summary)
