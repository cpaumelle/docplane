"""Contract for the WORK read surfaces added for captures d2b5460d / 9fbab02e / 5eb0bb56.

Three gaps, all the same shape: state that WORK already stored but exposed no way
to read.

  P2  a slug could not be resolved to an initiative without listing everything
      and matching client-side
  P3  the page -> initiative direction had no read at all
  P5  a principal UUID could not be resolved to a name, so activity authorship
      was inferable only from timing

Database-free, matching test_work_capture_contract.py: route inventory, OpenAPI
additivity and the query predicates each new read builds.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI

from app.agent_api import router as agent_router
from app.work_api import router as work_router
import app.work_api as work_api

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "docs-api"))


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(work_router)
    app.include_router(agent_router)
    return app


def _routes(app: FastAPI) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        if path:
            out.setdefault(path, set()).update(getattr(route, "methods", set()) or set())
    return out


class RecordingCursor:
    """Captures the SQL and params.

    `existence` drives the docs.pages probe that _resolve_link_resource runs:
    True means the page exists, False means it does not (so the endpoint must
    404 rather than report zero links).
    """

    def __init__(self, rows: list[tuple] | None = None, existence: bool = True):
        self.rows = rows or []
        self.existence = existence
        self.calls: list[tuple[str, list | tuple]] = []
        self._last = ""

    def execute(self, query, params=None):
        self._last = " ".join(str(query).split())
        self.calls.append((self._last, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        if self._last.startswith("SELECT 1 FROM docs.pages"):
            return (1,) if self.existence else None
        return self.rows[0] if self.rows else None


class RecordingConnection:
    def __init__(self, cursor: RecordingCursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        pass


# --- route inventory ---------------------------------------------------------

def test_new_read_surfaces_exist_with_get():
    routes = _routes(_app())
    assert "GET" in routes["/api/v1/initiatives/{initiative_id}/links"]
    assert "GET" in routes["/api/v1/pages/{page_resource_id}/initiatives"]
    assert "GET" in routes["/api/v1/principals/{principal_id}"]


def test_the_write_surface_is_unchanged():
    """Additive only: the POST that already existed on links must survive."""
    routes = _routes(_app())
    assert "POST" in routes["/api/v1/initiatives/{initiative_id}/links"]
    for prior in (
        "/api/v1/initiatives",
        "/api/v1/initiatives/{initiative_id}",
        "/api/v1/initiatives/{initiative_id}/activities",
        "/api/v1/work/captures",
    ):
        assert prior in routes, prior


def test_principals_read_is_a_resolver_not_a_directory():
    """No list endpoint: resolving one id must not become an enumeration surface."""
    routes = _routes(_app())
    assert "/api/v1/principals" not in routes


def test_principal_read_exposes_identity_only(monkeypatch):
    """Credential material lives in a separate table; metadata is free-form."""
    import app.agent_api as agent_api

    cur = RecordingCursor(rows=[("p-1", "claude-code@hub2", "AGENT", "ACTIVE", "2026-09-16")])
    monkeypatch.setattr(agent_api, "get_conn", lambda: RecordingConnection(cur))

    from uuid import UUID
    out = agent_api.get_principal(
        UUID("9f3c1d52-4a7b-4c1e-9d88-2b6f0a1c7e34"), principal=None
    )

    assert set(out) == {"principal_id", "display_name", "principal_kind", "status", "created_at"}
    assert "metadata" not in out
    sql = cur.calls[0][0]
    assert "docplane.api_tokens" not in sql
    assert "token" not in sql.lower()
    assert "metadata" not in sql.lower()


# --- P2: slug resolution -----------------------------------------------------

def test_key_filter_builds_an_exact_predicate(monkeypatch):
    cur = RecordingCursor()
    monkeypatch.setattr(work_api, "get_conn", lambda: RecordingConnection(cur))
    monkeypatch.setattr(work_api, "_stamp_soak_resolution", lambda *a, **k: None)

    work_api.list_initiatives(key="docplane-search-effective-terms-1", principal=None)

    sql, params = cur.calls[0]
    assert "i.initiative_key = %s" in sql
    assert "docplane-search-effective-terms-1" in params


def test_key_resolves_a_closed_initiative_without_include_closed(monkeypatch):
    """Identity lookup must not depend on the initiative still being open.

    Otherwise resolving a slug silently returns nothing once the work completes,
    which is the worst time to lose the reference.
    """
    cur = RecordingCursor()
    monkeypatch.setattr(work_api, "get_conn", lambda: RecordingConnection(cur))
    monkeypatch.setattr(work_api, "_stamp_soak_resolution", lambda *a, **k: None)

    work_api.list_initiatives(key="some-closed-initiative", principal=None)

    sql = cur.calls[0][0]
    assert "NOT IN ('COMPLETE', 'ABANDONED')" not in sql


def test_listing_without_key_still_hides_closed_by_default(monkeypatch):
    cur = RecordingCursor()
    monkeypatch.setattr(work_api, "get_conn", lambda: RecordingConnection(cur))
    monkeypatch.setattr(work_api, "_stamp_soak_resolution", lambda *a, **k: None)

    work_api.list_initiatives(principal=None)

    assert "NOT IN ('COMPLETE', 'ABANDONED')" in cur.calls[0][0]


# --- P3: the reverse edge ----------------------------------------------------

def test_page_initiatives_queries_the_reverse_edge(monkeypatch):
    cur = RecordingCursor()
    monkeypatch.setattr(work_api, "get_conn", lambda: RecordingConnection(cur))

    from uuid import UUID
    work_api.list_page_initiatives(
        UUID("97cbd90d-70b2-514d-bd7e-c4f943078045"), principal=None
    )

    # calls[0] is the docs.pages existence probe; find the reverse-edge query
    # by content rather than position, so adding a guard does not break this.
    sql, params = next(c for c in cur.calls if "work.initiative_links" in c[0])
    assert "l.resource_type = 'PAGE'" in sql
    assert "l.resource_id = %s" in sql
    assert "JOIN work.initiatives" in sql
    assert params == ("97cbd90d-70b2-514d-bd7e-c4f943078045",)


# --- an unknown page must not report "nothing owns me" -----------------------

def test_unlinked_but_real_page_returns_an_empty_set(monkeypatch):
    cur = RecordingCursor(rows=[], existence=True)
    monkeypatch.setattr(work_api, "get_conn", lambda: RecordingConnection(cur))

    from uuid import UUID
    out = work_api.list_page_initiatives(
        UUID("97cbd90d-70b2-514d-bd7e-c4f943078045"), principal=None
    )

    assert out["count"] == 0 and out["initiatives"] == []
    assert any(c[0].startswith("SELECT 1 FROM docs.pages") for c in cur.calls)


def test_nonexistent_page_is_a_404_not_an_empty_set(monkeypatch):
    """A stale or mistyped UUID must not read as "no work owns this page".

    That is the dangerous wrong answer for an endpoint whose purpose is
    "may I edit this?" — it invites exactly the edit it should have blocked.
    """
    import pytest
    from fastapi import HTTPException

    cur = RecordingCursor(rows=[], existence=False)
    monkeypatch.setattr(work_api, "get_conn", lambda: RecordingConnection(cur))

    from uuid import UUID
    with pytest.raises(HTTPException) as excinfo:
        work_api.list_page_initiatives(
            UUID("00000000-0000-4000-8000-000000000000"), principal=None
        )

    assert excinfo.value.status_code == 404
    assert excinfo.value.detail["code"] == "LINK_RESOURCE_NOT_FOUND"
    # and it must not have run the reverse-edge query at all
    assert not any("work.initiative_links" in c[0] for c in cur.calls)


def test_blank_key_is_not_treated_as_a_filter(monkeypatch):
    """"   " must not both vanish from the SQL and suppress the closed filter."""
    cur = RecordingCursor()
    monkeypatch.setattr(work_api, "get_conn", lambda: RecordingConnection(cur))
    monkeypatch.setattr(work_api, "_stamp_soak_resolution", lambda *a, **k: None)

    work_api.list_initiatives(key="   ", principal=None)

    sql, params = cur.calls[0]
    assert "i.initiative_key = %s" not in sql
    assert "NOT IN ('COMPLETE', 'ABANDONED')" in sql
