"""End-to-end import fixture test.

Drives the synthetic import manifest through the importer and proves, for a
real fixture set: source revision -> transformed content hash -> newly-minted
revision -> deterministic replay. All fixtures are synthetic.
"""
from __future__ import annotations

import json
from pathlib import Path

from migration.importer import SourcePage, import_page
from migration.redaction import braces_balanced, find_malformed_markers

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load_manifest() -> dict:
    return json.loads((FIXTURES / "import-e2e.json").read_text(encoding="utf-8"))


def test_end_to_end_import_of_synthetic_manifest():
    manifest = _load_manifest()
    minted: dict[str, str] = {}

    for entry in manifest["pages"]:
        source_text = (FIXTURES / entry["source_file"]).read_text(encoding="utf-8")
        page = SourcePage(
            resource_id=entry["resource_id"],
            revision=entry["source_revision"],
            content=source_text,
        )
        imported = import_page(page)

        # Structural guarantees on transformed output.
        assert braces_balanced(imported.content)
        assert find_malformed_markers(imported.content) == []

        # changed / revision-identity expectations from the manifest.
        assert imported.changed is entry["expected_changed"]
        if imported.changed:
            assert imported.revision != page.revision  # newly minted
        else:
            assert imported.revision == page.revision  # retained

        # Content hash is populated and stable.
        assert len(imported.content_sha256) == 64
        minted[entry["resource_id"]] = imported.revision

    # Deterministic replay of the whole manifest reproduces every revision.
    for entry in manifest["pages"]:
        source_text = (FIXTURES / entry["source_file"]).read_text(encoding="utf-8")
        replay = import_page(
            SourcePage(entry["resource_id"], entry["source_revision"], source_text)
        )
        assert replay.revision == minted[entry["resource_id"]]
