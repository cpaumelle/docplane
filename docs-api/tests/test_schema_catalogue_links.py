"""Resolver-level contract for generated schema-catalogue index links."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import schema_catalogue  # noqa: E402
from app.corpus_structure import analyse_links  # noqa: E402


def test_index_links_resolve_to_pages_emitted_in_the_same_run():
    """Every internal index link must resolve against this render's page set."""
    structure = {
        schema: {
            "t1": {
                "comment": None,
                "columns": [],
                "constraints": [],
                "indexes": [],
            }
        }
        for schema in ("docs", "model", "observe")
    }
    pages = schema_catalogue.render_pages(
        "docplane", "DocPlane", structure, "ab" * 32
    )
    rendered = [
        {
            "path": page["path"],
            "status": "active",
            "content": page["content"],
        }
        for page in pages
    ]

    result = analyse_links(rendered, [], {})
    assert result["counts"]["internal"] == len(structure)
    assert result["counts"]["broken"] == 0, result["broken_pages"]
