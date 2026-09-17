"""PostgreSQL-backed regression for capture d79bb0ce.

The companion module (test_initiative_dependencies.py) inspects the SQL a fake
cursor received. That is useful but it is not the boundary that broke: the
defect was never a Python string-construction bug. PostgreSQL rejected the
statement at PARSE time, because `ON CONFLICT ... DO UPDATE` without an explicit
conflict target is not valid SQL. Only a real server can refuse that, so only a
real server can prove it is fixed.

These run against a migrated database, the same one CI stands up for the other
e2e modules. Every assertion here would have failed on the shipped code, for the
exact reason production failed: HTTP 500 with a bare 'Internal Server Error'
body and no dependency row written.
"""
from __future__ import annotations

import hashlib
import os
import uuid

import pytest

if not os.environ.get("DB_HOST"):
    pytest.skip("requires a PostgreSQL database (set DB_HOST etc.)", allow_module_level=True)

os.environ.setdefault("DOCPLANE_EVENT_CURSOR_SECRET", "deps-e2e-cursor-secret-0123456789abcdef")
os.environ.setdefault("DOCPLANE_BOOTSTRAP_TOKEN", "deps-e2e-bootstrap")

from fastapi.testclient import TestClient  # noqa: E402

from app.application import app  # noqa: E402
from app.db import get_conn  # noqa: E402

RUN = uuid.uuid4().hex[:8]
client = TestClient(app)


def _mint_contributor() -> dict[str, str]:
    token = f"dp_deps_e2e_{uuid.uuid4().hex}"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO docplane.principals (display_name, principal_kind) VALUES (%s, 'AGENT') RETURNING principal_id::text",
            (f"deps-e2e-{RUN}",),
        )
        principal_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO docplane.api_tokens (principal_id, token_hash, token_prefix, description) VALUES (%s, %s, %s, 'deps e2e')",
            (principal_id, hashlib.sha256(token.encode()).hexdigest(), token[:8]),
        )
        conn.commit()
    return {"Authorization": f"Bearer {token}"}


AGENT = _mint_contributor()


def _work_workspace_id() -> str:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT workspace_id::text FROM docplane.workspaces WHERE workspace_key = 'work'")
        return cur.fetchone()[0]


def _key() -> str:
    return str(uuid.uuid4())


def _initiative(label: str) -> str:
    response = client.post(
        "/api/v1/initiatives",
        json={
            "initiative_key": f"deps-e2e-{label}-{RUN}",
            "workspace_id": _work_workspace_id(),
            "title": f"Dependency e2e {label}",
            "objective": "prove the dependency upsert reaches PostgreSQL and commits",
            "work_state": "BACKLOG",
        },
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert response.status_code == 201, response.text
    return response.json()["initiative_id"]


def _rows(initiative_id: str, depends_on: str) -> list[tuple[str, str]]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT depends_on_initiative_id::text, dependency_kind
              FROM work.initiative_dependencies
             WHERE initiative_id = %s AND depends_on_initiative_id = %s
            """,
            (initiative_id, depends_on),
        )
        return cur.fetchall()


def test_recording_a_dependency_reaches_postgres_and_commits():
    """The production failure, end to end.

    On the shipped code this returned 500 and wrote nothing, because the
    statement never parsed. A fake cursor cannot tell the difference.
    """
    a, b = _initiative("from"), _initiative("to")

    created = client.post(
        f"/api/v1/initiatives/{a}/dependencies",
        json={"depends_on_initiative_id": b, "dependency_kind": "REQUIRES"},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert created.status_code == 201, created.text
    assert _rows(a, b) == [(b, "REQUIRES")]

    read = client.get(f"/api/v1/initiatives/{a}", headers=AGENT)
    assert read.status_code == 200, read.text
    assert {"initiative_id": b, "dependency_kind": "REQUIRES"} in read.json()["dependencies"]


def test_re_recording_the_same_pair_updates_the_kind_in_place():
    """The upsert branch, which is what the missing conflict target was for.

    Re-POSTing a pair must change the kind rather than duplicate the row or
    raise a unique violation on the primary key.
    """
    a, b = _initiative("upsert-from"), _initiative("upsert-to")

    first = client.post(
        f"/api/v1/initiatives/{a}/dependencies",
        json={"depends_on_initiative_id": b, "dependency_kind": "REQUIRES"},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert first.status_code == 201, first.text

    second = client.post(
        f"/api/v1/initiatives/{a}/dependencies",
        json={"depends_on_initiative_id": b, "dependency_kind": "RELATED"},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert second.status_code == 201, second.text

    assert _rows(a, b) == [(b, "RELATED")], "expected exactly one row, kind updated in place"

    dependencies = client.get(f"/api/v1/initiatives/{a}", headers=AGENT).json()["dependencies"]
    matching = [d for d in dependencies if d["initiative_id"] == b]
    assert matching == [{"initiative_id": b, "dependency_kind": "RELATED"}]


@pytest.mark.parametrize("kind", ["REQUIRES", "BLOCKED_BY", "RELATED"])
def test_every_declared_kind_is_accepted(kind):
    """All three enum values the schema declares. The reporter used RELATED;
    a fix that only worked for the default would still look green."""
    a, b = _initiative(f"kind-{kind.lower()}-from"), _initiative(f"kind-{kind.lower()}-to")
    response = client.post(
        f"/api/v1/initiatives/{a}/dependencies",
        json={"depends_on_initiative_id": b, "dependency_kind": kind},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert response.status_code == 201, response.text
    assert _rows(a, b) == [(b, kind)]


def test_self_dependency_is_refused_with_a_structured_error():
    """422 with an actionable body, not the bare 500 string this endpoint used
    to return for everything."""
    a = _initiative("self")
    response = client.post(
        f"/api/v1/initiatives/{a}/dependencies",
        json={"depends_on_initiative_id": a, "dependency_kind": "RELATED"},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INITIATIVE_DEPENDENCY_SELF"
    assert _rows(a, a) == []


def test_unknown_initiative_is_a_404_not_a_500():
    """The capture asked whether the 500 was a validation path returning the
    wrong status. It was not -- but an unresolvable target should still answer
    with identity, not a crash."""
    a = _initiative("missing-target")
    response = client.post(
        f"/api/v1/initiatives/{a}/dependencies",
        json={"depends_on_initiative_id": str(uuid.uuid4()), "dependency_kind": "RELATED"},
        headers={**AGENT, "Idempotency-Key": _key()},
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "INITIATIVE_NOT_FOUND"
