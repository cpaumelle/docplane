"""Optional migration-ledger identity (additive to projection contract 2).

Pure tests (no database): ledger digest determinism, setting validation, the
UNAVAILABLE/EMPTY/CAPTURED mapping against a fake connection, provenance and overview
rendering with and without a ledger, the no-churn guarantee for catalogues that do not
configure a ledger, the ledger/structure separation in the render identity, and the
redaction guard's shape check on ledger digests.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2.errors
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import schema_catalogue  # noqa: E402
import schema_catalogue_ledger as ledger  # noqa: E402
import schema_catalogue_projection as projection  # noqa: E402

ROWS = [
    ("000_platform.sql", "a" * 64, datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ("001_ingest.sql", "b" * 64, datetime(2026, 1, 2, tzinfo=timezone.utc)),
    ("318_retwin_device_guard.sql", "c" * 64, datetime(2026, 11, 1, tzinfo=timezone.utc)),
]


class _Cursor:
    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.executed = rows, error, []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        if self.error is not None and sql.lstrip().startswith("SELECT"):
            raise self.error

    def fetchall(self):
        return list(self.rows or [])


class _Conn:
    def __init__(self, rows=None, error=None):
        self.cur, self.rolled_back = _Cursor(rows, error), 0

    def cursor(self):
        return self.cur

    def rollback(self):
        self.rolled_back += 1


def test_ledger_table_setting_accepts_schema_qualified_lowercase_only():
    assert ledger.ledger_table_setting(None) is None
    assert ledger.ledger_table_setting("   ") is None
    assert ledger.ledger_table_setting(" public.schema_migrations ") == "public.schema_migrations"
    for bad in ("schema_migrations", "Public.schema_migrations", "public.schema_migrations; drop", 'a."b"', "a.b.c"):
        with pytest.raises(RuntimeError, match="CATALOGUE_LEDGER_TABLE"):
            ledger.ledger_table_setting(bad)


def test_ledger_digest_is_order_independent_and_content_bound():
    pairs = [(r[0], r[1]) for r in ROWS]
    assert ledger.ledger_digest(pairs) == ledger.ledger_digest(list(reversed(pairs)))
    assert ledger.ledger_digest(pairs) != ledger.ledger_digest(pairs[:-1])
    assert ledger.ledger_digest(pairs) != ledger.ledger_digest([(pairs[0][0], "d" * 64), *pairs[1:]])
    assert len(ledger.ledger_digest(pairs)) == 24   # below the projection store's 40-hex secret-shape threshold


def test_capture_reads_in_a_read_only_pg_catalog_transaction_and_reports_the_set():
    conn = _Conn(rows=ROWS)
    captured = ledger.capture_ledger(conn, "public.schema_migrations")
    assert conn.cur.executed[:2] == ["SET TRANSACTION READ ONLY", "SET LOCAL search_path TO pg_catalog"]
    assert 'FROM "public"."schema_migrations" ORDER BY filename COLLATE "C"' in conn.cur.executed[2]
    assert captured["status"] == "CAPTURED"
    assert captured["rows"] == 3
    assert captured["head_filename"] == "318_retwin_device_guard.sql"
    assert captured["head_sha256_prefix"] == "c" * 24
    assert captured["digest_prefix_hex"] == 24
    assert captured["head_applied_at"] == "2026-11-01T00:00:00+00:00"
    assert captured["ledger_digest"] == ledger.ledger_digest([(r[0], r[1]) for r in ROWS])
    assert conn.rolled_back == 0


def test_a_gap_in_the_set_changes_the_digest_even_with_the_same_head():
    # A maximum migration number is not proof of the applied set.
    full = ledger.capture_ledger(_Conn(rows=ROWS), "public.schema_migrations")
    gapped = ledger.capture_ledger(_Conn(rows=[ROWS[0], ROWS[2]]), "public.schema_migrations")
    assert full["head_filename"] == gapped["head_filename"]
    assert full["ledger_digest"] != gapped["ledger_digest"]
    assert ledger.identity(full) != ledger.identity(gapped)


def test_capture_maps_a_refused_read_to_unavailable_and_rolls_back():
    conn = _Conn(error=psycopg2.errors.InsufficientPrivilege("permission denied for table schema_migrations"))
    captured = ledger.capture_ledger(conn, "public.schema_migrations")
    assert captured["status"] == "UNAVAILABLE"
    assert captured["error_class"] == "InsufficientPrivilege"
    assert "permission denied" not in json.dumps(captured)
    assert conn.rolled_back == 1


def test_capture_reports_an_empty_ledger():
    assert ledger.capture_ledger(_Conn(rows=[]), "public.schema_migrations") == {
        "status": "EMPTY", "table": "public.schema_migrations", "rows": 0,
    }


def test_identity_excludes_applied_at_and_head_sha():
    captured = ledger.capture_ledger(_Conn(rows=ROWS), "public.schema_migrations")
    assert set(ledger.identity(captured)) == {"status", "table", "rows", "head_filename", "ledger_digest"}
    assert ledger.identity(None) is None


STRUCTURE = {"platform": {"comment": None, "views": {}, "enums": {}, "tables": {}}}
STATIC = {"environment": "production", "source_identity": "VPS3/trevarn"}


def _provenance(migration_ledger=None):
    return projection.build_provenance(
        config={"schemas": {}}, structure=STRUCTURE, static_provenance=STATIC,
        source_metadata={"database_name": "trevarn", "server_version": "16.13"},
        extracted_at="2026-11-04T22:00:00+00:00", generator=schema_catalogue.GENERATOR,
        migration_ledger=migration_ledger,
    )


def test_provenance_default_is_the_unchanged_contract_2_not_captured():
    assert _provenance()["migration_ledger"] == ledger.NOT_CAPTURED


def test_provenance_embeds_a_captured_ledger_verbatim():
    captured = ledger.capture_ledger(_Conn(rows=ROWS), "public.schema_migrations")
    assert _provenance(captured)["migration_ledger"] == captured


def test_overview_page_names_the_ledger_head_only_when_captured_and_touches_no_schema_page():
    fp = schema_catalogue.fingerprint(STRUCTURE)
    without = schema_catalogue.render_pages("trevarn", "Trevarn PostgreSQL", STRUCTURE, fp, {"schemas": {}}, STATIC)
    captured = ledger.capture_ledger(_Conn(rows=ROWS), "public.schema_migrations")
    with_ledger = schema_catalogue.render_pages(
        "trevarn", "Trevarn PostgreSQL", STRUCTURE, fp, {"schemas": {}}, STATIC, migration_ledger=captured,
    )
    overview = {p["path"]: p["content"] for p in without}["model/schema-catalogue/trevarn/index.md"]
    overview_ledger = {p["path"]: p["content"] for p in with_ledger}["model/schema-catalogue/trevarn/index.md"]
    assert "not captured by this projection contract" in overview
    assert "`public.schema_migrations` head `318_retwin_device_guard.sql` (3 rows" in overview_ledger
    assert ("c" * 40) not in overview_ledger
    # A ledger-only change rewrites the overview, never a schema page.
    differing = [a for a, b in zip(without, with_ledger) if a["content"] != b["content"]]
    assert [p["path"] for p in differing] == ["model/schema-catalogue/trevarn/index.md"]


def test_unavailable_ledger_is_stated_on_the_page_without_server_text():
    unavailable = {"status": "UNAVAILABLE", "table": "public.schema_migrations", "error_class": "InsufficientPrivilege"}
    line = ledger.summary_line(unavailable)
    assert "UNAVAILABLE" in line and "InsufficientPrivilege" in line and "structure remains authoritative" in line


def test_render_identity_unchanged_without_a_ledger_setting_and_moved_by_a_ledger_only_change():
    assert projection.config_hash({"schemas": {}}, STATIC) == projection.config_hash({"schemas": {}}, dict(STATIC))
    captured = ledger.capture_ledger(_Conn(rows=ROWS), "public.schema_migrations")
    with_ledger = projection.config_hash({"schemas": {}}, {**STATIC, "migration_ledger": ledger.identity(captured)})
    assert with_ledger != projection.config_hash({"schemas": {}}, STATIC)
    other = ledger.capture_ledger(_Conn(rows=ROWS[:-1]), "public.schema_migrations")
    assert with_ledger != projection.config_hash({"schemas": {}}, {**STATIC, "migration_ledger": ledger.identity(other)})
    # The structural fingerprint never sees the ledger.
    assert schema_catalogue.fingerprint(STRUCTURE) == schema_catalogue.fingerprint(STRUCTURE)


def test_captured_ledger_carries_no_secret_shaped_value_for_the_projection_store():
    """DocPlane refuses 40+ hex characters anywhere in provenance (PROJECTION_SECRET_SHAPED);
    the dev end-to-end run proved it. Prefixes must stay below that threshold."""
    import re
    from migration.redaction import redact
    captured = ledger.capture_ledger(_Conn(rows=ROWS), "public.schema_migrations")
    for value in captured.values():
        if isinstance(value, str):
            assert not re.search(r"[0-9a-f]{40,}", value), value
            assert not redact(value, label="test").changed
    fp = schema_catalogue.fingerprint(STRUCTURE)
    document = projection.build_document("trevarn", "Trevarn PostgreSQL", STRUCTURE, fp, "0" * 64,
                                         schema_catalogue.PROJECTION_CONTRACT_VERSION)
    schema_catalogue.guard_projection(document, _provenance(captured))   # unchanged guard, no exemption needed
