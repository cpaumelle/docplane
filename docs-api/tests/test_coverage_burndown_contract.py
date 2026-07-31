from pathlib import Path

from app.model_models import EntityLinkCreate

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (ROOT / "db/migrations/009_coverage_burndown.sql").read_text(encoding="utf-8")


def test_migration_009_is_additive_and_carries_both_contracts():
    lowered = MIGRATION.lower()
    assert "alter table model.entity_links" in lowered
    assert "add column metadata jsonb" in lowered
    assert "create table work.coverage_gap_items" in lowered
    assert "unique (gap_kind, subject_entity_id)" in lowered
    assert "'open', 'resolved'" in lowered
    for forbidden in ("drop ", "delete from", "update docs.", "update model.", "update work."):
        assert forbidden not in lowered


def test_link_metadata_is_additive_and_defaults_empty():
    link = EntityLinkCreate(
        relation="WATCHES",
        to_entity_id="11111111-1111-1111-1111-111111111111",
    )
    assert link.metadata == {}
    overlay = EntityLinkCreate(
        relation="WATCHES",
        to_entity_id="11111111-1111-1111-1111-111111111111",
        metadata={"source": "overlay"},
    )
    assert overlay.metadata == {"source": "overlay"}
