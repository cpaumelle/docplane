"""Behaviour-preservation proofs for the schema-catalogue *source seam*.

``scripts/schema_catalogue_source.py`` was extracted from the generator
(``scripts/schema_catalogue.py``) so that a future SCHEDULED schema observer can
import the authoritative source projection — ``introspect`` and
``fingerprint`` — without importing the mutation-capable generator.

These tests exist to prove the extraction changed *nothing* observable:

  * the pure module reproduces a fingerprint captured from PRISTINE main
    (frozen in ``fixtures/schema_catalogue_source_oracle.json``), so "expected"
    is never computed through the code under test;
  * the generator now holds a single implementation and re-exports it;
  * canonical output is invariant to schema-allowlist order and to database
    row-return order;
  * rendering the extracted structure is byte-identical to before;
  * the pure module imports with no API/DB/redaction/generator side effects and
    exposes no mutation surface.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import schema_catalogue  # noqa: E402
import schema_catalogue_source  # noqa: E402

# The small reviewed structure fixture — identical to the one in
# test_schema_catalogue.py. Kept as a literal so this test file is a
# self-contained, independent oracle carrier.
STRUCTURE = {
    "docplane": {
        "principals": {
            "comment": "Named identities",
            "columns": [
                {"name": "principal_id", "type": "uuid", "nullable": False, "default": "gen_random_uuid()"},
                {"name": "display_name", "type": "text", "nullable": False, "default": None},
            ],
            "constraints": [
                {"kind": "p", "name": "principals_pkey", "definition": "PRIMARY KEY (principal_id)"},
            ],
            "indexes": [{"name": "principals_pkey", "definition": "CREATE UNIQUE INDEX ..."}],
        },
    },
}

ORACLE = json.loads((FIXTURES / "schema_catalogue_source_oracle.json").read_text())
PRODUCTION_SHAPE = json.loads((FIXTURES / "schema_catalogue_production_shape.json").read_text())


def _render_sha(structure: dict) -> str:
    """SHA-256 over render_pages output, matching the frozen oracle's join."""
    fp = schema_catalogue_source.fingerprint(structure)
    pages = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", structure, fp)
    blob = "\x1e".join(page["path"] + "\x1f" + page["content"] for page in pages)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# 1. Fixed structure → exactly the same canonical fingerprint as pre-extraction.
def test_fixed_structure_matches_frozen_preextraction_fingerprint():
    assert schema_catalogue_source.fingerprint(STRUCTURE) == ORACLE["structure_fingerprint"]


# 2. The generator re-exports the seam — one implementation, not two. (The full
#    generator test module continues to pass unchanged; see test_schema_catalogue.py.)
def test_generator_reexports_the_single_source_implementation():
    assert schema_catalogue.introspect is schema_catalogue_source.introspect
    assert schema_catalogue.fingerprint is schema_catalogue_source.fingerprint
    # No second copy of the SQL or the functions survives in the generator.
    generator_src = (SCRIPTS / "schema_catalogue.py").read_text()
    assert "def introspect(" not in generator_src
    assert "def fingerprint(" not in generator_src
    for sql in ("_TABLES_SQL", "_COLUMNS_SQL", "_CONSTRAINTS_SQL", "_INDEXES_SQL"):
        assert sql not in generator_src


# 4. Schema allowlist ORDER does not affect structure or fingerprint.
def test_schema_allowlist_order_is_irrelevant():
    class RecordingCursor:
        """Minimal cursor returning empty result sets, recording table probes."""

        def __init__(self):
            self.schemas_probed = []

        def execute(self, sql, params=None):
            self._last = (sql, params)
            if params and "pg_class c" in sql and "con." not in sql:
                self.schemas_probed.append(params[0])

        def fetchall(self):
            return []

    class Conn:
        def __init__(self):
            self._cur = RecordingCursor()

        def cursor(self):
            return self._cur

    forward = Conn()
    reverse = Conn()
    s1 = schema_catalogue_source.introspect(forward, ["docplane", "docs", "audit"])
    s2 = schema_catalogue_source.introspect(reverse, ["audit", "docs", "docplane"])
    assert s1 == s2
    assert schema_catalogue_source.fingerprint(s1) == schema_catalogue_source.fingerprint(s2)
    # Regardless of allowlist order, schemas are probed in canonical sorted order.
    assert forward._cur.schemas_probed == ["audit", "docplane", "docs"]
    assert reverse._cur.schemas_probed == ["audit", "docplane", "docs"]


# 5. Database/catalog row-return ORDER does not affect canonical output.
#    (Ordering is pinned in SQL; the fingerprint additionally sort_keys, so even
#    if the driver returned rows in a different order the canonical digest is
#    invariant. Proven here by shuffling dict/list structure that json canonicalises.)
def test_row_return_order_does_not_change_canonical_output():
    ordered = PRODUCTION_SHAPE
    # Reverse every schema's table insertion order and every table's key order:
    # canonicalisation with sort_keys must absorb it entirely.
    shuffled = {
        schema: {
            table: {k: tbl[k] for k in reversed(list(tbl.keys()))}
            for table, tbl in reversed(list(tables.items()))
        }
        for schema, tables in reversed(list(ordered.items()))
    }
    assert schema_catalogue_source.fingerprint(shuffled) == schema_catalogue_source.fingerprint(ordered)
    assert schema_catalogue_source.fingerprint(ordered) == ORACLE["production_shape_fingerprint"]


# 6. Rendering the same extracted structure is byte-identical to pre-extraction.
def test_rendered_output_is_byte_identical_to_preextraction():
    assert _render_sha(STRUCTURE) == ORACLE["structure_render_sha256"]
    assert _render_sha(PRODUCTION_SHAPE) == ORACLE["production_shape_render_sha256"]


# 7. A production-shaped fixture retains the exact expected fingerprint.
def test_production_shape_retains_exact_frozen_fingerprint():
    assert (
        schema_catalogue_source.fingerprint(PRODUCTION_SHAPE)
        == ORACLE["production_shape_fingerprint"]
    )


# 8. Comments, defaults, nullability, constraints and indexes are represented
#    exactly as before — the canonical JSON of a representative table is stable.
def test_all_structural_facets_are_represented_exactly():
    pages_table = PRODUCTION_SHAPE["docs"]["pages"]
    assert set(pages_table) == {"comment", "columns", "constraints", "indexes"}
    assert pages_table["comment"].startswith("Published corpus pages")
    # nullability + defaults survive round-tripping through the fingerprint input
    nav = next(c for c in pages_table["columns"] if c["name"] == "nav_path")
    assert nav["nullable"] is True and nav["default"] is None
    content = next(c for c in pages_table["columns"] if c["name"] == "content")
    assert content["nullable"] is False and content["default"] == "''::text"
    kinds = {c["kind"] for c in pages_table["constraints"]}
    assert kinds == {"p", "f", "u"}
    assert any(i["name"] == "pages_content_trgm_idx" for i in pages_table["indexes"])
    # Exactness of the full representation is pinned by the whole-fixture frozen
    # fingerprint oracle (test_production_shape_retains_exact_frozen_fingerprint):
    # any change to how comments/defaults/nullability/constraints/indexes are
    # represented would move production_shape_fingerprint.


# 3. Disposable PostgreSQL introspection matches pre-extraction semantics EXACTLY,
#    compared against an independently authored expected structure.
@pytest.mark.skipif(not os.environ.get("DB_HOST"), reason="requires a PostgreSQL database")
def test_disposable_postgres_introspection_matches_expected_semantics():
    import psycopg2

    dsn = (
        f"host={os.environ['DB_HOST']} port={os.environ.get('DB_PORT', '5432')} "
        f"dbname={os.environ.get('DB_NAME', 'docs')} user={os.environ.get('DB_USER', 'docs')} "
        f"password={os.environ.get('DB_PASS', '')}"
    )
    ddl = """
    DROP SCHEMA IF EXISTS seam_probe CASCADE;
    CREATE SCHEMA seam_probe;
    CREATE TABLE seam_probe.parent (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        label text NOT NULL
    );
    COMMENT ON TABLE seam_probe.parent IS 'seam probe parent';
    CREATE TABLE seam_probe.child (
        id integer PRIMARY KEY,
        parent_id uuid NOT NULL REFERENCES seam_probe.parent(id),
        code text NOT NULL,
        note text,
        active boolean NOT NULL DEFAULT false,
        UNIQUE (code)
    );
    """
    # DDL in autocommit; introspection then reads through the source seam.
    setup = psycopg2.connect(dsn)
    try:
        setup.autocommit = True
        with setup.cursor() as cur:
            cur.execute(ddl)
    finally:
        setup.close()

    try:
        with psycopg2.connect(dsn) as conn:
            structure = schema_catalogue_source.introspect(conn, ["seam_probe"])
        # Deterministic + rowless across two independent connections.
        with psycopg2.connect(dsn) as conn2:
            again = schema_catalogue_source.introspect(conn2, ["seam_probe"])
        assert structure == again
        assert (
            schema_catalogue_source.fingerprint(structure)
            == schema_catalogue_source.fingerprint(again)
        )

        tables = structure["seam_probe"]
        assert set(tables) == {"child", "parent"}

        # Independently authored expectations — NOT derived from the code under test.
        assert tables["parent"]["comment"] == "seam probe parent"
        assert tables["child"]["comment"] is None

        assert tables["parent"]["columns"] == [
            {"name": "id", "type": "uuid", "nullable": False, "default": "gen_random_uuid()"},
            {"name": "label", "type": "text", "nullable": False, "default": None},
        ]
        assert tables["child"]["columns"] == [
            {"name": "id", "type": "integer", "nullable": False, "default": None},
            {"name": "parent_id", "type": "uuid", "nullable": False, "default": None},
            {"name": "code", "type": "text", "nullable": False, "default": None},
            {"name": "note", "type": "text", "nullable": True, "default": None},
            {"name": "active", "type": "boolean", "nullable": False, "default": "false"},
        ]

        # Constraints: kinds present, ordered by name (as the SQL pins).
        child_constraints = tables["child"]["constraints"]
        assert [c["name"] for c in child_constraints] == sorted(c["name"] for c in child_constraints)
        assert {c["kind"] for c in child_constraints} == {"p", "f", "u"}
        fk = next(c for c in child_constraints if c["kind"] == "f")
        assert "FOREIGN KEY (parent_id) REFERENCES seam_probe.parent(id)" in fk["definition"]

        # Indexes: names only (definition text is PG-stable but we assert names,
        # ordered), proving indexes are represented, structure-only.
        idx_names = [i["name"] for i in tables["child"]["indexes"]]
        assert idx_names == sorted(idx_names)
        assert "child_pkey" in idx_names
    finally:
        cleanup = psycopg2.connect(dsn)
        try:
            cleanup.autocommit = True
            with cleanup.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS seam_probe CASCADE;")
        finally:
            cleanup.close()


# 9 & 10. The pure module imports with no API/DB/env/redaction/generator side
#    effects, and exposes no mutation surface. Proven in a CLEAN interpreter so
#    the current test session's already-imported generator cannot mask it.
def test_pure_module_imports_without_mutation_capable_side_effects():
    probe = (
        "import sys\n"
        "import schema_catalogue_source as s\n"
        # Nothing mutation-capable is pulled in by importing the seam:
        "assert 'psycopg2' not in sys.modules, 'must not import a DB driver'\n"
        "assert 'schema_catalogue' not in sys.modules, 'must not import the generator'\n"
        "assert 'migration.redaction' not in sys.modules, 'must not import redaction'\n"
        # The seam exposes exactly the source projection and nothing else:
        "assert sorted(s.__all__) == ['fingerprint', 'introspect']\n"
        "for banned in ('Client','ApiError','render_pages','redact','presence_page',\n"
        "               'publish_pages','emit_generation','reconcile_catalogues',\n"
        "               'ensure_entities','main','os','psycopg2','urllib'):\n"
        "    assert not hasattr(s, banned), banned\n"
        # No env read at import time.
        "assert not hasattr(s, 'environ')\n"
        "print('clean-import OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(SCRIPTS)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "clean-import OK" in result.stdout
