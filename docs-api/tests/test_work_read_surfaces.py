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

import pytest

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


# --- agent history read: activity_bodies=redacted ------------------------------

# Synthetic secret-SHAPED value (non-secret example shape, as in
# migration/tests/test_redaction.py).
_TOKEN_SHAPED = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
_INITIATIVE = "9f3c1d52-4a7b-4c1e-9d88-2b6f0a1c7e34"


class SequencedCursor:
    """Returns one prepared result set per fetchall, in query order."""

    def __init__(self, results: list[list[tuple]]):
        self.results = list(results)
        self.calls: list[tuple[str, list | tuple]] = []

    def execute(self, query, params=None):
        self.calls.append((" ".join(str(query).split()), params))

    def fetchall(self):
        return self.results.pop(0)


def _get(monkeypatch, bodies: list, **kwargs):
    activities = [
        (f"a-{n}", "p-1", "NOTE", body, {"title": "free-form"}, f"2026-09-2{n}T00:00:00Z")
        for n, body in enumerate(bodies)
    ]
    cur = SequencedCursor([activities, [], []])
    monkeypatch.setattr(work_api, "get_conn", lambda: RecordingConnection(cur))
    monkeypatch.setattr(work_api, "_load", lambda _conn, _id: {"initiative_id": _INITIATIVE})
    from uuid import UUID
    return work_api.get_initiative(UUID(_INITIATIVE), principal=None, **kwargs)


def test_default_initiative_read_is_unchanged(monkeypatch):
    out = _get(monkeypatch, [f"token {_TOKEN_SHAPED}"], activity_bodies="raw")
    assert "activity_bodies" not in out
    assert out["activities"][0]["body"] == f"token {_TOKEN_SHAPED}"
    assert out["activities"][0]["metadata"] == {"title": "free-form"}
    assert "body_withheld" not in out["activities"][0]


def test_redacted_read_passes_every_body_through_canonical_redaction(monkeypatch):
    out = _get(monkeypatch, ["plain history", f"token {_TOKEN_SHAPED}"], activity_bodies="redacted")
    assert out["activity_bodies"] == "redacted"
    first, second = out["activities"]
    assert first["body"] == "plain history" and first["body_withheld"] is False
    assert _TOKEN_SHAPED not in repr(out)
    assert "<REDACTED:" in second["body"] and second["body_withheld"] is False
    # free-form metadata predates receiver policy and is not part of this read
    assert all("metadata" not in activity for activity in out["activities"])
    # order and identity are preserved
    assert [a["activity_id"] for a in out["activities"]] == ["a-0", "a-1"]


def test_redacted_read_withholds_a_body_the_transform_refuses(monkeypatch):
    def refuse(_source, **_kwargs):
        raise work_api.DocumentRefusedError(())

    monkeypatch.setattr(work_api, "redact", refuse)
    out = _get(monkeypatch, [f"```\n{_TOKEN_SHAPED}\n```"], activity_bodies="redacted")
    assert out["activities"][0]["body"] is None
    assert out["activities"][0]["body_withheld"] is True
    assert _TOKEN_SHAPED not in repr(out)


@pytest.mark.parametrize("body", ["plain history with an unmatched { brace", "a stray closing } brace"])
def test_unbalanced_brace_is_withheld_not_a_server_error(monkeypatch, body):
    # The real canonical transform: its brace post-condition raises AssertionError
    # on this synthetic text. The read must withhold the field, not fail with 500.
    with pytest.raises(AssertionError):
        work_api.redact(body, label="work-activity")
    out = _get(monkeypatch, [body, "plain {balanced} history"], activity_bodies="redacted")
    withheld, kept = out["activities"]
    assert withheld["body"] is None and withheld["body_withheld"] is True
    assert kept["body"] == "plain {balanced} history" and kept["body_withheld"] is False


def test_shared_boundary_preserves_refusal_semantics():
    from app.work_api import _redacted_activity_body
    assert _redacted_activity_body("ok {balanced}") == ("ok {balanced}", False)
    assert _redacted_activity_body("unmatched {") == (None, True)
    assert _redacted_activity_body("{<REDACTED:PASSWORD:old>}}") == (None, True)   # malformed marker
    assert _redacted_activity_body(None) == (None, True)
    sanitised, withheld = _redacted_activity_body(f"token {_TOKEN_SHAPED}")
    assert withheld is False and _TOKEN_SHAPED not in sanitised and "<REDACTED:" in sanitised


def test_activity_bodies_parameter_is_closed_to_unknown_values():
    from fastapi.testclient import TestClient
    from app.agent_auth import require_contributor

    app = _app()
    app.dependency_overrides[require_contributor] = lambda: None
    response = TestClient(app).get(f"/api/v1/initiatives/{_INITIATIVE}?activity_bodies=everything")
    assert response.status_code == 422


# --- agent read: allowlisted activity metadata, links and owner kind ---------

_CAPTURE = "3c2a1b0d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
_OWNER = "7a6b5c4d-3e2f-4a1b-9c8d-7e6f5a4b3c2d"
_HEX40 = "0123456789abcdef0123456789abcdef01234567"


def _get_full(monkeypatch, metadatas: list, links: list, owner_kind="AUTOMATION", **kwargs):
    activities = [(f"a-{n}", "p-1", "NOTE", "plain body", meta, f"2026-10-2{n}T00:00:00Z") for n, meta in enumerate(metadatas)]
    results = [activities, links, []]
    if kwargs.get("activity_bodies") == "redacted":
        results.append([(owner_kind,)] if owner_kind else [])
    cur = SequencedCursor(results)
    monkeypatch.setattr(work_api, "get_conn", lambda: RecordingConnection(cur))
    monkeypatch.setattr(work_api, "_load", lambda _conn, _id: {"initiative_id": _INITIATIVE, "owner_principal_id": _OWNER})
    from uuid import UUID
    return work_api.get_initiative(UUID(_INITIATIVE), principal=None, **kwargs)


def test_redacted_read_returns_allowlisted_typed_references_only(monkeypatch):
    meta = {"title": "free-form title", "classification": "x", "references": [
        {"reference_type": "PR", "reference_id": "cpaumelle/docplane#213"},
        {"reference_type": "COMMIT", "reference_id": _HEX40},
        {"reference_type": "ARTIFACT", "reference_id": f"token {_TOKEN_SHAPED}"},
        {"reference_type": "BOGUS", "reference_id": "dropped"},
        "not-a-dict"]}
    out = _get_full(monkeypatch, [meta], [], activity_bodies="redacted")
    activity = out["activities"][0]
    assert out["activity_metadata"] == "allowlisted-v1"
    assert "metadata" not in activity and "title" not in activity and "classification" not in activity
    assert activity["references"] == [
        {"reference_type": "PR", "reference_id": "cpaumelle/docplane#213", "reference_id_withheld": False},
        # a 40-hex id is secret-shaped to the canonical redactor: withheld, never exempted
        {"reference_type": "COMMIT", "reference_id": None, "reference_id_withheld": True},
        {"reference_type": "ARTIFACT", "reference_id": None, "reference_id_withheld": True},
    ]
    assert _TOKEN_SHAPED not in repr(out) and _HEX40 not in repr(out)


def test_redacted_read_projects_reported_provenance_and_source_capture(monkeypatch):
    meta = {"capture_id": _CAPTURE, "capture_kind": "FINDING", "capture_origin": {
        "agent": "claude-code-hub2", "corrects": "c96344b6",
        "supersedes": "a6d79271-bbcd-4814-82f4-e704c1890313, not-an-id",
        "related_capture": "c697f075-33c4-408c-92c9-e768265535b7",
        "repo": "trevarn-core", "password": "hunter2-synthetic", "free": {"nested": "dropped"}}}
    out = _get_full(monkeypatch, [meta], [], activity_bodies="redacted")
    activity = out["activities"][0]
    assert activity["source_capture_id"] == _CAPTURE
    assert activity["provenance"] == {
        "reported_producer": "claude-code-hub2", "reported_producer_withheld": False,
        "corrects": ["c96344b6"], "supersedes": ["a6d79271-bbcd-4814-82f4-e704c1890313"],
        "relates_to": ["c697f075-33c4-408c-92c9-e768265535b7"]}
    # the reported producer is a claim; the authenticated writer stays separate
    assert activity["author_principal_id"] == "p-1"
    assert "hunter2" not in repr(out) and "trevarn-core" not in repr(out)


def test_secret_shaped_reported_producer_is_withheld(monkeypatch):
    meta = {"capture_origin": {"source": f"agent {_TOKEN_SHAPED}"}}
    out = _get_full(monkeypatch, [meta], [], activity_bodies="redacted")
    assert out["activities"][0]["provenance"] == {"reported_producer": None, "reported_producer_withheld": True}
    assert _TOKEN_SHAPED not in repr(out)


def test_redacted_read_links_and_owner_kind(monkeypatch):
    links = [("CONTEXT", "EXTERNAL", f"docplane://work/captures/{_CAPTURE}", "p-1", "2026-10-20T00:00:00Z"),
             ("EVIDENCE", "EXTERNAL", f"https://example.invalid/?t={_TOKEN_SHAPED}", "p-1", "2026-10-20T00:00:00Z")]
    out = _get_full(monkeypatch, [{}], links, activity_bodies="redacted")
    founding, leaky = out["links"]
    assert founding["resource_id"] == f"docplane://work/captures/{_CAPTURE}" and founding["resource_id_withheld"] is False
    assert leaky["resource_id"] is None and leaky["resource_id_withheld"] is True
    assert out["owner_principal_id"] == _OWNER and out["owner_principal_kind"] == "AUTOMATION"
    assert _TOKEN_SHAPED not in repr(out)


def test_raw_read_shape_is_unchanged(monkeypatch):
    meta = {"capture_id": _CAPTURE, "capture_origin": {"agent": "x"}}
    out = _get_full(monkeypatch, [meta], [], activity_bodies="raw")
    assert "activity_metadata" not in out and "owner_principal_kind" not in out
    assert out["activities"][0]["metadata"] == meta and "provenance" not in out["activities"][0]


# --- agent discovery: view=summary and bounded text search -------------------

_ROW_KEYS = ("initiative_id", "initiative_key", "workspace_id", "workspace_key", "title", "objective", "scope",
             "owner_principal_id", "work_state", "priority", "version", "target_date", "review_due_at",
             "blocker_summary", "soak_started_at", "soak_review_at", "soak_success_criteria",
             "soak_failure_conditions", "soak_monitoring_ref", "parked_reason", "parked_review_at",
             "parked_indefinitely", "promotion_state", "promotion_change_id", "model_disposition",
             "model_disposition_note", "observe_disposition", "observe_disposition_note", "completed_at",
             "created_at", "updated_at")


def _row(n, title, owner=_OWNER):
    values = {k: None for k in _ROW_KEYS}
    values.update(initiative_id=f"00000000-0000-4000-8000-00000000000{n}", initiative_key=f"key-{n}", title=title,
                  objective="free-form objective", scope="free-form scope", owner_principal_id=owner,
                  work_state="ACTIVE", priority="NORMAL", version=1, updated_at=f"2026-10-2{n}T00:00:00Z")
    return tuple(values[k] for k in _ROW_KEYS)


def _summary(monkeypatch, rows, owner_rows, **kwargs):
    cur = SequencedCursor([rows, owner_rows])
    monkeypatch.setattr(work_api, "get_conn", lambda: RecordingConnection(cur))
    return work_api.list_initiatives(principal=None, view="summary", **kwargs), cur


def test_summary_view_is_allowlisted_redacted_and_stamped(monkeypatch):
    rows = [_row(1, "Plain title"), _row(2, f"leaked {_TOKEN_SHAPED}")]
    out, _ = _summary(monkeypatch, rows, [(_OWNER, "AUTOMATION")], limit=5)
    assert out["initiative_view"] == "summary-v1" and out["truncated"] is False and out["count"] == 2
    first, second = out["initiatives"]
    assert set(first) == {"initiative_id", "initiative_key", "work_state", "priority", "owner_principal_id",
                          "owner_principal_kind", "target_date", "review_due_at", "updated_at", "title", "title_withheld"}
    assert first["title"] == "Plain title" and first["owner_principal_kind"] == "AUTOMATION"
    assert second["title"] is None and second["title_withheld"] is True
    assert _TOKEN_SHAPED not in repr(out) and "free-form" not in repr(out)


def test_summary_view_reports_truncation(monkeypatch):
    rows = [_row(1, "a"), _row(2, "b"), _row(3, "c")]
    out, _ = _summary(monkeypatch, rows, [(_OWNER, "HUMAN")], limit=2)
    assert out["count"] == 2 and out["truncated"] is True


def test_text_search_is_literal_and_parameterised(monkeypatch):
    _out, cur = _summary(monkeypatch, [], [], q="50%_x'; --", limit=5)
    sql, params = cur.calls[0]
    assert "i.initiative_key ILIKE %s ESCAPE" in sql and "i.title ILIKE %s ESCAPE" in sql
    assert params[-3:-1] == ["%50\\%\\_x'; --%"] * 2
    assert "50%_x" not in sql


def test_default_list_view_is_unchanged(monkeypatch):
    cur = RecordingCursor()
    monkeypatch.setattr(work_api, "get_conn", lambda: RecordingConnection(cur))
    monkeypatch.setattr(work_api, "_stamp_soak_resolution", lambda *a, **k: None)
    out = work_api.list_initiatives(principal=None)
    assert "initiative_view" not in out and "ILIKE" not in cur.calls[0][0]
