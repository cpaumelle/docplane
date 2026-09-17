"""Regression for capture 075a36de: generated catalogue pages carry no lifecycle marker.

Every page the meter-list and schema-catalogue generators emitted landed in the
observatory's `missing_lifecycle` signal, and always had -- 42 of 42 meter-list
pages at the time of filing. It was never a regression from any one change; the
omission was in `render_pages()` from the start, so each new rule file or schema
silently enlarged a structural signal nobody could act on.

The capture asked whether the sibling generator shared the blind spot. It did:
neither emitted `**Lifecycle:**` nor `<!-- lifecycle: -->`.

These tests assert through the real parser (`corpus_structure.lifecycle_of`, the
same function the observatory uses) rather than by matching strings. A marker
that the detector cannot read would be no fix at all, and `VALID_LIFECYCLES` is
checked so a typo cannot move a page from `missing_lifecycle` into
`unknown_lifecycle` and call it progress.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "docs-api"))

import meter_list  # noqa: E402
import schema_catalogue  # noqa: E402
from app.corpus_structure import VALID_LIFECYCLES, lifecycle_of  # noqa: E402

RULES_YML = """
groups:
- name: example_group
  rules:
  - alert: ExampleAlert
    expr: up == 0
    for: 5m
    labels:
      severity: warning
      service: example
    annotations:
      summary: "example target down"
"""


SCHEMA_STRUCTURE = {
    "docplane": {
        "principals": {
            "comment": "Named identities",
            "columns": [
                {"name": "principal_id", "type": "uuid", "nullable": False, "default": None},
            ],
            "constraints": [
                {"kind": "p", "name": "principals_pkey", "definition": "PRIMARY KEY (principal_id)"},
            ],
            "indexes": [{"name": "principals_pkey", "definition": "CREATE UNIQUE INDEX ..."}],
        },
    },
}


def _meter_pages(tmp_path) -> list[dict[str, str]]:
    (tmp_path / "example-alerts.yml").write_text(RULES_YML, encoding="utf-8")
    structure = meter_list.parse_rules(tmp_path)
    fp = meter_list.fingerprint(structure)
    return [
        meter_list.presence_page(),
        *meter_list.render_pages("example.prometheus", structure, fp),
    ]


def _schema_pages(tmp_path) -> list[dict[str, str]]:
    fp = schema_catalogue.fingerprint(SCHEMA_STRUCTURE)
    return [
        schema_catalogue.presence_page(),
        *schema_catalogue.render_pages("docplane", "DocPlane PostgreSQL", SCHEMA_STRUCTURE, fp),
    ]


@pytest.mark.parametrize(
    "pages_fn, generator",
    [(_meter_pages, "docplane-meter-list"), (_schema_pages, "docplane-schema-catalogue")],
)
def test_every_generated_page_declares_a_lifecycle(pages_fn, generator, tmp_path):
    """The exact defect. Asserted through the observatory's own parser: a
    marker it cannot read would not clear missing_lifecycle."""
    pages = pages_fn(tmp_path)
    assert pages, generator
    missing = [page["path"] for page in pages if lifecycle_of(page["content"]) is None]
    assert not missing, f"{generator} emits pages with no lifecycle marker: {missing}"


@pytest.mark.parametrize(
    "pages_fn, generator",
    [(_meter_pages, "docplane-meter-list"), (_schema_pages, "docplane-schema-catalogue")],
)
def test_declared_lifecycle_is_in_the_vocabulary(pages_fn, generator, tmp_path):
    """A typo would move these pages from missing_lifecycle into
    unknown_lifecycle -- a different bucket, not a fix."""
    for page in pages_fn(tmp_path):
        value = lifecycle_of(page["content"])
        assert value in VALID_LIFECYCLES, f"{generator} {page['path']}: {value!r}"
        assert value == "REFERENCE", page["path"]


@pytest.mark.parametrize("pages_fn", [_meter_pages, _schema_pages])
def test_marker_carries_both_forms(pages_fn, tmp_path):
    """Authored pages carry the visible field and the machine comment. The
    parser accepts either, but generated pages should not look different from
    the rest of the corpus to a human reading the source."""
    for page in pages_fn(tmp_path):
        assert "**Lifecycle:** REFERENCE" in page["content"], page["path"]
        assert "<!-- lifecycle: REFERENCE -->" in page["content"], page["path"]


@pytest.mark.parametrize("pages_fn", [_meter_pages, _schema_pages])
def test_marker_sits_directly_under_the_h1(pages_fn, tmp_path):
    """Placement is not cosmetic: the field is the first thing a reader should
    see about the page, and burying it below generated tables would make it
    invisible in exactly the pages that needed it."""
    for page in pages_fn(tmp_path):
        lines = [line for line in page["content"].splitlines() if line.strip()]
        assert lines[0].startswith("# "), page["path"]
        assert lines[1] == "**Lifecycle:** REFERENCE", (page["path"], lines[:3])


def test_generator_versions_were_bumped():
    """Both generators skip regeneration when the fingerprint is unchanged and
    generator_version matches, so shipping new output without a version bump
    would leave every existing page exactly as it was."""
    assert meter_list.GENERATOR_VERSION != "1.3.1"
    assert schema_catalogue.GENERATOR_VERSION != "1.0.4"
