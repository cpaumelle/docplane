"""Importer-level revision-identity regression tests.

Proves defect (a): when import rewrites content, the source revision identity
must never be retained; when import changes nothing, it is retained.
All fixtures are synthetic.
"""
from __future__ import annotations

from migration.importer import SourcePage, import_page

ACCESS_KEY_SHAPED = "AKIAABCDEFGHIJKLMNOP"


def test_changed_content_mints_a_new_revision():
    page = SourcePage(
        resource_id="example-0001",
        revision="rev-source-example-0001",
        content=f"secret key {ACCESS_KEY_SHAPED} in body",
    )
    imported = import_page(page)
    assert imported.changed
    assert not imported.revision_retained
    assert imported.revision != page.revision
    assert ACCESS_KEY_SHAPED not in imported.content


def test_unchanged_content_retains_the_source_revision():
    page = SourcePage(
        resource_id="example-0002",
        revision="rev-source-example-0002",
        content="clean body with {{password}} and <VAR> only",
    )
    imported = import_page(page)
    assert not imported.changed
    assert imported.revision_retained
    assert imported.revision == page.revision


def test_minted_revision_is_deterministic_on_replay():
    page = SourcePage(
        resource_id="example-0001",
        revision="rev-source-example-0001",
        content=f"key {ACCESS_KEY_SHAPED}",
    )
    first = import_page(page)
    second = import_page(page)
    assert first.revision == second.revision
    assert first.content_sha256 == second.content_sha256


def test_different_source_content_yields_different_revision():
    base = SourcePage("example-0001", "rev-source-example-0001", f"a {ACCESS_KEY_SHAPED}")
    other = SourcePage("example-0001", "rev-source-example-0001", f"b {ACCESS_KEY_SHAPED}")
    assert import_page(base).revision != import_page(other).revision


def test_revision_retention_invariant_holds_across_a_batch():
    pages = [
        SourcePage("example-0001", "rev-a", f"x {ACCESS_KEY_SHAPED}"),
        SourcePage("example-0002", "rev-b", "clean {{password}}"),
        SourcePage("example-0003", "rev-c", f"y {ACCESS_KEY_SHAPED} z"),
    ]
    for page in pages:
        imported = import_page(page)
        # The defect-(a) invariant, stated directly.
        if imported.changed:
            assert imported.revision != imported.source_revision
        else:
            assert imported.revision == imported.source_revision
