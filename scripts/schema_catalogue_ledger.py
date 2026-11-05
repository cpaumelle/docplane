"""Optional migration-ledger identity for the schema catalogue (contract 2, additive).

Contract 2 records ``provenance.migration_ledger`` as ``NOT_CAPTURED`` because the
least-privilege source contract reads ``pg_catalog`` only. Some sources keep a
small, data-free migration ledger (Trevarn: ``public.schema_migrations`` with
``filename``, ``sha256``, ``applied_at``) whose identity *is* the deployed-release
identity an operator needs next to the structure. This module captures it when,
and only when, the operator configures ``CATALOGUE_LEDGER_TABLE`` and the source
role has been granted ``SELECT`` on exactly that table.

Rules:

* opt-in per catalogue (``CATALOGUE_LEDGER_TABLE=<schema>.<table>``); unset means
  the unchanged ``NOT_CAPTURED`` provenance and an unchanged render identity, so
  existing catalogues see no churn;
* the ledger is never part of the structural fingerprint (the observer's role
  reads no rows and its FRESHNESS_CHECK must stay structure-only); it is folded
  into the render identity instead, so a ledger-only change (a data migration)
  republishes the overview and projection without pretending the structure moved;
* a refused or missing table is recorded as ``UNAVAILABLE`` with the error class
  and the run continues: the structure is still authoritative evidence;
* only ``filename`` and ``sha256`` take part in the digest; ``applied_at`` is
  reported for the head row and never hashed;
* digests are published as 24-hex (96-bit) prefixes: DocPlane's projection store
  refuses any 40+ character hex value as secret-shaped (``PROJECTION_SECRET_SHAPED``),
  and a prefix is an identity, not a credential. The full digest is reproducible
  from the ledger by anyone who can read it.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

import psycopg2

LEDGER_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")

NOT_CAPTURED: dict[str, Any] = {
    "status": "NOT_CAPTURED",
    "reason": (
        "Ledger identity requires a row read of the migration ledger; the "
        "least-privilege source contract reads catalogue metadata only."
    ),
}

# Published digest length. 24 hex characters (96 bits) sit below the projection
# store's 40-character secret-shape threshold and are ample for identity comparison.
DIGEST_PREFIX = 24


def ledger_table_setting(value: str | None) -> str | None:
    """Validate ``CATALOGUE_LEDGER_TABLE``; ``None``/blank disables capture."""
    table = (value or "").strip()
    if not table:
        return None
    if not LEDGER_TABLE_RE.match(table):
        raise RuntimeError(
            "CATALOGUE_LEDGER_TABLE must be <schema>.<table> in lowercase identifiers"
        )
    return table


def ledger_digest(rows: list[tuple[str, str]]) -> str:
    """24-hex prefix of SHA-256 over ``filename<TAB>sha256`` lines in C-collation filename order."""
    lines = sorted(f"{filename}\t{sha}" for filename, sha in rows)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:DIGEST_PREFIX]


def identity(ledger: dict[str, Any] | None) -> dict[str, Any] | None:
    """The part of a capture that shapes the render identity (never applied_at)."""
    if ledger is None:
        return None
    keep = {"status", "table", "rows", "head_filename", "ledger_set_digest_prefix", "error_class"}
    return {key: ledger[key] for key in sorted(ledger) if key in keep}


def capture_ledger(conn, table: str) -> dict[str, Any]:
    """Read the ledger identity from ``conn`` in a read-only transaction.

    ``conn`` is an already-open DB-API connection owned by the caller. The read
    uses the same transaction posture as introspection (READ ONLY, search_path
    pinned to ``pg_catalog``) and a schema-qualified, quoted identifier that was
    validated by ``ledger_table_setting``.
    """
    schema, name = table.split(".", 1)
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    cur.execute("SET LOCAL search_path TO pg_catalog")
    try:
        cur.execute(
            f'SELECT filename::text, sha256::text, applied_at '
            f'FROM "{schema}"."{name}" ORDER BY filename COLLATE "C"'
        )
        rows = cur.fetchall()
    except psycopg2.Error as exc:
        conn.rollback()
        return {
            "status": "UNAVAILABLE",
            "table": table,
            "error_class": type(exc).__name__,
            "reason": (
                "The configured ledger table could not be read by the source role; "
                "structure remains authoritative, ledger identity is not captured."
            ),
        }
    if not rows:
        return {"status": "EMPTY", "table": table, "rows": 0}
    head_filename, head_sha, head_applied = rows[-1]
    return {
        "status": "CAPTURED",
        "table": table,
        "rows": len(rows),
        "head_filename": head_filename,
        "head_sha256_prefix": str(head_sha)[:DIGEST_PREFIX],
        "head_applied_at": head_applied.isoformat() if head_applied is not None else None,
        "ledger_set_digest_prefix": ledger_digest([(row[0], row[1]) for row in rows]),
        "digest_prefix_hex": DIGEST_PREFIX,
        "identity_note": "truncated comparison identifiers, not integrity hashes",
    }


def summary_line(ledger: dict[str, Any] | None) -> str:
    """One Markdown bullet for the catalogue overview page."""
    if not ledger or ledger.get("status") == "NOT_CAPTURED":
        return "- Migration-ledger identity: not captured by this projection contract."
    status = ledger.get("status")
    table = ledger.get("table", "?")
    if status == "CAPTURED":
        return (
            f"- Migration-ledger identity: `{table}` head `{ledger['head_filename']}` "
            f"({ledger['rows']} rows, set digest prefix `{ledger['ledger_set_digest_prefix']}`, "
            f"{ledger.get('digest_prefix_hex', DIGEST_PREFIX)} hex). Truncated comparison identifiers, "
            "not the release manifest's integrity hashes. The ledger shapes the render identity, not the "
            "structural fingerprint; the observer verifies structure only."
        )
    if status == "EMPTY":
        return f"- Migration-ledger identity: `{table}` is present but holds no rows."
    return (
        f"- Migration-ledger identity: `{table}` configured but UNAVAILABLE "
        f"({ledger.get('error_class', 'unknown')}); structure remains authoritative."
    )
