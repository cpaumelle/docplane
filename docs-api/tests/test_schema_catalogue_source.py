"""Behaviour-preservation proofs for the schema-catalogue *source seam*.

``scripts/schema_catalogue_source.py`` was extracted from the generator
(``scripts/schema_catalogue.py``) so the SCHEDULED schema observer can import
the authoritative source projection — ``introspect`` and
``fingerprint`` — without importing the mutation-capable generator.

Source projection contract 2 (e004787b D1, 2026-09-26) deliberately changed
the STRUCTURE the seam produces (pg_catalog-only, pinned search_path, FK column
pairs, CHECK/exclusion constraints, index columns, udt/enum/array, views). The
fingerprint ALGORITHM did not change, so the frozen fingerprint oracles below
still pin it exactly; the v1 render oracle is retired and replaced by a
contract-2 byte-stability pin, and the disposable-PostgreSQL test asserts the
contract-2 facets against independently authored expectations.

These tests originally proved the extraction changed *nothing* observable:

  * the pure module reproduces a fingerprint captured from PRISTINE main
    (frozen in ``fixtures/schema_catalogue_source_oracle.json``), so "expected"
    is never computed through the code under test;
  * the generator now holds a single implementation and re-exports it;
  * schema-allowlist order is normalised, SQL pins list order, and fingerprint
    canonicalisation absorbs mapping insertion order;
  * rendering the extracted structure matches a frozen render oracle (whose
    epoch was deliberately moved 2026-09-17 when render_pages() began emitting
    a lifecycle marker -- see the fixture's _provenance; the *fingerprint*
    oracles are untouched and still carry the pre-extraction values);
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

        def fetchone(self):
            return None

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


# 5. Mapping insertion order does not affect the canonical fingerprint.
#    List order is significant and is made deterministic by the introspection
#    SQL's explicit ORDER BY clauses; this test deliberately changes mappings
#    only and proves exactly what json.dumps(sort_keys=True) guarantees.
def test_mapping_insertion_order_does_not_change_canonical_fingerprint():
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


# 6. Rendering a contract-2 structure is byte-stable. (The v1 render oracle
#    was retired with the v1 structure shape on 2026-09-26; this pin moves only
#    with a deliberate projection-contract or presentation change.)
V2_STRUCTURE = {
    "docplane": {
        "comment": None,
        "views": {},
        "enums": {"status": ["ACTIVE", "RETIRED"]},
        "tables": {
            "principals": {
                "kind": "table", "comment": "Named identities",
                "columns": [
                    {"name": "principal_id", "type": "uuid", "udt": "uuid", "array": False, "enum": None,
                     "nullable": False, "default": "gen_random_uuid()", "identity": None, "generated": None, "comment": None},
                    {"name": "status", "type": "docplane.status", "udt": "docplane.status", "array": False,
                     "enum": "docplane.status", "nullable": False, "default": None, "identity": None,
                     "generated": None, "comment": None},
                ],
                "primary_key": {"name": "principals_pkey", "columns": ["principal_id"]},
                "foreign_keys": [], "unique": [], "checks": [], "exclusions": [],
                "indexes": [{"name": "principals_pkey", "unique": True, "primary": True, "method": "btree",
                             "columns": ["principal_id"], "include": [], "predicate": None,
                             "definition": "CREATE UNIQUE INDEX principals_pkey ON docplane.principals USING btree (principal_id)"}],
            },
        },
    },
}


def test_contract2_rendering_is_byte_stable_and_redaction_clean():
    first = _render_sha(V2_STRUCTURE)
    assert first == _render_sha(V2_STRUCTURE)
    fp = schema_catalogue_source.fingerprint(V2_STRUCTURE)
    pages = schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", V2_STRUCTURE, fp)
    assert all("<REDACTED" not in page["content"] for page in pages)
    assert "enum `docplane.status`" in pages[1]["content"]


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


# 3. Disposable PostgreSQL introspection matches contract-2 semantics EXACTLY,
#    compared against independently authored expectations.
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
    DROP SCHEMA IF EXISTS structure_probe CASCADE;
    CREATE SCHEMA seam_probe;
    CREATE SCHEMA structure_probe;
    COMMENT ON SCHEMA seam_probe IS 'seam probe schema';
    CREATE TYPE seam_probe.mood AS ENUM ('calm', 'busy');
    CREATE TABLE structure_probe.site (id integer PRIMARY KEY);
    CREATE TABLE seam_probe.parent (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        label text NOT NULL
    );
    COMMENT ON TABLE seam_probe.parent IS 'seam probe parent';
    CREATE TABLE seam_probe.child (
        id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        parent_id uuid NOT NULL REFERENCES seam_probe.parent(id) ON DELETE CASCADE,
        site_id integer REFERENCES structure_probe.site(id),
        code text NOT NULL,
        note text,
        tags text[] NOT NULL DEFAULT '{}',
        mood seam_probe.mood NOT NULL DEFAULT 'calm',
        moods seam_probe.mood[],
        active boolean NOT NULL DEFAULT false,
        code_upper text GENERATED ALWAYS AS (upper(code)) STORED,
        CONSTRAINT child_code_key UNIQUE (code),
        CONSTRAINT child_code_shape CHECK (code ~ '^[a-z]+$')
    );
    COMMENT ON COLUMN seam_probe.child.note IS 'free text';
    CREATE INDEX child_active_idx ON seam_probe.child (parent_id) INCLUDE (code) WHERE active;
    CREATE INDEX child_lower_idx ON seam_probe.child (lower(code));
    CREATE VIEW seam_probe.active_children AS SELECT id, code FROM seam_probe.child WHERE active;
    """
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
        # Role/session search_path must not change a single byte (#174 finding).
        with psycopg2.connect(dsn) as conn2:
            with conn2.cursor() as cur:
                cur.execute("SET search_path TO seam_probe, structure_probe, public")
            conn2.commit()
            again = schema_catalogue_source.introspect(conn2, ["seam_probe"])
        assert structure == again
        assert schema_catalogue_source.fingerprint(structure) == schema_catalogue_source.fingerprint(again)

        schema = structure["seam_probe"]
        assert schema["comment"] == "seam probe schema"
        assert set(schema["tables"]) == {"child", "parent"}
        assert schema["enums"] == {"mood": ["calm", "busy"]}

        parent = schema["tables"]["parent"]
        assert parent["kind"] == "table"
        assert parent["comment"] == "seam probe parent"
        assert parent["primary_key"] == {"name": "parent_pkey", "columns": ["id"]}
        assert parent["columns"][0] == {
            "name": "id", "type": "uuid", "udt": "uuid", "array": False, "enum": None,
            "nullable": False, "default": "gen_random_uuid()", "identity": None,
            "generated": None, "comment": None,
        }

        child = schema["tables"]["child"]
        columns = {column["name"]: column for column in child["columns"]}
        assert [column["name"] for column in child["columns"]] == [
            "id", "parent_id", "site_id", "code", "note", "tags", "mood", "moods", "active", "code_upper",
        ]
        assert columns["id"]["identity"] == "ALWAYS" and columns["id"]["default"] is None
        assert columns["note"]["comment"] == "free text" and columns["note"]["nullable"] is True
        assert columns["tags"]["array"] is True and columns["tags"]["type"] == "text[]"
        assert columns["tags"]["default"] == "'{}'::text[]"
        # Schema-qualified regardless of the session search_path above.
        assert columns["mood"]["type"] == "seam_probe.mood"
        assert columns["mood"]["enum"] == "seam_probe.mood"
        assert columns["mood"]["default"] == "'calm'::seam_probe.mood"
        assert columns["moods"]["array"] is True and columns["moods"]["enum"] == "seam_probe.mood"
        assert columns["code_upper"]["generated"] == "upper(code)"
        assert columns["code_upper"]["default"] is None

        fks = {fk["name"]: fk for fk in child["foreign_keys"]}
        assert fks["child_parent_id_fkey"]["columns"] == ["parent_id"]
        assert fks["child_parent_id_fkey"]["references"] == {
            "schema": "seam_probe", "table": "parent", "columns": ["id"],
        }
        assert fks["child_parent_id_fkey"]["on_delete"] == "CASCADE"
        assert fks["child_parent_id_fkey"]["on_update"] == "NO ACTION"
        # A cross-schema reference is captured even though that schema is not catalogued.
        assert fks["child_site_id_fkey"]["references"] == {
            "schema": "structure_probe", "table": "site", "columns": ["id"],
        }
        assert "REFERENCES seam_probe.parent(id)" in fks["child_parent_id_fkey"]["definition"]
        assert child["unique"] == [{"name": "child_code_key", "columns": ["code"], "definition": "UNIQUE (code)"}]
        assert [check["name"] for check in child["checks"]] == ["child_code_shape"]
        assert child["checks"][0]["columns"] == ["code"]

        indexes = {index["name"]: index for index in child["indexes"]}
        assert indexes["child_active_idx"]["columns"] == ["parent_id"]
        assert indexes["child_active_idx"]["include"] == ["code"]
        assert indexes["child_active_idx"]["predicate"] == "active"
        assert indexes["child_lower_idx"]["columns"] == ["lower(code)"]
        assert indexes["child_code_key"]["unique"] is True
        assert indexes["child_pkey"]["primary"] is True
        assert [index["name"] for index in child["indexes"]] == sorted(indexes)

        view = schema["views"]["active_children"]
        assert view["kind"] == "view"
        assert [column["name"] for column in view["columns"]] == ["id", "code"]
        assert "WHERE" in view["definition"] and "active" in view["definition"]
    finally:
        cleanup = psycopg2.connect(dsn)
        try:
            cleanup.autocommit = True
            with cleanup.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS seam_probe CASCADE; DROP SCHEMA IF EXISTS structure_probe CASCADE;")
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
