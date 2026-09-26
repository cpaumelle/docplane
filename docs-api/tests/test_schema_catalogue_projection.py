"""Pure projection and human-view contracts for the schema catalogue (e004787b).

Covers the parts of the structured projection and its Markdown/Mermaid views
that need no database: relation derivation, configuration authority,
canonical-availability statements (D3), deterministic rendering (D2: no
publication churn), few-pages-many-records, and redaction compatibility.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import schema_catalogue  # noqa: E402
import schema_catalogue_projection as projection  # noqa: E402
from migration.redaction import braces_balanced, redact  # noqa: E402


def _column(name, nullable=False):
    return {"name": name, "type": "integer", "udt": "int4", "array": False, "enum": None,
            "nullable": nullable, "default": None, "identity": None, "generated": None, "comment": None}


def _table(columns, fks=(), pk=("id",)):
    return {"kind": "table", "comment": None, "columns": columns,
            "primary_key": {"name": "pk", "columns": list(pk)} if pk else None,
            "foreign_keys": list(fks), "unique": [], "checks": [], "exclusions": [], "indexes": []}


def _fk(name, column, schema, table):
    return {"name": name, "columns": [column], "references": {"schema": schema, "table": table, "columns": ["id"]},
            "on_update": "NO ACTION", "on_delete": "CASCADE", "deferrable": False,
            "initially_deferred": False, "definition": "FOREIGN KEY"}


STRUCTURE = {
    "ccm": {"comment": None, "views": {}, "enums": {}, "tables": {
        "edges": _table([_column("id"), _column("edge_node_id", nullable=True)],
                        [_fk("edges_node_fk", "edge_node_id", "transit", "edge_nodes")]),
        "loose": _table([_column("id")]),
    }},
    "transit": {"comment": None, "views": {}, "enums": {}, "tables": {
        "edge_nodes": _table([_column("id"), _column("site_id")], [_fk("nodes_site_fk", "site_id", "transit", "sites")]),
        "sites": _table([_column("id")]),
        "outside": _table([_column("id"), _column("ref")], [_fk("outside_fk", "ref", "public", "uncatalogued")]),
    }},
}


def _render(config=None, static=None):
    fp = schema_catalogue.fingerprint(STRUCTURE)
    return schema_catalogue.render_pages(
        "pilot", "Pilot DB", STRUCTURE, fp, config or {"schemas": {}},
        static or {"environment": "test", "source_identity": "unit"},
    )


def test_relations_are_explicit_edges_with_cross_schema_and_coverage_flags():
    edges = {edge["constraint"]: edge for edge in projection.relations(STRUCTURE)}
    assert edges["edges_node_fk"]["cross_schema"] is True
    assert edges["edges_node_fk"]["target_catalogued"] is True
    assert edges["nodes_site_fk"]["cross_schema"] is False
    assert edges["outside_fk"]["target_catalogued"] is False
    assert edges["edges_node_fk"]["from"] == {"schema": "ccm", "table": "edges", "columns": ["edge_node_id"]}


def test_one_page_per_schema_plus_overview_regardless_of_table_count():
    many = {"s": {"comment": None, "views": {}, "enums": {}, "tables": {
        f"t{index:03d}": _table([_column("id")]) for index in range(150)
    }}}
    fp = schema_catalogue.fingerprint(many)
    pages = schema_catalogue.render_pages("big", "Big", many, fp)
    assert [page["path"] for page in pages] == [
        "model/schema-catalogue/big/index.md", "model/schema-catalogue/big/s.md",
    ]
    assert pages[1]["content"].count("\n### `t") == 150  # table sections, not table pages


def test_rendering_is_deterministic_and_free_of_volatile_provenance():
    first = _render()
    assert first == _render()
    provenance = projection.build_provenance(
        config={"schemas": {}}, structure=STRUCTURE,
        static_provenance={"environment": "test", "source_identity": "unit"},
        source_metadata={"database_name": "db", "server_version": "16.99"},
        extracted_at="2031-01-01T00:00:00+00:00",
        generator=schema_catalogue.GENERATOR,
    )
    rendered = "\n".join(page["content"] for page in first)
    # Volatile facts live only in the stored provenance, so they can never
    # make a scheduled run republish an unchanged schema.
    assert provenance["extracted_at"] not in rendered
    assert "16.99" not in rendered


def test_mermaid_is_brace_balanced_word_form_and_passes_canonical_redaction():
    pages = _render()
    for page in pages:
        assert braces_balanced(page["content"]), page["path"]
        assert redact(page["content"], label="unit").changed is False
        for block in re.findall(r"```mermaid\n(.*?)```", page["content"], re.S):
            assert block.startswith("erDiagram")
            assert "--o{" not in block and "}o--" not in block
            for entity in re.findall(r"^    (\S+) \{$", block, re.M):
                assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", entity), entity
    overview = pages[0]["content"]
    assert "## Cross-schema relationships" in overview
    assert 'transit__edge_nodes zero or one to zero or more ccm__edges : "edge_node_id"' in overview
    transit = pages[2]["content"]
    assert 'sites only one to zero or more edge_nodes : "site_id"' in transit


def test_viewpoints_render_configured_tables_and_name_absent_ones():
    config = projection.load_config(None)
    config["schemas"]["transit"] = {
        "description": None,
        "canonical": {"status": "NOT_YET_COMPARED", "reason": None, "source": None},
        "viewpoints": [{"name": "Topology", "description": "d", "tables": ["sites", "edge_nodes", "retired_table"]}],
    }
    transit = _render(config)[2]["content"]
    assert "### Viewpoint: Topology" in transit
    assert "Configured but absent from the deployed schema: `retired_table`" in transit


def test_canonical_availability_comes_only_from_configuration():
    provenance = projection.build_provenance(
        config={"schemas": {}}, structure=STRUCTURE, static_provenance={}, source_metadata={},
        extracted_at="t", generator=schema_catalogue.GENERATOR,
    )
    # Absent configuration never claims a canonical schema exists.
    assert {spec["canonical"]["status"] for spec in provenance["schemas"].values()} == {"NOT_YET_COMPARED"}
    assert {spec["basis"] for spec in provenance["schemas"].values()} == {"DEPLOYED_INTROSPECTION"}
    assert provenance["migration_ledger"]["status"] == "NOT_CAPTURED"


def test_pilot_configuration_states_d3_exactly():
    config = projection.load_config(ROOT / "config" / "schema-catalogue" / "charliehub_domains.yml")
    ccm = config["schemas"]["ccm"]["canonical"]
    transit = config["schemas"]["transit"]["canonical"]
    assert ccm["status"] == "NOT_AVAILABLE" and "genesis" in ccm["reason"]
    assert transit["status"] == "NOT_YET_COMPARED" and "migrate_schema.py" in transit["source"]
    assert all(viewpoint["tables"] for viewpoint in config["schemas"]["transit"]["viewpoints"])


def test_configuration_is_validated_fail_closed(tmp_path):
    bad = tmp_path / "bad.yml"
    bad.write_text("schemas:\n  ccm:\n    canonical:\n      status: PROBABLY\n", encoding="utf-8")
    with pytest.raises(projection.ConfigError):
        projection.load_config(bad)
    empty_viewpoint = tmp_path / "vp.yml"
    empty_viewpoint.write_text("schemas:\n  s:\n    viewpoints:\n      - name: x\n        tables: []\n", encoding="utf-8")
    with pytest.raises(projection.ConfigError):
        projection.load_config(empty_viewpoint)
    assert projection.load_config(tmp_path / "absent.yml") == {"schemas": {}}


def test_config_hash_tracks_presentation_but_not_structure():
    base = projection.config_hash({"schemas": {}}, {"environment": "a"})
    assert base == projection.config_hash({"schemas": {}}, {"environment": "a"})
    assert base != projection.config_hash({"schemas": {}}, {"environment": "b"})


def test_projection_document_carries_the_fingerprinted_structure_verbatim():
    fp = schema_catalogue.fingerprint(STRUCTURE)
    document = projection.build_document("pilot", "Pilot DB", STRUCTURE, fp, "c" * 64, 2)
    assert document["schemas"] is STRUCTURE
    assert schema_catalogue.fingerprint(document["schemas"]) == document["source_fingerprint"] == fp
    assert len(document["relations"]) == 3
    schema_catalogue.guard_projection(document, {"environment": "test"})  # clean input passes
