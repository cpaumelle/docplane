#!/usr/bin/env python3
"""Authoritative-source projection for the schema catalogue — pure and shared.

This module owns the *source projection seam* only: reading STRUCTURE-ONLY
metadata from a PostgreSQL connection and reducing it to a deterministic
structural fingerprint. It is the single implementation of that projection.

The generator (``schema_catalogue.py``) imports :func:`introspect` and
:func:`fingerprint` from here; a future SCHEDULED schema *observer* is intended
to import the same seam so that observation and generation share one exact
notion of "the source structure" and one exact fingerprint algorithm.

Deliberate boundary — this module contains and imports **nothing** beyond the
source projection. It does not touch:

  * Markdown rendering, redaction, or DocPlane API mutation clients;
  * MODEL / CATALOGUES reconciliation, publication, or GENERATED ownership;
  * GENERATION evidence, observer scheduling, or runtime discovery;
  * credentials, environment loading, service or timer behaviour.

Because of that, importing this module has no side effects and cannot reach any
mutation-capable state: it opens no connections and reads no environment. The
caller supplies an already-open ``conn``; this module never creates one, so it
does not even import ``psycopg2``. Keeping the seam this narrow lets observer
code depend on the source projection without pulling in the generator's
mutation surface.

The projection is structure-only and deterministic. It covers the same current
semantics as the generator always has:

  * schemas (processed in a canonical, allowlist-order-independent order);
  * tables and their comments;
  * columns with types, nullability and defaults, in ordinal position;
  * primary-key, foreign-key and unique constraints (by name);
  * indexes (by name);

with all ordering fixed by the SQL below and the fingerprint canonicalised by
``json.dumps(..., sort_keys=True)`` so neither the schema allowlist order nor
the database row-return order can affect the result.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = ["introspect", "fingerprint"]


# ── Introspection: structure only, deterministic order, never row data ──────
#
# These queries return metadata only. Ordering is pinned inside SQL (ORDER BY)
# so a run is reproducible regardless of the database's physical row order; the
# fingerprint additionally sorts keys, so any residual ordering is irrelevant to
# the canonical output.

_TABLES_SQL = """
SELECT c.relname,
       obj_description(c.oid, 'pg_class')
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = %s AND c.relkind IN ('r', 'p')
 ORDER BY c.relname
"""

_COLUMNS_SQL = """
SELECT column_name, data_type, is_nullable, column_default
  FROM information_schema.columns
 WHERE table_schema = %s AND table_name = %s
 ORDER BY ordinal_position
"""

_CONSTRAINTS_SQL = """
SELECT con.contype, con.conname, pg_get_constraintdef(con.oid)
  FROM pg_constraint con
  JOIN pg_class c ON c.oid = con.conrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = %s AND c.relname = %s AND con.contype IN ('p', 'f', 'u')
 ORDER BY con.conname
"""

_INDEXES_SQL = """
SELECT indexname, indexdef
  FROM pg_indexes
 WHERE schemaname = %s AND tablename = %s
 ORDER BY indexname
"""


def introspect(conn, schemas: list[str]) -> dict[str, Any]:
    """Project the STRUCTURE-ONLY metadata of ``schemas`` from ``conn``.

    ``conn`` is an already-open DB-API connection supplied by the caller; this
    function never opens or configures a connection and reads no environment.
    The read runs in a READ ONLY transaction and returns only structural
    metadata (never row data). Schemas are processed in ``sorted`` order, so the
    allowlist's order does not affect the result.
    """
    structure: dict[str, Any] = {}
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    # Deterministic, role-independent DDL rendering. pg_get_constraintdef() and
    # column-default pg_get_expr() qualify object names RELATIVE TO search_path:
    # a role whose "$user" schema is catalogued (e.g. the generator's own
    # CATALOGUE_SOURCE_USER) renders those refs unqualified, while any other
    # role renders them schema-qualified — a different fingerprint for identical
    # structure (proven in SCHEMA_OBSERVER_PRIVILEGE_PROOF.md). Pinning
    # search_path to pg_catalog forces every user-schema reference to render
    # fully qualified, so the fingerprint is a function of structure alone, not
    # of the connecting role's name. This lets a future Schema observer share the
    # generator's exact fingerprint from its own least-privilege role.
    #
    # NOTE (fingerprint-moving): this changes today's output — the deployed
    # catalogue currently renders docs.* unqualified via the generator role's
    # "$user"=docs path. It is a projection-contract change: deploying it moves
    # the source fingerprint and re-renders every catalogue page with qualified
    # names, so the first post-deploy run regenerates the catalogue. Gate deploy
    # on that ratification/re-baseline; merging this code does not deploy it.
    cur.execute("SET search_path = pg_catalog")
    for schema in sorted(schemas):
        tables = {}
        cur.execute(_TABLES_SQL, (schema,))
        for table_name, comment in cur.fetchall():
            cur.execute(_COLUMNS_SQL, (schema, table_name))
            columns = [
                {
                    "name": name,
                    "type": data_type,
                    "nullable": nullable == "YES",
                    "default": default,
                }
                for name, data_type, nullable, default in cur.fetchall()
            ]
            cur.execute(_CONSTRAINTS_SQL, (schema, table_name))
            constraints = [
                {"kind": kind, "name": name, "definition": definition}
                for kind, name, definition in cur.fetchall()
            ]
            cur.execute(_INDEXES_SQL, (schema, table_name))
            indexes = [
                {"name": name, "definition": definition}
                for name, definition in cur.fetchall()
            ]
            tables[table_name] = {
                "comment": comment,
                "columns": columns,
                "constraints": constraints,
                "indexes": indexes,
            }
        structure[schema] = tables
    return structure


def fingerprint(structure: dict[str, Any]) -> str:
    """Canonical SHA-256 of the source structure.

    Canonicalised with ``sort_keys=True`` so neither schema allowlist order nor
    database row-return order changes the digest. This is the exact fingerprint
    algorithm the generator has always used; it is intentionally unversioned
    here — its stability across the extraction is a behaviour-preservation
    contract (see ``test_schema_catalogue_source.py``).
    """
    canonical = json.dumps(structure, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
