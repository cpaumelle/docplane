"""One consistent capture of structure, source metadata and the migration ledger.

Used only when ``CATALOGUE_LEDGER_TABLE`` is configured. Without a ledger the
generator keeps its original two short reads (structure, then metadata), whose
only cross-read coupling is the non-fingerprinted server version.

With a ledger, structure from one database state must never be published with
ledger evidence from another. Trevarn's migration runner (``db/migrate.sh``)
commits a migration's DDL and then records its ledger row; between those two
commits the database holds new structure with the old ledger, and no number of
ledger re-reads can tell. The runner therefore holds a PostgreSQL **advisory
lock** on the ledger across the DDL and its ledger row, and this capture takes
the **shared** form of the same lock for its whole read:

    key = hashtext('<ledger table>')::bigint          -- identical on both sides
    runner : SELECT pg_advisory_lock(key)             -- exclusive, whole psql session
    capture: SELECT pg_try_advisory_lock_shared(key)  -- shared, whole capture

While a migration holds the exclusive lock the capture cannot acquire the shared
one: it polls for a bounded time, then refuses (``MigrationInProgress``). While a
capture holds the shared lock a migration waits a few seconds. Inside the lock the
capture still:

1. reads the ledger (its own short transaction);
2. opens one ``REPEATABLE READ, READ ONLY`` transaction and reads structure,
   source metadata and the ledger again inside that single snapshot;
3. reads the ledger once more after the snapshot closes;
4. accepts the capture only if the three ledger identities are equal (belt and
   braces for a writer that bypasses the runner's lock), otherwise retries on a
   fresh connection and after ``attempts`` refuses (``SourceChangedDuringCapture``).

A ledger that is ``UNAVAILABLE`` (the role cannot read it) or ``EMPTY`` is never
"consistent": the structure may still be published, but ``capture_info`` says
``consistent=False`` and names the reason, and the provenance carries it, so a
missing ledger can never certify a structure/ledger pair.

The snapshot makes direct ``pg_catalog`` reads and the ledger read see one state.
PostgreSQL's ``pg_get_*def()`` helpers consult the system cache rather than the
transaction snapshot, which is one more reason the lock, not the snapshot alone,
is the guard. DDL applied outside the migration runner is outside this guard, as
documented in the runbook.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import psycopg2
import psycopg2.extensions

import schema_catalogue_ledger as ledger_module

GUARD = "advisory-shared-lock+repeatable-read+ledger-compare"
_TRY_SHARED = "SELECT pg_try_advisory_lock_shared(hashtext(%s)::bigint)"
_UNLOCK_SHARED = "SELECT pg_advisory_unlock_shared(hashtext(%s)::bigint)"


class SourceChangedDuringCapture(RuntimeError):
    """The migration ledger changed while structure was being captured; publication refused."""


class MigrationInProgress(RuntimeError):
    """A migration held the ledger's advisory lock for the whole wait budget; publication refused."""


def _acquire_shared_lock(connection, ledger_table: str, wait_seconds: float, poll_seconds: float, sleeper) -> float:
    """Poll ``pg_try_advisory_lock_shared`` until acquired or the budget is spent."""
    deadline = time.monotonic() + max(wait_seconds, 0.0)
    waited = 0.0
    while True:
        cur = connection.cursor()
        cur.execute(_TRY_SHARED, (ledger_table,))
        acquired = bool(cur.fetchone()[0])
        connection.commit()
        if acquired:
            return waited
        if time.monotonic() >= deadline:
            raise MigrationInProgress(
                f"a migration holds the ledger lock for {ledger_table}; waited {waited:.1f}s; refusing to publish"
            )
        sleeper(poll_seconds)
        waited += poll_seconds


def _release_shared_lock(connection, ledger_table: str) -> None:
    try:
        cur = connection.cursor()
        cur.execute(_UNLOCK_SHARED, (ledger_table,))
        connection.commit()
    except Exception:  # the session close releases it anyway
        pass


def capture_with_ledger(
    connection_parameters: dict[str, str],
    schemas: list[str],
    ledger_table: str,
    *,
    introspector: Callable[[Any, list[str]], dict[str, Any]],
    metadata_reader: Callable[[Any], dict[str, Any]],
    connector: Callable[..., Any] | None = None,
    attempts: int = 3,
    lock_wait_seconds: float = 60.0,
    lock_poll_seconds: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return ``(structure, metadata, migration_ledger, capture_info)`` from one guarded capture.

    ``capture_info`` = ``{"guard", "attempts", "lock_wait_seconds", "consistent", ["reason"]}``.
    ``consistent`` is True only when all three ledger reads are ``CAPTURED`` and identical.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    connect = connector or psycopg2.connect
    history: list[tuple[Any, Any, Any]] = []
    for attempt in range(1, attempts + 1):
        connection = connect(**connection_parameters)
        try:
            waited = _acquire_shared_lock(connection, ledger_table, lock_wait_seconds, lock_poll_seconds, sleeper)
            try:
                before = ledger_module.capture_ledger(connection, ledger_table)
                connection.commit()
                connection.set_session(
                    isolation_level=psycopg2.extensions.ISOLATION_LEVEL_REPEATABLE_READ, readonly=True,
                )
                structure = introspector(connection, schemas)
                metadata = metadata_reader(connection)
                during = ledger_module.capture_ledger(connection, ledger_table)
                connection.commit()
                connection.set_session(
                    isolation_level=psycopg2.extensions.ISOLATION_LEVEL_DEFAULT, readonly=False,
                )
                after = ledger_module.capture_ledger(connection, ledger_table)
                connection.commit()
            finally:
                _release_shared_lock(connection, ledger_table)
        finally:
            connection.close()
        identities = (
            ledger_module.identity(before), ledger_module.identity(during), ledger_module.identity(after),
        )
        if identities[0] == identities[1] == identities[2]:
            info: dict[str, Any] = {
                "guard": GUARD, "attempts": attempt, "lock_wait_seconds": round(waited, 3),
            }
            if during.get("status") == "CAPTURED":
                info["consistent"] = True
            else:
                info["consistent"] = False
                info["reason"] = (
                    f"ledger {during.get('status')}: structure is published without ledger evidence; "
                    "this capture does not certify a structure/ledger pair"
                )
            return structure, metadata, during, info
        history.append(identities)
    heads = [[i.get("head_filename") if i else None for i in trio] for trio in history]
    raise SourceChangedDuringCapture(
        f"migration ledger {ledger_table} changed during capture on {attempts} consecutive attempts "
        f"(ledger heads seen before/during/after each attempt: {heads}); refusing to publish"
    )
