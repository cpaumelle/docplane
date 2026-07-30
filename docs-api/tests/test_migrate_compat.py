"""Rollback/forward-compatibility contract for the migration runner.

DocPlane migrations are additive-only, so restoring a previous image must be
a real rollback path: an old runner sees migrations applied by a newer image
as tolerated 'ahead' history, never as fatal divergence. Divergence within
the known range stays fatal.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "docs-api"))

import migrate  # noqa: E402


def _migration(ordinal: int, name: str, checksum: str = "a" * 64) -> migrate.Migration:
    return migrate.Migration(ordinal=ordinal, filename=f"{ordinal:03d}_{name}.sql", path=Path("."), checksum=checksum, sql="")


def _record(migration: migrate.Migration) -> dict:
    return {"ordinal": migration.ordinal, "filename": migration.filename, "checksum": migration.checksum, "applied_at": None}


OLD_IMAGE = [_migration(0, "docplane_genesis")]
NEW_IMAGE = OLD_IMAGE + [_migration(1, "work_captures", "b" * 64), _migration(2, "model_genesis", "c" * 64)]


def test_old_image_tolerates_newer_additive_history():
    history = {m.ordinal: _record(m) for m in NEW_IMAGE}
    ahead = migrate.verify_history(OLD_IMAGE, history)
    assert ahead == ["001 (001_work_captures.sql)", "002 (002_model_genesis.sql)"]


def test_new_image_sees_clean_prefix_and_applies_the_rest():
    history = {0: _record(OLD_IMAGE[0])}
    assert migrate.verify_history(NEW_IMAGE, history) == []


def test_divergence_within_known_range_stays_fatal():
    rogue = _migration(1, "something_else", "d" * 64)
    history = {0: _record(OLD_IMAGE[0]), 1: _record(rogue)}
    with pytest.raises(migrate.MigrationError):
        migrate.verify_history(NEW_IMAGE, history)  # filename drift at known ordinal


def test_checksum_drift_stays_fatal():
    tampered = dict(_record(NEW_IMAGE[1]), checksum="f" * 64)
    history = {0: _record(OLD_IMAGE[0]), 1: tampered}
    with pytest.raises(migrate.MigrationError):
        migrate.verify_history(NEW_IMAGE, history)


def test_history_gap_stays_fatal():
    history = {0: _record(OLD_IMAGE[0]), 2: _record(NEW_IMAGE[2])}
    with pytest.raises(migrate.MigrationError):
        migrate.verify_history(NEW_IMAGE, history)


def test_real_migration_files_verify_against_their_own_full_history():
    migrations = migrate.discover(ROOT / "db" / "migrations")
    history = {m.ordinal: {"ordinal": m.ordinal, "filename": m.filename, "checksum": m.checksum, "applied_at": None} for m in migrations}
    assert migrate.verify_history(migrations, history) == []
    # The rollback scenario with the real files: an image knowing only the
    # genesis runs against the fully-migrated database.
    ahead = migrate.verify_history(migrations[:1], history)
    assert len(ahead) == len(migrations) - 1