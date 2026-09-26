#!/usr/bin/env python3
"""Authoritative-source projection for the schema catalogue — pure and shared.

This module owns the *source projection seam* only: reading STRUCTURE-ONLY
metadata from a PostgreSQL connection and reducing it to a deterministic
structural fingerprint. It is the single implementation of that projection.

The generator (``schema_catalogue.py``) and source-only observer
(``schema_catalogue_observer.py``) both import :func:`introspect` and
:func:`fingerprint` from here, so observation and generation share one exact
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
does not even import ``psycopg2``.

Source projection contract 2 (e004787b decision D1, 2026-09-26)
---------------------------------------------------------------
The structure is the detailed schema graph that the generator publishes as a
machine-readable projection, not only the input to Markdown rendering:

  * per schema: comment, ``tables``, ``views`` and ``enums``;
  * per table: kind, comment, columns, primary key, foreign keys as exact
    column pairs with their referenced schema/table/columns and actions,
    unique, CHECK and exclusion constraints, and indexes with their key
    columns, INCLUDE columns, method and predicate;
  * per column: rendered type, ``udt`` (qualified outside ``pg_catalog``),
    array and enum element identity, nullability, default, identity,
    generated expression and comment;
  * per view: kind, comment, columns and definition.

Everything is read from ``pg_catalog``, never ``information_schema``. The
latter hides columns from roles lacking column privileges, which made the
v1 fingerprint depend on the connecting role (SCHEMA_OBSERVER_PRIVILEGE_PROOF
H2). The read also pins ``search_path`` to ``pg_catalog`` for its own
transaction, so ``pg_get_constraintdef`` / ``pg_get_expr`` / ``format_type``
schema-qualify every non-catalog name regardless of the connecting role's
search path (the #174 "new finding"). Both changes make the fingerprint a
property of the structure alone.

Row data is never read. List order is fixed by explicit ``ORDER BY`` clauses;
mapping-key order is canonicalised by ``json.dumps(..., sort_keys=True)``.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = ["introspect", "fingerprint"]

SOURCE_PROJECTION_CONTRACT = 2

_RELATION_KINDS = {
    "r": "table",
    "p": "partitioned_table",
    "v": "view",
    "m": "materialized_view",
}
_FK_ACTIONS = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}
_IDENTITY = {"a": "ALWAYS", "d": "BY DEFAULT"}


# ── Introspection: structure only, deterministic order, never row data ──────

_SCHEMA_SQL = """
SELECT obj_description(n.oid, 'pg_namespace')
  FROM pg_namespace n
 WHERE n.nspname = %s
"""

_RELATIONS_SQL = """
SELECT c.oid, c.relname::text, c.relkind::text, obj_description(c.oid, 'pg_class')
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm')
 ORDER BY c.relname
"""

_ATTRIBUTES_SQL = """
SELECT a.attname::text,
       format_type(a.atttypid, a.atttypmod),
       t.typname::text, tn.nspname::text, t.typtype::text,
       t.typcategory = 'A' AND t.typelem <> 0,
       et.typname::text, etn.nspname::text, et.typtype::text,
       NOT a.attnotnull,
       pg_get_expr(d.adbin, d.adrelid),
       a.attidentity::text, a.attgenerated::text,
       col_description(a.attrelid, a.attnum)
  FROM pg_attribute a
  JOIN pg_type t ON t.oid = a.atttypid
  JOIN pg_namespace tn ON tn.oid = t.typnamespace
  LEFT JOIN pg_type et ON et.oid = t.typelem AND t.typcategory = 'A'
  LEFT JOIN pg_namespace etn ON etn.oid = et.typnamespace
  LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
 WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
 ORDER BY a.attnum
"""

_CONSTRAINTS_SQL = """
SELECT con.conname::text, con.contype::text, pg_get_constraintdef(con.oid),
       ARRAY(SELECT a.attname::text
               FROM unnest(con.conkey) WITH ORDINALITY AS k(num, ord)
               JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.num
              ORDER BY k.ord),
       fn.nspname::text, fc.relname::text,
       ARRAY(SELECT a.attname::text
               FROM unnest(con.confkey) WITH ORDINALITY AS k(num, ord)
               JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.num
              ORDER BY k.ord),
       con.confupdtype::text, con.confdeltype::text,
       con.condeferrable, con.condeferred
  FROM pg_constraint con
  LEFT JOIN pg_class fc ON fc.oid = con.confrelid
  LEFT JOIN pg_namespace fn ON fn.oid = fc.relnamespace
 WHERE con.conrelid = %s AND con.contype IN ('p', 'f', 'u', 'c', 'x')
 ORDER BY con.conname
"""

_INDEXES_SQL = """
SELECT ic.relname::text, i.indisunique, i.indisprimary, am.amname::text,
       pg_get_indexdef(i.indexrelid),
       pg_get_expr(i.indpred, i.indrelid),
       ARRAY(SELECT CASE WHEN k.num = 0
                         THEN pg_get_indexdef(i.indexrelid, k.ord::int, true)
                         ELSE a.attname::text END
               FROM unnest(i.indkey::int2[]) WITH ORDINALITY AS k(num, ord)
               LEFT JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.num
              WHERE k.ord <= i.indnkeyatts
              ORDER BY k.ord),
       ARRAY(SELECT a.attname::text
               FROM unnest(i.indkey::int2[]) WITH ORDINALITY AS k(num, ord)
               JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.num
              WHERE k.ord > i.indnkeyatts
              ORDER BY k.ord)
  FROM pg_index i
  JOIN pg_class ic ON ic.oid = i.indexrelid
  JOIN pg_am am ON am.oid = ic.relam
 WHERE i.indrelid = %s
 ORDER BY ic.relname
"""

_VIEW_SQL = "SELECT pg_get_viewdef(%s::oid, true)"

_ENUMS_SQL = """
SELECT t.typname::text,
       ARRAY(SELECT e.enumlabel::text FROM pg_enum e
              WHERE e.enumtypid = t.oid ORDER BY e.enumsortorder)
  FROM pg_type t
  JOIN pg_namespace n ON n.oid = t.typnamespace
 WHERE n.nspname = %s AND t.typtype = 'e'
 ORDER BY t.typname
"""


def _qualified(namespace: str | None, name: str | None) -> str | None:
    if name is None:
        return None
    return name if namespace in (None, "pg_catalog") else f"{namespace}.{name}"


def _columns(cur, oid) -> list[dict[str, Any]]:
    cur.execute(_ATTRIBUTES_SQL, (oid,))
    columns = []
    for (
        name, rendered, typname, typns, typtype, is_array,
        elem_name, elem_ns, elem_type, nullable, default, identity, generated, comment,
    ) in cur.fetchall():
        enum = None
        if typtype == "e":
            enum = _qualified(typns, typname)
        elif is_array and elem_type == "e":
            enum = _qualified(elem_ns, elem_name)
        columns.append(
            {
                "name": name,
                "type": rendered,
                "udt": _qualified(typns, typname),
                "array": bool(is_array),
                "enum": enum,
                "nullable": bool(nullable),
                # A stored generated column keeps its expression in pg_attrdef;
                # it is a generation expression, not a default.
                "default": None if generated == "s" else default,
                "identity": _IDENTITY.get(identity or ""),
                "generated": default if generated == "s" else None,
                "comment": comment,
            }
        )
    return columns


def _constraints(cur, oid) -> dict[str, Any]:
    cur.execute(_CONSTRAINTS_SQL, (oid,))
    result: dict[str, Any] = {
        "primary_key": None,
        "foreign_keys": [],
        "unique": [],
        "checks": [],
        "exclusions": [],
    }
    for (
        name, kind, definition, columns, ref_schema, ref_table, ref_columns,
        on_update, on_delete, deferrable, deferred,
    ) in cur.fetchall():
        columns = list(columns or [])
        if kind == "p":
            result["primary_key"] = {"name": name, "columns": columns}
        elif kind == "f":
            result["foreign_keys"].append(
                {
                    "name": name,
                    "columns": columns,
                    "references": {
                        "schema": ref_schema,
                        "table": ref_table,
                        "columns": list(ref_columns or []),
                    },
                    "on_update": _FK_ACTIONS.get(on_update or "", on_update),
                    "on_delete": _FK_ACTIONS.get(on_delete or "", on_delete),
                    "deferrable": bool(deferrable),
                    "initially_deferred": bool(deferred),
                    "definition": definition,
                }
            )
        elif kind == "u":
            result["unique"].append({"name": name, "columns": columns, "definition": definition})
        elif kind == "c":
            result["checks"].append({"name": name, "columns": columns, "definition": definition})
        elif kind == "x":
            result["exclusions"].append({"name": name, "definition": definition})
    return result


def _indexes(cur, oid) -> list[dict[str, Any]]:
    cur.execute(_INDEXES_SQL, (oid,))
    return [
        {
            "name": name,
            "unique": bool(unique),
            "primary": bool(primary),
            "method": method,
            "columns": list(columns or []),
            "include": list(include or []),
            "predicate": predicate,
            "definition": definition,
        }
        for name, unique, primary, method, definition, predicate, columns, include in cur.fetchall()
    ]


def introspect(conn, schemas: list[str]) -> dict[str, Any]:
    """Project the STRUCTURE-ONLY metadata of ``schemas`` from ``conn``.

    ``conn`` is an already-open DB-API connection supplied by the caller; this
    function never opens or configures a connection and reads no environment.
    The read runs in a READ ONLY transaction whose ``search_path`` is pinned to
    ``pg_catalog`` and returns only catalogue metadata (never row data).
    Schemas are processed in ``sorted`` order, so the allowlist's order does
    not affect the result.
    """
    structure: dict[str, Any] = {}
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    cur.execute("SET LOCAL search_path TO pg_catalog")
    for schema in sorted(schemas):
        cur.execute(_SCHEMA_SQL, (schema,))
        row = cur.fetchone()
        schema_comment = row[0] if row else None
        cur.execute(_RELATIONS_SQL, (schema,))
        relations = cur.fetchall()
        tables: dict[str, Any] = {}
        views: dict[str, Any] = {}
        for oid, name, relkind, comment in relations:
            kind = _RELATION_KINDS[relkind]
            columns = _columns(cur, oid)
            if kind in {"view", "materialized_view"}:
                cur.execute(_VIEW_SQL, (oid,))
                definition = cur.fetchone()[0]
                views[name] = {
                    "kind": kind,
                    "comment": comment,
                    "columns": columns,
                    "definition": definition,
                    "indexes": _indexes(cur, oid) if kind == "materialized_view" else [],
                }
                continue
            tables[name] = {
                "kind": kind,
                "comment": comment,
                "columns": columns,
                **_constraints(cur, oid),
                "indexes": _indexes(cur, oid),
            }
        cur.execute(_ENUMS_SQL, (schema,))
        enums = {name: list(labels or []) for name, labels in cur.fetchall()}
        structure[schema] = {
            "comment": schema_comment,
            "tables": tables,
            "views": views,
            "enums": enums,
        }
    return structure


def fingerprint(structure: dict[str, Any]) -> str:
    """Canonical SHA-256 of the source structure.

    Canonicalised with ``sort_keys=True`` so mapping insertion order does not
    change the digest. Ordered list values remain significant and are made
    deterministic by ``introspect()`` and its SQL ``ORDER BY`` clauses. The
    algorithm itself is unchanged from contract 1; contract 2 changed only the
    structure it is applied to.
    """
    canonical = json.dumps(structure, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
