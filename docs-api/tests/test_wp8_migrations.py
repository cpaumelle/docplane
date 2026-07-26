from __future__ import annotations

import pathlib
import sys

import pytest


DOCS_API = pathlib.Path(__file__).resolve().parents[1]
ROOT = DOCS_API.parent
sys.path.insert(0, str(DOCS_API))

import migrate  # noqa: E402


def test_docplane_ships_one_authoritative_genesis():
    migrations = migrate.discover(ROOT / "db" / "migrations")
    assert [migration.ordinal for migration in migrations] == [0]
    assert migrations[0].filename == "000_docplane_genesis.sql"
    assert len(migrations[0].checksum) == 64


def test_genesis_contains_the_complete_product_authorities():
    migration = migrate.discover(ROOT / "db" / "migrations")[0]
    sql = migration.sql

    required_tables = (
        "docs.corpus_certification",
        "docs.redirects",
        "docs.deployment_attempts",
        "docplane.principals",
        "docs.change_proposals",
        "docplane.events",
        "docplane.workspaces",
        "work.initiatives",
        "docs.page_verifications",
        "docs.trust_mutation_receipts",
        "work.mutation_receipts",
        "docs.reorganisation_plans",
        "docs.reorganisation_operations",
    )
    for table in required_tables:
        assert f"CREATE TABLE {table}" in sql

    assert "resource_id uuid" in sql
    assert "RECERTIFY_POLICY" in sql
    assert "CREATE SCHEMA analytics" not in sql
    assert "schema_migrations" not in sql
    assert "ADD COLUMN IF NOT EXISTS" not in sql


def test_genesis_seeds_stable_system_workspaces():
    sql = migrate.discover(ROOT / "db" / "migrations")[0].sql
    expected = {
        "d0c00000-0000-5000-8000-000000000001": "reference",
        "d0c00000-0000-5000-8000-000000000002": "operations",
        "d0c00000-0000-5000-8000-000000000003": "work",
        "d0c00000-0000-5000-8000-000000000004": "migration-import",
    }
    for workspace_id, workspace_key in expected.items():
        assert workspace_id in sql
        assert f"'{workspace_key}'" in sql
    assert "Stable system workspaces" in sql
    assert "COPY " not in sql


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
        filename="000_docplane_genesis.sql",
        path=pathlib.Path("000_docplane_genesis.sql"),
        checksum="a" * 64,
        sql="SELECT 1;",
    )
    history = {
        0: {
            "ordinal": 0,
            "filename": "000_docplane_genesis.sql",
            "checksum": "b" * 64,
            "applied_at": None,
        }
    }
    with pytest.raises(migrate.MigrationError, match="checksum drift"):
        migrate.verify_history([migration], history)
