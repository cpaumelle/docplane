"""GET /api/v1/redirects: the read-only view of docs.redirects.

Redirects were readable only by SQL, so tooling that had to know which redirects target a
page (before an archive, which REDIRECT_TARGET_MISSING refuses, or a move) either read the
table directly or dry-ran an ARCHIVE_PAGE change to discover them. These tests prove the read
surface is faithful, bounded, authenticated like every contributor read, and never writes.

Route-shape checks run anywhere; the rest need a migrated database (CI provides one; locally
set DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASS).
"""
from __future__ import annotations

import hashlib
import os
import uuid

import pytest

os.environ.setdefault("DOCPLANE_EVENT_CURSOR_SECRET", "redirects-cursor-secret-0123456789abcdef")
os.environ.setdefault("DOCPLANE_BOOTSTRAP_TOKEN", "redirects-bootstrap")

from fastapi.testclient import TestClient  # noqa: E402

from app import agent_contract_api  # noqa: E402
from app.application import app  # noqa: E402

client = TestClient(app)
RUN = uuid.uuid4().hex[:8]
needs_db = pytest.mark.skipif(not os.environ.get("DB_HOST"), reason="requires a PostgreSQL database (set DB_HOST etc.)")


def test_the_redirects_surface_is_get_only():
    methods = {m for r in app.routes if getattr(r, "path", None) == "/api/v1/redirects" for m in r.methods}
    assert methods == {"GET"} or methods == {"GET", "HEAD"}


def test_discovery_advertises_the_redirects_surface():
    assert agent_contract_api.discovery()["surfaces"]["redirects"] == "/api/v1/redirects"


def test_missing_bearer_is_refused_like_every_contributor_read():
    r = client.get("/api/v1/redirects")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "AUTH_REQUIRED"


def _auth() -> dict[str, str]:
    from app.db import get_conn
    token = f"dp_redirects_{uuid.uuid4().hex}"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO docplane.principals (display_name, principal_kind) VALUES (%s, 'AGENT') "
                    "RETURNING principal_id::text", (f"redirects-agent-{RUN}",))
        pid = cur.fetchone()[0]
        cur.execute("INSERT INTO docplane.api_tokens (principal_id, token_hash, token_prefix, description) "
                    "VALUES (%s, %s, %s, 'redirects')", (pid, hashlib.sha256(token.encode()).hexdigest(), token[:8]))
        conn.commit()
    return {"Authorization": f"Bearer {token}"}


def _seed(rows: list[tuple[str, str]]) -> None:
    from app.db import get_conn
    with get_conn() as conn:
        cur = conn.cursor()
        for src, dst in rows:
            cur.execute("INSERT INTO docs.redirects (from_path, to_path, updated_by) VALUES (%s, %s, 'redirects-test')",
                        (src, dst))
        conn.commit()


def _table() -> list[tuple]:
    from app.db import get_conn
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT from_path, to_path, revision, version, updated_at FROM docs.redirects ORDER BY from_path")
        return cur.fetchall()


T1, T2 = f"reference/redir-{RUN}-target-one.md", f"reference/redir-{RUN}-target-two.md"
SEED = [(f"reference/redir-{RUN}-a.md", T1), (f"reference/redir-{RUN}-b.md", T1), (f"reference/redir-{RUN}-c.md", T2)]


@pytest.fixture(scope="module")
def auth():
    if not os.environ.get("DB_HOST"):
        pytest.skip("requires a PostgreSQL database")
    headers = _auth()
    _seed(SEED)
    yield headers
    # The seeded targets are not pages. Every change validation checks every redirect's target,
    # so leaving these rows would fail unrelated suites sharing the database: remove exactly them.
    from app.db import get_conn
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM docs.redirects WHERE from_path = ANY(%s)", ([s for s, _ in SEED],))
        conn.commit()


@needs_db
def test_invalid_bearer_is_refused(auth):
    r = client.get("/api/v1/redirects", headers={"Authorization": "Bearer dp_not_a_real_token"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "AUTH_TOKEN_INVALID"


@needs_db
def test_authenticated_read_returns_every_redirect_with_exact_fidelity(auth):
    body = client.get("/api/v1/redirects?limit=2000", headers=auth).json()
    assert body["total"] == len(_table()) == body["count"]
    got = {r["from_path"]: r for r in body["redirects"]}
    for src, dst in SEED:
        assert got[src]["to_path"] == dst
    row = {r[0]: r for r in _table()}
    for src, _ in SEED:
        assert (got[src]["revision"], got[src]["version"]) == (row[src][2], row[src][3])
        assert got[src]["updated_at"] == row[src][4].isoformat()
    assert set(body["redirects"][0]) == {"from_path", "to_path", "revision", "version", "updated_at"}
    assert [r["from_path"] for r in body["redirects"]] == sorted(r["from_path"] for r in body["redirects"])


@needs_db
def test_to_path_filter_returns_exactly_the_redirects_targeting_that_page(auth):
    body = client.get(f"/api/v1/redirects?to_path={T1}", headers=auth).json()
    assert sorted(r["from_path"] for r in body["redirects"]) == sorted(s for s, d in SEED if d == T1)
    assert body["total"] == 2 and all(r["to_path"] == T1 for r in body["redirects"])


@needs_db
def test_a_valid_path_with_no_redirects_is_an_empty_set(auth):
    body = client.get(f"/api/v1/redirects?to_path=reference/redir-{RUN}-nothing.md", headers=auth).json()
    assert body == {"redirects": [], "count": 0, "total": 0, "limit": 500}


@needs_db
@pytest.mark.parametrize("bad", ["Reference/Upper.md", "reference/no-suffix", "../escape.md", "reference/a b.md"])
def test_a_malformed_to_path_is_rejected(auth, bad):
    assert client.get("/api/v1/redirects", params={"to_path": bad}, headers=auth).status_code == 422


@needs_db
@pytest.mark.parametrize("limit", [0, 2001])
def test_limit_is_bounded(auth, limit):
    assert client.get(f"/api/v1/redirects?limit={limit}", headers=auth).status_code == 422


@needs_db
def test_total_reveals_truncation(auth):
    body = client.get("/api/v1/redirects?limit=1", headers=auth).json()
    assert body["count"] == 1 and body["total"] >= len(SEED) > body["count"]


@needs_db
def test_reading_never_mutates_the_table(auth):
    before = _table()
    for q in ("", "?limit=1", f"?to_path={T1}", "?to_path=Bad.md"):
        client.get("/api/v1/redirects" + q, headers=auth)
    assert _table() == before
