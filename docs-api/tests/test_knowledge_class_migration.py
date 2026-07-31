"""Migration 010 forward and transactional rollback contract."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg2.errors
import pytest

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "db/migrations/010_knowledge_class_check.sql").read_text(encoding="utf-8")
ROLLBACK_SQL = "ALTER TABLE docs.pages DROP CONSTRAINT pages_knowledge_class_check"


def test_migration_010_is_nullable_and_closes_exact_vocabulary():
    assert "ADD CONSTRAINT pages_knowledge_class_check" in SQL
    assert "knowledge_class IS NULL" in SQL
    assert "NOT NULL" not in SQL
    for value in ("ARCHITECTURE", "OPERATION", "REFERENCE", "POLICY", "DECISION", "EVIDENCE", "DESIGN", "WORK_NOTE"):
        assert f"'{value}'" in SQL
    sys.path.insert(0, str(ROOT / "docs-api"))
    from app.trust_models import PageClassificationUpdate
    request = PageClassificationUpdate(
        expected_metadata_version=1,
        workspace_id="00000000-0000-0000-0000-000000000001",
        publication_state="PUBLISHED",
        knowledge_class=None,
        reason="leave unclassified from Authoring",
    )
    assert request.knowledge_class is None


@pytest.mark.skipif(not os.environ.get("DB_HOST"), reason="requires migrated PostgreSQL")
def test_forward_refuses_typos_and_rollback_reopens_only_the_vocabulary_gate():
    sys.path.insert(0, str(ROOT / "docs-api"))
    from app.db import get_conn

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT workspace_id FROM docplane.workspaces WHERE workspace_key = 'reference'")
        workspace_id = cur.fetchone()[0]
        values = (f"migration-010-{uuid.uuid4().hex}.md", "Migration 010", "Tests/Migration 010", "# Test", workspace_id)
        cur.execute("SAVEPOINT before_invalid")
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(
                "INSERT INTO docs.pages (path,title,nav_path,content,workspace_id,knowledge_class,updated_by) VALUES (%s,%s,%s,%s,%s,'TYPO','test')",
                values,
            )
        cur.execute("ROLLBACK TO SAVEPOINT before_invalid")

        # Rollback is intentionally just the additive gate. Exercise it in
        # this transaction, prove the pre-010 write is accepted, then roll the
        # whole transaction back so the shared harness retains migration 010.
        cur.execute(ROLLBACK_SQL)
        cur.execute(
            "INSERT INTO docs.pages (path,title,nav_path,content,workspace_id,knowledge_class,updated_by) VALUES (%s,%s,%s,%s,%s,'TYPO','test')",
            values,
        )
        conn.rollback()

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_constraint WHERE conname = 'pages_knowledge_class_check'")
        assert cur.fetchone() == (1,)
