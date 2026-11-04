"""One consistent capture of structure, source metadata and the migration ledger.

Used only when ``CATALOGUE_LEDGER_TABLE`` is configured. Without a ledger the
generator keeps its original two short reads (structure, then metadata), whose
only cross-read coupling is the non-fingerprinted server version.

With a ledger, structure from one database state must never be published with
ledger evidence from another. The capture therefore:

1. reads the ledger (its own short transaction);
2. opens one ``REPEATABLE READ, READ ONLY`` transaction and reads structure,
   source metadata and the ledger again inside that single snapshot;
3. reads the ledger once more after the snapshot closes;
4. accepts the capture only if the three ledger identities are equal, which
   proves no ledger-recorded migration committed between the first and the last
   read; otherwise it retries, and after ``attempts`` refuses to publish.

The snapshot makes direct ``pg_catalog`` reads and the ledger read see one state.
PostgreSQL's ``pg_get_*def()`` helpers consult the system cache rather than the
transaction snapshot, which is why the before/after ledger comparison, not the
snapshot alone, is the guard against a migration committing mid-capture. DDL
applied outside the migration runner is outside this guard, as documented.
"""
from __future__ import annotations

from typing import Any, Callable

import psycopg2
import psycopg2.extensions

import schema_catalogue_ledger as ledger_module


class SourceChangedDuringCapture(RuntimeError):
    """The migration ledger changed while structure was being captured; publication refused."""


def capture_with_ledger(
    connection_parameters: dict[str, str],
    schemas: list[str],
    ledger_table: str,
    *,
    introspector: Callable[[Any, list[str]], dict[str, Any]],
    metadata_reader: Callable[[Any], dict[str, Any]],
    connector: Callable[..., Any] | None = None,
    attempts: int = 3,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return ``(structure, metadata, migration_ledger, capture_info)`` from one consistent capture."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    connect = connector or psycopg2.connect
    history: list[tuple[Any, Any, Any]] = []
    for attempt in range(1, attempts + 1):
        connection = connect(**connection_parameters)
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
            connection.close()
        identities = (
            ledger_module.identity(before), ledger_module.identity(during), ledger_module.identity(after),
        )
        if identities[0] == identities[1] == identities[2]:
            return structure, metadata, during, {"attempts": attempt, "consistent": True}
        history.append(identities)
    heads = [[i.get("head_filename") if i else None for i in trio] for trio in history]
    raise SourceChangedDuringCapture(
        f"migration ledger {ledger_table} changed during capture on {attempts} consecutive attempts "
        f"(ledger heads seen before/during/after each attempt: {heads}); refusing to publish"
    )
