from __future__ import annotations

import pathlib
import sys

import pytest


DOCS_API = pathlib.Path(__file__).resolve().parents[1]
ROOT = DOCS_API.parent
sys.path.insert(0, str(DOCS_API))

import migrate  # noqa: E402


def test_wp8_migration_chain_is_complete_and_ordered():
    migrations = migrate.discover(ROOT / "db" / "migrations")
    assert [migration.ordinal for migration in migrations] == list(range(12))
    assert migrations[0].filename == "000_base_docs.sql"
    assert migrations[-1].filename == "011_events_and_usage.sql"
    assert all(len(migration.checksum) == 64 for migration in migrations)


def test_wp8_schema_contains_the_durable_product_authorities():
    migrations = migrate.discover(ROOT / "db" / "migrations")
    sql = "\n".join(migration.sql for migration in migrations)
    assert "CREATE TABLE IF NOT EXISTS docs.corpus_certification" in sql
    assert "CREATE TABLE IF NOT EXISTS docs.redirects" in sql
    assert "CREATE TABLE IF NOT EXISTS docs.deployment_attempts" in sql
    assert "CREATE TABLE IF NOT EXISTS docplane.principals" in sql
    assert "CREATE TABLE IF NOT EXISTS docs.change_proposals" in sql
    assert "CREATE TABLE IF NOT EXISTS docplane.events" in sql
    assert "CREATE TABLE IF NOT EXISTS analytics.daily_page_usage" in sql
    assert "ADD COLUMN IF NOT EXISTS resource_id uuid" in sql
    assert "RECERTIFY_POLICY" in sql


def test_duplicate_ordinals_fail_closed(tmp_path):
    (tmp_path / "000_a.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (tmp_path / "000_b.sql").write_text("SELECT 2;\n", encoding="utf-8")
    with pytest.raises(migrate.MigrationError, match="duplicate migration ordinal"):
        migrate.discover(tmp_path)


def test_ordinal_gap_fails_closed(tmp_path):
    (tmp_path / "000_a.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (tmp_path / "002_b.sql").write_text("SELECT 2;\n", encoding="utf-8")
    with pytest.raises(migrate.MigrationError, match="migration ordinal gap"):
        migrate.discover(tmp_path)


def test_applied_checksum_drift_fails_closed():
    migration = migrate.Migration(
        ordinal=0,
        filename="000_base.sql",
        path=pathlib.Path("000_base.sql"),
        checksum="a" * 64,
        sql="SELECT 1;",
    )
    history = {
        0: {
            "ordinal": 0,
            "filename": "000_base.sql",
            "checksum": "b" * 64,
            "applied_at": None,
        }
    }
    with pytest.raises(migrate.MigrationError, match="checksum drift"):
        migrate.verify_history([migration], history)
