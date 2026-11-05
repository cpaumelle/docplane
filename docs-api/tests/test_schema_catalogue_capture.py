"""Guarded capture of structure and ledger (schema_catalogue_capture), scripted-connection tests.

Covers lock acquisition and polling, snapshot ordering, retry on a ledger change mid-capture,
refusal after the last attempt, refusal when a migration holds the lock for the whole budget,
UNAVAILABLE/EMPTY ledgers never certifying consistency, and the generator's dispatch. The
real-PostgreSQL case (DDL committed, ledger row not yet written) is in
test_schema_catalogue_capture_integration.py.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2.errors
import psycopg2.extensions
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import schema_catalogue  # noqa: E402
import schema_catalogue_capture as capture  # noqa: E402

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
LEDGER_A = [("000_a.sql", "a" * 64, T0), ("001_b.sql", "b" * 64, T0)]
LEDGER_B = LEDGER_A + [("002_c.sql", "c" * 64, T0)]
STRUCTURE = {"platform": {"comment": None, "views": {}, "enums": {}, "tables": {}}}
DENIED = psycopg2.errors.InsufficientPrivilege("permission denied for table schema_migrations")


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        c = self.conn
        if "pg_try_advisory_lock_shared" in sql:
            c.log.append(("try_lock", params[0]))
            c.last_rows = [(c.lock_answers.pop(0) if c.lock_answers else True,)]
        elif "pg_advisory_unlock_shared" in sql:
            c.log.append(("unlock", params[0]))
            c.last_rows = [(True,)]
        elif sql.lstrip().startswith("SELECT"):
            c.log.append(("ledger_select",))
            item = c.ledger_reads.pop(0)
            if isinstance(item, Exception):
                raise item
            c.last_rows = item
        else:
            c.log.append(("sql", sql.split()[0]))

    def fetchone(self):
        return self.conn.last_rows[0]

    def fetchall(self):
        return list(self.conn.last_rows)


class _Conn:
    """Scripted connection: ``ledger_reads`` are the row sets (or exceptions) successive ledger
    SELECTs produce; ``lock_answers`` the successive pg_try_advisory_lock_shared results."""

    def __init__(self, ledger_reads, log, lock_answers=None):
        self.ledger_reads, self.log = list(ledger_reads), log
        self.lock_answers = list(lock_answers or [])
        self.last_rows, self.closed = [], False

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.log.append(("commit",))

    def rollback(self):
        self.log.append(("rollback",))

    def close(self):
        self.closed = True
        self.log.append(("close",))

    def set_session(self, *, isolation_level, readonly):
        self.log.append(("set_session", isolation_level, readonly))


def _connector(script, lock_script=None):
    log, conns = [], []

    def connect(**_params):
        i = len(conns)
        conn = _Conn(script[i], log, (lock_script or [None] * len(script))[i])
        conns.append(conn)
        return conn

    return connect, log, conns


def _introspector(log):
    def introspect(conn, schemas):
        log.append(("introspect", tuple(schemas)))
        return STRUCTURE
    return introspect


def _metadata(log):
    def read(conn):
        log.append(("metadata",))
        return {"database_name": "trevarn", "server_version": "16.13"}
    return read


def _run(connect, log, **kw):
    kw.setdefault("sleeper", lambda s: log.append(("sleep", s)))
    return capture.capture_with_ledger(
        {"dsn": "x"}, ["platform"], "public.schema_migrations",
        introspector=_introspector(log), metadata_reader=_metadata(log), connector=connect, **kw,
    )


def test_capture_takes_the_shared_lock_first_and_reads_inside_one_snapshot():
    connect, log, conns = _connector([[LEDGER_A, LEDGER_A, LEDGER_A]])
    structure, metadata, ledger, info = _run(connect, log)
    assert structure is STRUCTURE and metadata["server_version"] == "16.13"
    assert ledger["status"] == "CAPTURED" and ledger["rows"] == 2 and ledger["head_filename"] == "001_b.sql"
    assert info == {"guard": capture.GUARD, "attempts": 1, "lock_wait_seconds": 0.0, "consistent": True}
    assert len(conns) == 1 and conns[0].closed
    kinds = [e[0] for e in log]
    snapshot = ("set_session", psycopg2.extensions.ISOLATION_LEVEL_REPEATABLE_READ, True)
    assert kinds[0] == "try_lock" and log[0][1] == "public.schema_migrations"
    selects = [i for i, k in enumerate(kinds) if k == "ledger_select"]
    assert len(selects) == 3
    assert selects[0] < log.index(snapshot) < kinds.index("introspect") < kinds.index("metadata") < selects[1] < selects[2]
    assert kinds.index("unlock") > selects[2] and kinds.index("close") > kinds.index("unlock")
    assert ("set_session", psycopg2.extensions.ISOLATION_LEVEL_DEFAULT, False) in log


def test_capture_polls_while_a_migration_holds_the_lock_then_proceeds():
    connect, log, _ = _connector([[LEDGER_A, LEDGER_A, LEDGER_A]], lock_script=[[False, False, True]])
    _, _, _, info = _run(connect, log, lock_wait_seconds=10, lock_poll_seconds=0.5)
    assert [e for e in log if e[0] == "sleep"] == [("sleep", 0.5), ("sleep", 0.5)]
    assert info["lock_wait_seconds"] == 1.0 and info["consistent"] is True


def test_capture_refuses_when_the_migration_lock_is_held_for_the_whole_budget(monkeypatch):
    connect, log, conns = _connector([[LEDGER_A, LEDGER_A, LEDGER_A]], lock_script=[[False] * 50])
    clock = [0.0]
    monkeypatch.setattr(capture.time, "monotonic", lambda: clock[0])

    def sleep(s):
        clock[0] += s
        log.append(("sleep", s))

    with pytest.raises(capture.MigrationInProgress, match="refusing to publish"):
        _run(connect, log, lock_wait_seconds=2.0, lock_poll_seconds=0.5, sleeper=sleep)
    assert "ledger_select" not in [e[0] for e in log]      # nothing was read under a held lock
    assert conns[0].closed


def test_a_migration_during_capture_is_retried_on_a_fresh_connection():
    connect, log, conns = _connector([[LEDGER_A, LEDGER_A, LEDGER_B], [LEDGER_B, LEDGER_B, LEDGER_B]])
    _, _, ledger, info = _run(connect, log)
    assert info["attempts"] == 2 and info["consistent"] is True
    assert ledger["head_filename"] == "002_c.sql" and ledger["rows"] == 3
    assert len(conns) == 2 and all(c.closed for c in conns)
    assert [e for e in log if e[0] == "introspect"] == [("introspect", ("platform",))] * 2


def test_persistent_change_refuses_publication_after_the_last_attempt():
    script = [[LEDGER_A, LEDGER_A, LEDGER_B], [LEDGER_B, LEDGER_B, LEDGER_A], [LEDGER_A, LEDGER_B, LEDGER_B]]
    connect, log, conns = _connector(script)
    with pytest.raises(capture.SourceChangedDuringCapture, match="refusing to publish"):
        _run(connect, log, attempts=3)
    assert len(conns) == 3 and all(c.closed for c in conns)


def test_unavailable_ledger_never_certifies_a_consistent_capture():
    connect, log, _ = _connector([[DENIED, DENIED, DENIED]])
    structure, _, ledger, info = _run(connect, log)
    assert structure is STRUCTURE                         # structure-only publication stays possible
    assert ledger["status"] == "UNAVAILABLE" and ledger["error_class"] == "InsufficientPrivilege"
    assert info["consistent"] is False
    assert "does not certify a structure/ledger pair" in info["reason"]


def test_empty_ledger_is_not_consistent_either():
    connect, log, _ = _connector([[[], [], []]])
    _, _, ledger, info = _run(connect, log)
    assert ledger["status"] == "EMPTY" and info["consistent"] is False


def test_generator_dispatch_uses_the_capture_only_with_a_ledger_and_records_capture_info(monkeypatch, tmp_path):
    calls, emitted = [], {}

    def fake_capture(params, schemas, table, **kwargs):
        calls.append((table, sorted(kwargs)))
        return STRUCTURE, {"database_name": "t", "server_version": "16"}, {
            "status": "CAPTURED", "table": table, "rows": 1, "head_filename": "000.sql",
            "head_sha256_prefix": "a" * 24, "head_applied_at": None,
            "ledger_set_digest_prefix": "b" * 24, "digest_prefix_hex": 24,
            "identity_note": "truncated comparison identifiers, not integrity hashes",
        }, {"guard": capture.GUARD, "attempts": 1, "lock_wait_seconds": 0.0, "consistent": True}

    monkeypatch.setattr(capture, "capture_with_ledger", fake_capture)
    monkeypatch.setattr(schema_catalogue, "source_connection_parameters", lambda: {"dsn": "x"})
    monkeypatch.setattr(schema_catalogue, "introspect", lambda *_: STRUCTURE)
    monkeypatch.setattr(schema_catalogue, "source_metadata", lambda *_: {"database_name": "t", "server_version": "16"})

    class Source:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(schema_catalogue.psycopg2, "connect", lambda **_: Source())
    for key, value in {"CATALOGUE_DB_KEY": "trevarn", "CATALOGUE_SCHEMAS": "platform",
                       "CATALOGUE_ENVIRONMENT": "production", "CATALOGUE_SOURCE_IDENTITY": "VPS3/trevarn",
                       "CATALOGUE_LEDGER_TABLE": "public.schema_migrations"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CATALOGUE_CONFIG", raising=False)
    out = tmp_path / "p.json"
    assert schema_catalogue.main(["--dry-run", "--emit-projection", str(out)]) == 0
    assert calls == [("public.schema_migrations", ["introspector", "lock_wait_seconds", "metadata_reader"])]
    import json
    ledger = json.loads(out.read_text())["provenance"]["migration_ledger"]
    assert ledger["capture"] == {"guard": capture.GUARD, "attempts": 1, "lock_wait_seconds": 0.0, "consistent": True}
    monkeypatch.delenv("CATALOGUE_LEDGER_TABLE")
    assert schema_catalogue.main(["--dry-run"]) == 0
    assert len(calls) == 1   # no-ledger path never touches the capture module
