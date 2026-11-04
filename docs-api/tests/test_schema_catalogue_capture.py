"""Consistent capture of structure and ledger (schema_catalogue_capture).

Pure tests with a scripted fake connection: snapshot ordering, retry on a ledger change
mid-capture, refusal after the last attempt, and the generator's dispatch (ledger path uses
the capture; the no-ledger path is byte-for-byte the original two short reads).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

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


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.log.append(("sql", sql.split()[0] if sql.strip() else ""))
        if sql.lstrip().startswith("SELECT"):
            rows = self.conn.ledger_reads.pop(0)
            self.conn.last_rows = rows

    def fetchall(self):
        return list(self.conn.last_rows)


class _Conn:
    """Scripted connection: ``ledger_reads`` is the sequence of row sets successive ledger SELECTs return."""

    def __init__(self, ledger_reads, log):
        self.ledger_reads, self.log, self.last_rows, self.closed = list(ledger_reads), log, [], False

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


def _connector(script):
    """``script`` is a list per connection of the ledger row sets its three reads return."""
    log, conns = [], []

    def connect(**_params):
        conn = _Conn(script[len(conns)], log)
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


def test_consistent_capture_reads_structure_and_ledger_inside_one_repeatable_read_snapshot():
    connect, log, conns = _connector([[LEDGER_A, LEDGER_A, LEDGER_A]])
    structure, metadata, ledger, info = capture.capture_with_ledger(
        {"dsn": "x"}, ["platform"], "public.schema_migrations",
        introspector=_introspector(log), metadata_reader=_metadata(log), connector=connect,
    )
    assert structure is STRUCTURE and metadata["server_version"] == "16.13"
    assert ledger["status"] == "CAPTURED" and ledger["rows"] == 2 and ledger["head_filename"] == "001_b.sql"
    assert info == {"attempts": 1, "consistent": True}
    assert len(conns) == 1 and conns[0].closed
    events = [e[0] if e[0] != "set_session" else ("set_session", e[1], e[2]) for e in log]
    # order: ledger read, commit, snapshot opened (repeatable read, read only), structure, metadata,
    # ledger again, commit, session reset, ledger after, commit, close
    snapshot = ("set_session", psycopg2.extensions.ISOLATION_LEVEL_REPEATABLE_READ, True)
    assert events.index(snapshot) < events.index("introspect") < events.index("metadata")
    sql_selects = [i for i, e in enumerate(log) if e == ("sql", "SELECT")]
    assert len(sql_selects) == 3
    assert sql_selects[0] < events.index(snapshot) < sql_selects[1] < sql_selects[2]
    assert ("set_session", psycopg2.extensions.ISOLATION_LEVEL_DEFAULT, False) in events


def test_a_migration_during_capture_is_retried_on_a_fresh_connection():
    # attempt 1: ledger changes between the snapshot and the after-read; attempt 2 is stable
    connect, log, conns = _connector([[LEDGER_A, LEDGER_A, LEDGER_B], [LEDGER_B, LEDGER_B, LEDGER_B]])
    _, _, ledger, info = capture.capture_with_ledger(
        {"dsn": "x"}, ["platform"], "public.schema_migrations",
        introspector=_introspector(log), metadata_reader=_metadata(log), connector=connect,
    )
    assert info == {"attempts": 2, "consistent": True}
    assert ledger["head_filename"] == "002_c.sql" and ledger["rows"] == 3
    assert len(conns) == 2 and all(c.closed for c in conns)
    assert log.count(("introspect", ("platform",))) == 2   # structure re-read with the new state


def test_a_migration_between_the_first_read_and_the_snapshot_is_also_detected():
    connect, log, conns = _connector([[LEDGER_A, LEDGER_B, LEDGER_B], [LEDGER_B, LEDGER_B, LEDGER_B]])
    _, _, ledger, info = capture.capture_with_ledger(
        {"dsn": "x"}, ["platform"], "public.schema_migrations",
        introspector=_introspector(log), metadata_reader=_metadata(log), connector=connect,
    )
    assert info["attempts"] == 2 and ledger["rows"] == 3


def test_persistent_change_refuses_publication_after_the_last_attempt():
    script = [[LEDGER_A, LEDGER_A, LEDGER_B], [LEDGER_B, LEDGER_B, LEDGER_A], [LEDGER_A, LEDGER_B, LEDGER_B]]
    connect, log, conns = _connector(script)
    with pytest.raises(capture.SourceChangedDuringCapture, match="refusing to publish"):
        capture.capture_with_ledger(
            {"dsn": "x"}, ["platform"], "public.schema_migrations",
            introspector=_introspector(log), metadata_reader=_metadata(log), connector=connect, attempts=3,
        )
    assert len(conns) == 3 and all(c.closed for c in conns)


def test_generator_dispatch_uses_the_capture_only_when_a_ledger_table_is_configured(monkeypatch):
    calls = []

    def fake_capture(params, schemas, table, **kwargs):
        calls.append((table, sorted(kwargs)))
        return STRUCTURE, {"database_name": "t", "server_version": "16"}, {
            "status": "CAPTURED", "table": table, "rows": 1, "head_filename": "000.sql",
            "head_sha256_prefix": "a" * 24, "head_applied_at": None,
            "ledger_set_digest_prefix": "b" * 24, "digest_prefix_hex": 24,
            "identity_note": "truncated comparison identifiers, not integrity hashes",
        }, {"attempts": 1, "consistent": True}

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
    monkeypatch.setenv("CATALOGUE_DB_KEY", "trevarn")
    monkeypatch.setenv("CATALOGUE_SCHEMAS", "platform")
    monkeypatch.setenv("CATALOGUE_ENVIRONMENT", "production")
    monkeypatch.setenv("CATALOGUE_SOURCE_IDENTITY", "VPS3/trevarn")
    monkeypatch.delenv("CATALOGUE_CONFIG", raising=False)
    monkeypatch.setenv("CATALOGUE_LEDGER_TABLE", "public.schema_migrations")
    assert schema_catalogue.main(["--dry-run"]) == 0
    assert calls == [("public.schema_migrations", ["introspector", "metadata_reader"])]
    monkeypatch.delenv("CATALOGUE_LEDGER_TABLE")
    assert schema_catalogue.main(["--dry-run"]) == 0
    assert len(calls) == 1   # no-ledger path never touches the capture module
