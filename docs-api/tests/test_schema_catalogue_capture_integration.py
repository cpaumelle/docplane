"""Real-PostgreSQL proof of the structure/ledger capture guard.

Requires a disposable PostgreSQL (DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS, as the other
database-backed tests). A "migration runner" thread behaves exactly like trevarn-core
db/migrate.sh after its coordination change: it holds pg_advisory_lock(hashtext(ledger)) for
one session, commits the DDL, then PAUSES before inserting the ledger row. The capture must
wait, retry or refuse; it must never return the new structure with the old ledger.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import schema_catalogue  # noqa: E402
import schema_catalogue_capture as capture  # noqa: E402
from schema_catalogue_source import introspect  # noqa: E402

pytestmark = pytest.mark.skipif(not os.environ.get("DB_HOST"), reason="requires a PostgreSQL database")
LEDGER = "capture_probe.schema_migrations"
LOCK_SQL = "SELECT pg_advisory_lock(hashtext(%s)::bigint)"
UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtext(%s)::bigint)"


def _params():
    return {
        "host": os.environ["DB_HOST"], "port": os.environ.get("DB_PORT", "5432"),
        "dbname": os.environ.get("DB_NAME", "docs"), "user": os.environ.get("DB_USER", "docs"),
        "password": os.environ.get("DB_PASS", ""),
    }


@pytest.fixture
def probe():
    import psycopg2
    conn = psycopg2.connect(**_params())
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS capture_probe CASCADE")
    cur.execute("CREATE SCHEMA capture_probe")
    cur.execute("CREATE TABLE capture_probe.schema_migrations (filename text PRIMARY KEY, sha256 text NOT NULL, "
                "applied_at timestamptz NOT NULL DEFAULT now())")
    cur.execute("INSERT INTO capture_probe.schema_migrations (filename, sha256) VALUES ('000_base.sql', repeat('a', 64))")
    cur.execute("CREATE TABLE capture_probe.t1 (id integer PRIMARY KEY)")
    yield conn
    cur.execute("DROP SCHEMA IF EXISTS capture_probe CASCADE")
    conn.close()


class Runner(threading.Thread):
    """migrate.sh after the coordination change: lock, DDL commit, (pause), ledger row, unlock."""

    def __init__(self, pause_seconds: float, take_lock: bool = True):
        super().__init__(daemon=True)
        self.pause, self.take_lock = pause_seconds, take_lock
        self.ddl_committed = threading.Event()
        self.finished = threading.Event()
        self.lock_waited = None

    def run(self):
        import psycopg2
        conn = psycopg2.connect(**_params())
        cur = conn.cursor()
        t0 = time.monotonic()
        if self.take_lock:
            cur.execute(LOCK_SQL, (LEDGER,))
            conn.commit()
        self.lock_waited = time.monotonic() - t0
        cur.execute("CREATE TABLE capture_probe.t2 (id integer PRIMARY KEY, note text)")
        conn.commit()                                   # DDL visible, ledger NOT yet written
        self.ddl_committed.set()
        time.sleep(self.pause)
        cur.execute("INSERT INTO capture_probe.schema_migrations (filename, sha256) VALUES ('001_t2.sql', repeat('b', 64))")
        conn.commit()
        if self.take_lock:
            cur.execute(UNLOCK_SQL, (LEDGER,))
            conn.commit()
        conn.close()
        self.finished.set()


def _capture(**kw):
    return capture.capture_with_ledger(
        _params(), ["capture_probe"], LEDGER,
        introspector=introspect, metadata_reader=schema_catalogue.source_metadata, **kw,
    )


def test_capture_in_the_gap_waits_for_the_runner_and_returns_the_matching_pair(probe):
    runner = Runner(pause_seconds=2.0)
    runner.start()
    assert runner.ddl_committed.wait(10)
    started = time.monotonic()
    structure, _, ledger, info = _capture(lock_wait_seconds=15, lock_poll_seconds=0.2)
    elapsed = time.monotonic() - started
    assert runner.finished.wait(10)
    # The capture could only proceed once the runner released the lock, i.e. after the ledger row.
    assert "t2" in structure["capture_probe"]["tables"]
    assert ledger["status"] == "CAPTURED" and ledger["rows"] == 2 and ledger["head_filename"] == "001_t2.sql"
    assert info["consistent"] is True and info["lock_wait_seconds"] >= 1.0
    assert elapsed >= 1.0


def test_capture_refuses_when_the_runner_pauses_longer_than_the_wait_budget(probe):
    runner = Runner(pause_seconds=4.0)
    runner.start()
    assert runner.ddl_committed.wait(10)
    with pytest.raises(capture.MigrationInProgress):
        _capture(lock_wait_seconds=1.0, lock_poll_seconds=0.2)
    assert runner.finished.wait(15)
    # Afterwards the pair is consistent again and the capture succeeds.
    structure, _, ledger, info = _capture(lock_wait_seconds=5, lock_poll_seconds=0.2)
    assert "t2" in structure["capture_probe"]["tables"] and ledger["rows"] == 2 and info["consistent"]


def test_a_runner_arriving_during_a_capture_waits_for_the_shared_lock(probe):
    import psycopg2
    holder = psycopg2.connect(**_params())
    cur = holder.cursor()
    cur.execute("SELECT pg_try_advisory_lock_shared(hashtext(%s)::bigint)", (LEDGER,))
    assert cur.fetchone()[0] is True
    holder.commit()
    runner = Runner(pause_seconds=0.0)
    runner.start()
    assert not runner.ddl_committed.wait(1.5)          # blocked on the exclusive lock while we hold shared
    cur.execute("SELECT pg_advisory_unlock_shared(hashtext(%s)::bigint)", (LEDGER,))
    holder.commit()
    holder.close()
    assert runner.ddl_committed.wait(10) and runner.finished.wait(10)
    assert runner.lock_waited >= 1.0


def test_without_the_runner_lock_the_gap_is_only_narrowed_by_the_ledger_compare(probe):
    """Documents why the runner MUST hold the lock: a legacy writer that commits DDL and inserts the
    ledger row later is caught only if the insert lands inside the capture's read window."""
    runner = Runner(pause_seconds=0.6, take_lock=False)
    runner.start()
    assert runner.ddl_committed.wait(10)
    try:
        _, _, ledger, info = _capture(lock_wait_seconds=5, lock_poll_seconds=0.1, attempts=4)
    except capture.SourceChangedDuringCapture:
        ledger, info = None, None
    assert runner.finished.wait(10)
    if ledger is not None and ledger["rows"] == 1:
        pytest.xfail("legacy writer without the advisory lock: mismatched pair observable; migrate.sh takes the lock")
