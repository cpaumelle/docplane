#!/usr/bin/env python3
"""Apply DocPlane schema migrations in strict ordinal order.

DocPlane owns one database and one migration history. The runner deliberately has no
"apply everything in a monorepo" mode and never discovers migrations outside this product.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import sys
from dataclasses import dataclass

import psycopg2
import psycopg2.extras


MIGRATION_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")
DEFAULT_DIR = pathlib.Path(__file__).resolve().parent.parent / "db" / "migrations"


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    ordinal: int
    filename: str
    path: pathlib.Path
    checksum: str
    sql: str


def discover(path: pathlib.Path) -> list[Migration]:
    if not path.is_dir():
        raise MigrationError(f"migration directory not found: {path}")
    migrations: list[Migration] = []
    ordinals: dict[int, str] = {}
    for candidate in sorted(path.iterdir(), key=lambda p: p.name):
        match = MIGRATION_RE.fullmatch(candidate.name)
        if not match:
            if candidate.suffix == ".sql":
                raise MigrationError(f"invalid migration filename: {candidate.name}")
            continue
        ordinal = int(match.group(1))
        if ordinal in ordinals:
            raise MigrationError(
                f"duplicate migration ordinal {ordinal:03d}: {ordinals[ordinal]}, {candidate.name}"
            )
        raw = candidate.read_bytes()
        try:
            sql = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MigrationError(f"migration is not UTF-8: {candidate.name}") from exc
        migrations.append(
            Migration(
                ordinal=ordinal,
                filename=candidate.name,
                path=candidate,
                checksum=hashlib.sha256(raw).hexdigest(),
                sql=sql,
            )
        )
        ordinals[ordinal] = candidate.name

    if not migrations:
        raise MigrationError("no migrations found")
    expected = list(range(migrations[0].ordinal, migrations[-1].ordinal + 1))
    actual = [migration.ordinal for migration in migrations]
    if actual != expected:
        raise MigrationError(f"migration ordinal gap: expected {expected}, found {actual}")
    return migrations


def connect():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "postgres"),
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ.get("DB_NAME", "docs"),
        user=os.environ.get("DB_USER", "docs"),
        password=os.environ.get("DB_PASS", ""),
        connect_timeout=int(os.environ.get("DB_CONNECT_TIMEOUT", "10")),
        cursor_factory=psycopg2.extras.DictCursor,
    )


def ensure_ledger(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS docplane")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS docplane.schema_migrations (
                ordinal       integer PRIMARY KEY,
                filename      text NOT NULL UNIQUE,
                checksum      text NOT NULL,
                applied_at    timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT schema_migrations_ordinal_nonnegative CHECK (ordinal >= 0),
                CONSTRAINT schema_migrations_checksum_sha256
                    CHECK (checksum ~ '^[0-9a-f]{64}$')
            )
            """
        )
    conn.commit()


def applied(conn) -> dict[int, dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ordinal, filename, checksum, applied_at "
            "FROM docplane.schema_migrations ORDER BY ordinal"
        )
        return {int(row["ordinal"]): dict(row) for row in cur.fetchall()}


def verify_history(migrations: list[Migration], history: dict[int, dict]) -> list[str]:
    """Verify the database history against this image's migrations.

    Rollback/forward-compatibility contract: DocPlane migrations are
    additive-only (new schemas, tables, defaulted/nullable columns, CHECK
    vocabularies extended by adding values). An OLDER image may therefore run
    safely against a database that a NEWER image has already advanced — the
    old code simply does not use the newer objects. Ordinals BEYOND this
    image's newest migration are tolerated and reported, never fatal, which
    is what makes restoring the previous image a real rollback path.

    Divergence is still fatal: an unknown ordinal at-or-below this image's
    newest migration, a filename mismatch, or a checksum mismatch means the
    database and this image share no consistent history.
    """
    known = {migration.ordinal: migration for migration in migrations}
    newest_known = migrations[-1].ordinal
    ahead: list[str] = []
    for ordinal, record in sorted(history.items()):
        migration = known.get(ordinal)
        if migration is None:
            if ordinal > newest_known:
                ahead.append(f"{ordinal:03d} ({record['filename']})")
                continue
            raise MigrationError(
                f"database carries unknown migration {ordinal:03d} ({record['filename']})"
            )
        if record["filename"] != migration.filename:
            raise MigrationError(
                f"migration filename drift at {ordinal:03d}: "
                f"database={record['filename']} source={migration.filename}"
            )
        if record["checksum"] != migration.checksum:
            raise MigrationError(
                f"migration checksum drift at {ordinal:03d} ({migration.filename})"
            )
    applied_ordinals = sorted(history)
    if applied_ordinals:
        expected = list(range(migrations[0].ordinal, max(applied_ordinals) + 1))
        if applied_ordinals != expected:
            raise MigrationError(f"database migration history has a gap: {applied_ordinals}")
    return ahead


def apply_pending(conn, migrations: list[Migration], *, dry_run: bool = False) -> list[str]:
    ensure_ledger(conn)
    history = applied(conn)
    ahead = verify_history(migrations, history)
    for name in ahead:
        print(f"AHEAD {name} (applied by a newer image; tolerated under the additive-schema contract)")
    pending = [migration for migration in migrations if migration.ordinal not in history]
    if dry_run:
        return [migration.filename for migration in pending]

    applied_names: list[str] = []
    for migration in pending:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext('docplane-schema-migrations'))")
                cur.execute(
                    "SELECT ordinal, filename, checksum FROM docplane.schema_migrations "
                    "WHERE ordinal = %s FOR UPDATE",
                    (migration.ordinal,),
                )
                existing = cur.fetchone()
                if existing:
                    if (
                        existing["filename"] != migration.filename
                        or existing["checksum"] != migration.checksum
                    ):
                        raise MigrationError(
                            f"migration {migration.ordinal:03d} was applied differently by another runner"
                        )
                    conn.rollback()
                    continue
                cur.execute(migration.sql)
                cur.execute(
                    "INSERT INTO docplane.schema_migrations (ordinal, filename, checksum) "
                    "VALUES (%s, %s, %s)",
                    (migration.ordinal, migration.filename, migration.checksum),
                )
            conn.commit()
            applied_names.append(migration.filename)
        except Exception:
            conn.rollback()
            raise
    return applied_names


def status(conn, migrations: list[Migration]) -> tuple[list[dict], list[str]]:
    """Rows for this image's migrations, plus the tolerated-ahead entries a
    newer image applied. Both surfaces must be reported: hiding the ahead
    list would make an upgraded database look identical to a current one."""
    ensure_ledger(conn)
    history = applied(conn)
    ahead = verify_history(migrations, history)
    rows = [
        {
            "ordinal": migration.ordinal,
            "filename": migration.filename,
            "checksum": migration.checksum,
            "applied": migration.ordinal in history,
            "applied_at": (
                history[migration.ordinal]["applied_at"].isoformat()
                if migration.ordinal in history
                else None
            ),
        }
        for migration in migrations
    ]
    return rows, ahead


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DocPlane schema migration runner")
    parser.add_argument("command", choices=("apply", "dry-run", "status", "verify"))
    parser.add_argument("--dir", type=pathlib.Path, default=DEFAULT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    migrations = discover(args.dir)
    with connect() as conn:
        if args.command == "apply":
            for name in apply_pending(conn, migrations):
                print(f"APPLIED {name}")
        elif args.command == "dry-run":
            for name in apply_pending(conn, migrations, dry_run=True):
                print(f"PENDING {name}")
        else:
            rows, ahead = status(conn, migrations)
            if args.command == "status":
                for row in rows:
                    state = "APPLIED" if row["applied"] else "PENDING"
                    print(f"{state} {row['filename']} {row['checksum']}")
                for name in ahead:
                    print(f"AHEAD {name} (applied by a newer image; tolerated under the additive-schema contract)")
            else:
                pending = [row["filename"] for row in rows if not row["applied"]]
                if pending:
                    raise MigrationError("pending migrations: " + ", ".join(pending))
                if ahead:
                    print(f"VERIFIED {len(rows)} migrations ({len(ahead)} ahead, applied by a newer image)")
                else:
                    print(f"VERIFIED {len(rows)} migrations")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationError as exc:
        print(f"MIGRATION_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
