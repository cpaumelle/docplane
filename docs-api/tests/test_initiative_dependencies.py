"""Regression for capture d79bb0ce: POST /initiatives/{id}/dependencies 500s.

The statement was written as

    INSERT INTO work.initiative_dependencies (...) VALUES (...)
    ON CONFLICT DO UPDATE SET dependency_kind = EXCLUDED.dependency_kind

PostgreSQL allows a bare `ON CONFLICT` only with `DO NOTHING`. `DO UPDATE`
requires an explicit conflict target, so this was a *parse-time* syntax error:
it failed on every call, including the first insert of a pair, which is why the
reporter hit it while creating a brand-new dependency. psycopg2 raised, nothing
caught it, and FastAPI returned a bare 'Internal Server Error' string with none
of the structured error envelope the rest of this API provides.

Typed initiative-to-initiative dependencies were therefore unrecordable, which
pushed callers into exactly the prose-only relationship the Work guide forbids.

Why this was not caught: the only existing coverage of this route is
test_work_capture_contract.py, which asserts the *path is present in the route
table*. Registration was never the problem. So the second test here is a
repo-wide guard for the defect class rather than for this one line -- a
database-free suite cannot execute SQL, but it can refuse to ship an untargeted
upsert.
"""
from __future__ import annotations

import re
import tokenize
import sys
from pathlib import Path
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "docs-api"))

import app.work_api as work_api  # noqa: E402
from app.work_models import InitiativeDependencyCreate  # noqa: E402

APP_DIR = ROOT / "docs-api" / "app"

A = UUID("7ef6ea1e-26fd-4d78-ab29-da265d704349")  # uk-supervision-architecture-1
B = UUID("87c68b61-ecfa-4574-b131-f7df97720bb0")  # uk-power-recovery-model-1


class RecordingCursor:
    def __init__(self):
        self.calls: list[tuple[str, tuple | list | None]] = []

    def execute(self, query, params=None):
        self.calls.append((" ".join(str(query).split()), params))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


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


def _upsert(monkeypatch) -> str:
    cur = RecordingCursor()
    monkeypatch.setattr(work_api, "get_conn", lambda: RecordingConnection(cur))
    # _load only proves both initiatives exist; the defect is downstream of it.
    monkeypatch.setattr(work_api, "_load", lambda *a, **k: {"initiative_id": str(a[1])})

    work_api.add_dependency(
        A,
        InitiativeDependencyCreate(depends_on_initiative_id=B, dependency_kind="RELATED"),
        principal=None,
    )
    inserts = [sql for sql, _ in cur.calls if sql.startswith("INSERT INTO work.initiative_dependencies")]
    assert len(inserts) == 1, cur.calls
    return inserts[0]


def test_upsert_names_a_conflict_target(monkeypatch):
    """The exact defect: DO UPDATE with no target is not valid SQL."""
    sql = _upsert(monkeypatch)
    assert "ON CONFLICT DO UPDATE" not in sql, (
        "untargeted ON CONFLICT ... DO UPDATE is a syntax error in PostgreSQL: " + sql
    )
    assert "ON CONFLICT (initiative_id, depends_on_initiative_id) DO UPDATE" in sql, sql


def test_upsert_target_matches_the_primary_key(monkeypatch):
    """The target must be the PK declared in the genesis migration, or the
    upsert raises at runtime instead of parse time -- a worse failure, because
    it would only appear on the second POST for a pair."""
    genesis = (ROOT / "db" / "migrations" / "000_docplane_genesis.sql").read_text()
    table = genesis.split("CREATE TABLE work.initiative_dependencies", 1)[1].split(");", 1)[0]
    pk = re.search(r"PRIMARY KEY \(([^)]*)\)", table).group(1)
    pk_cols = [c.strip() for c in pk.split(",")]

    sql = _upsert(monkeypatch)
    target = re.search(r"ON CONFLICT \(([^)]*)\) DO UPDATE", sql).group(1)
    assert [c.strip() for c in target.split(",")] == pk_cols


def test_self_dependency_is_refused_before_any_write(monkeypatch):
    """Pre-existing guard; asserted here so the fix cannot regress it."""
    cur = RecordingCursor()
    monkeypatch.setattr(work_api, "get_conn", lambda: RecordingConnection(cur))
    with pytest.raises(Exception) as excinfo:
        work_api.add_dependency(
            A,
            InitiativeDependencyCreate(depends_on_initiative_id=A, dependency_kind="RELATED"),
            principal=None,
        )
    assert getattr(excinfo.value, "status_code", None) == 422
    assert cur.calls == []


def test_no_untargeted_upsert_anywhere_in_the_app():
    """Class-wide guard, not line-specific.

    A database-free suite cannot execute SQL, so it cannot catch a syntax
    error by running it. It can, however, refuse the one construct that is
    always wrong. `ON CONFLICT DO NOTHING` stays legal and is untouched.
    """
    offenders: list[str] = []
    pattern = re.compile(r"ON\s+CONFLICT\s+DO\s+UPDATE", re.I)
    for path in sorted(APP_DIR.rglob("*.py")):
        # Only string literals: SQL never lives in a comment, and prose about
        # this defect (including the comment on the fixed line) must not trip
        # the guard that protects it.
        with path.open("rb") as handle:
            for token in tokenize.tokenize(handle.readline):
                if token.type == tokenize.STRING and pattern.search(token.string):
                    offenders.append(f"{path.relative_to(ROOT)}:{token.start[0]}")
    assert not offenders, (
        "ON CONFLICT ... DO UPDATE requires an explicit conflict target; "
        "found untargeted: " + ", ".join(offenders)
    )
