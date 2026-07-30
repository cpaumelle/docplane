"""Regression suite for corpus link discovery, rewriting and verification.

Every case here corresponds to behaviour a real corpus migration depends on, or
to a defect that actually occurred. The four historical defects are called out by
name so they cannot be optimised away as redundant:

  (a) line-oriented parsing under-reports references;
  (b) a rewrite silently changing more than link destinations;
  (c) a navigation label derived from a title corrupting the tree;
  (d) an expected path hardcoded to one migration's destination directory.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from migration.links import (
    AmbiguousIdentifierError,
    ParserUncertaintyError,
    PreservationReason,
    expected_redirect_hop,
    find_links,
    inline_code_spans,
    mask_links,
    blocking_page_for_section,
    find_duplicate_routes,
    find_page_section_conflicts,
    nav_leaf,
    nav_path_from_leaf,
    page_url,
    plan_corpus,
    plan_rewrites,
    plan_source_move,
    redirect_is_representable,
    protected_lines,
    resolve,
    resolve_identifier_prefixes,
    retarget,
    retarget_nav,
    scan_stale_references,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "scripts" / "docplane_links.py"

MAP = {
    "control-plane/doctrine.md": "control-plane/foundational/doctrine.md",
    "control-plane/rfc/x.md": "control-plane/design/x.md",
}


def plan(page, text, mapping=MAP):
    return plan_rewrites(page, text, mapping)


# --- (a) multiline parsing ---------------------------------------------------

def test_link_label_and_destination_on_separate_lines():
    """Defect (a): a per-line parser drops this link and reports success."""
    result = plan("control-plane/index.md", "See the [governing\ndoctrine](doctrine.md).\n")
    assert len(result.rewrites) == 1
    assert result.rewrites[0].new_target == "foundational/doctrine.md"
    assert "foundational/doctrine.md" in result.content


def test_multiline_link_is_reported_as_spanning_lines():
    refs = list(find_links("p.md", "[a\nb](x.md)\n"))
    assert refs[0].spans_lines and refs[0].line == 1 and refs[0].end_line == 2


def test_multiline_link_still_satisfies_the_masking_proof():
    text = "[a\nb](doctrine.md)\n"
    assert mask_links(plan("control-plane/index.md", text).content) == mask_links(text)


# --- link forms --------------------------------------------------------------

def test_relative_destination_stays_relative():
    assert plan("control-plane/index.md", "[d](doctrine.md)\n").rewrites[0].new_target == \
        "foundational/doctrine.md"


def test_relative_destination_from_a_subdirectory():
    result = plan("control-plane/topology-invariants/i-1.md", "[d](../doctrine.md)\n")
    assert result.rewrites[0].new_target == "../foundational/doctrine.md"


def test_root_absolute_destination_stays_root_absolute():
    result = plan("network/x.md", "[d](/control-plane/doctrine.md)\n")
    assert result.rewrites[0].new_target == "/control-plane/foundational/doctrine.md"


def test_anchor_fragment_is_carried_through():
    result = plan("control-plane/index.md", "[d](doctrine.md#authority)\n")
    assert result.rewrites[0].new_target == "foundational/doctrine.md#authority"


def test_root_absolute_with_anchor():
    result = plan("network/x.md", "[d](/control-plane/doctrine.md#a-b)\n")
    assert result.rewrites[0].new_target == "/control-plane/foundational/doctrine.md#a-b"


def test_multiple_links_on_one_line():
    result = plan("control-plane/index.md",
                  "[a](doctrine.md) and [b](rfc/x.md) and [c](untouched.md)\n")
    assert len(result.rewrites) == 2
    assert "untouched.md" in result.content


def test_optional_link_title_survives():
    result = plan("control-plane/index.md", '[d](doctrine.md "The doctrine")\n')
    assert result.rewrites[0].new_target == "foundational/doctrine.md"
    assert '"The doctrine"' in result.content


def test_external_and_non_markdown_links_are_excluded():
    text = "[x](https://example.invalid/a.md) [y](../img.png) [z](#anchor)\n"
    result = plan("control-plane/index.md", text)
    assert not result.rewrites and not result.preservations and not result.changed
    assert resolve("p.md", "https://example.invalid/a.md") is None


# --- protected contexts ------------------------------------------------------

def test_fenced_code_reference_is_preserved_and_classified():
    result = plan("control-plane/index.md", "a\n\n```\n[d](doctrine.md)\n```\n\nb\n")
    assert not result.rewrites and not result.changed
    assert result.preservations[0].reason is PreservationReason.FENCED_CODE


def test_tilde_fence_is_honoured():
    result = plan("control-plane/index.md", "~~~\n[d](doctrine.md)\n~~~\n")
    assert result.preservations[0].reason is PreservationReason.FENCED_CODE


def test_blockquote_reference_is_preserved_and_classified():
    result = plan("control-plane/index.md", "> quoted [d](doctrine.md)\n")
    assert result.preservations[0].reason is PreservationReason.BLOCKQUOTE


@pytest.mark.parametrize("page", [
    "programme/evidence/x.md",
    "operations/incidents/inc-1.md",
    "operations/security/redaction-remediation-register.md",
    "x/control-plane-active-inventory.md",
])
def test_evidence_surfaces_are_preserved_wholesale(page):
    result = plan(page, "[d](/control-plane/doctrine.md)\n")
    assert not result.rewrites
    assert result.preservations[0].reason is PreservationReason.EVIDENCE_SURFACE


def test_ordinary_surfaces_are_not_treated_as_evidence():
    result = plan("control-plane/index.md", "[d](doctrine.md)\n")
    assert result.rewrites and not result.preservations


# --- (b) the link-only proof -------------------------------------------------

def test_masking_proof_holds_across_a_realistic_body():
    text = ("# Title {#title}\n\nProse with  double  spaces and [a](doctrine.md).\n\n"
            "## Heading {#h2}\n\n> quoted [b](doctrine.md)\n\n"
            "```\ncode [c](doctrine.md)\n```\n\n| a | b |\n|---|---|\n| [x](rfc/x.md) | y |\n")
    result = plan("control-plane/index.md", text)
    assert mask_links(result.content) == mask_links(text)
    assert len(result.rewrites) == 2 and len(result.preservations) == 2
    for fragment in ("# Title {#title}", "## Heading {#h2}", "double  spaces", "| a | b |"):
        assert fragment in result.content


def test_rewriter_refuses_a_body_that_would_change_beyond_destinations(monkeypatch):
    """Defect (b): guard the guard - corruption must abort, not ship."""
    import migration.links as links
    monkeypatch.setattr(links, "retarget", lambda *a: "x)  INJECTED  [y](z")
    with pytest.raises(RuntimeError, match="more than link destinations"):
        plan("control-plane/index.md", "[d](doctrine.md)\n")


def test_mask_distinguishes_bodies_that_differ_outside_destinations():
    assert mask_links("[a](one.md)\n") != mask_links("[different](one.md)\n")


# --- zero-op plans -----------------------------------------------------------

def test_zero_op_plan_leaves_content_none_and_body_untouched():
    result = plan("control-plane/index.md", "# T\n\n[a](other.md) [b](/elsewhere.md)\n")
    assert not result.changed and result.content is None and not result.rewrites


def test_zero_op_corpus_plan_returns_no_plans():
    assert plan_corpus({"a.md": "# A\n\n[x](b.md)\n"}, MAP) == []


def test_corpus_plan_is_deterministic_and_path_ordered():
    corpus = {"z.md": "[d](control-plane/doctrine.md)\n",
              "a.md": "[d](control-plane/doctrine.md)\n"}
    assert [p.page for p in plan_corpus(corpus, MAP)] == ["a.md", "z.md"]


# --- (c) navigation ----------------------------------------------------------

def test_nav_leaf_comes_from_the_navigation_path():
    assert nav_leaf("Control Plane/Anti-Patterns/Shadow Inventory") == "Shadow Inventory"


def test_curated_nav_leaf_is_retained_when_resectioned():
    assert retarget_nav("Control Plane/Anti-Patterns/Shadow Inventory",
                        "Control Plane/Foundational") == \
        "Control Plane/Foundational/Shadow Inventory"


def test_a_title_containing_a_separator_cannot_become_a_navigation_leaf():
    """Defect (c): a title like 'DESIGN: docs-api bulk/rename' would silently
    invent an extra navigation level and discard the curated label."""
    title = "DESIGN: docs-api bulk/rename"
    curated = "Control Plane/Specs/Docs Rename (Design)"
    # the curated leaf survives a re-sectioning and the title is never consulted
    assert retarget_nav(curated, "Control Plane/Design") == \
        "Control Plane/Design/Docs Rename (Design)"
    # and the title itself cannot be used as a leaf: it would add a level
    with pytest.raises(ValueError, match="exactly one level"):
        nav_path_from_leaf("Control Plane/Design", title)
    # a separator-free title is accepted, so the guard is about structure only
    assert nav_path_from_leaf("Control Plane/Design", "Docs Rename") == \
        "Control Plane/Design/Docs Rename"


def test_nav_leaf_rejects_an_empty_navigation_path():
    with pytest.raises(ValueError):
        nav_leaf("   ")


# --- (d) derived routes ------------------------------------------------------

def test_expected_hop_is_derived_from_depth_not_a_directory_name():
    """Defect (d): no hardcoded cohort directory may satisfy this."""
    assert expected_redirect_hop("control-plane/doctrine.md",
                                 "control-plane/foundational/doctrine.md") == \
        "../foundational/doctrine/"
    assert expected_redirect_hop("control-plane/anti-patterns/shadow.md",
                                 "control-plane/foundational/shadow.md") == \
        "../../foundational/shadow/"
    assert expected_redirect_hop("a/b.md", "c/b.md") == "../../c/b/"


def test_expected_hop_differs_between_cohorts_with_the_same_leaf():
    """A verifier hardcoding one destination would pass both; these must differ."""
    specs = expected_redirect_hop("control-plane/x.md", "control-plane/specs/x.md")
    design = expected_redirect_hop("control-plane/x.md", "control-plane/design/x.md")
    assert specs != design and "specs" in specs and "design" in design


def test_page_url_handles_index_pages():
    assert page_url("a/index.md") == "/a/"
    assert page_url("index.md") == "/"
    assert page_url("a/b.md") == "/a/b/"


# --- identity ----------------------------------------------------------------

def test_identifier_prefixes_resolve_when_unique():
    ids = ["aaaaaaaa-1111", "bbbbbbbb-2222"]
    assert resolve_identifier_prefixes(ids)["aaaaaaaa"] == "aaaaaaaa-1111"


def test_identifier_prefix_collision_aborts():
    with pytest.raises(AmbiguousIdentifierError, match="collision"):
        resolve_identifier_prefixes(["aaaaaaaa-1111", "aaaaaaaa-2222"])


def test_identifier_prefix_collision_message_names_the_prefix():
    with pytest.raises(AmbiguousIdentifierError, match="aaaaaaaa"):
        resolve_identifier_prefixes(["aaaaaaaa-1", "aaaaaaaa-2", "cccccccc-3"])


# --- parser uncertainty ------------------------------------------------------

def test_unterminated_fence_fails_closed():
    with pytest.raises(ParserUncertaintyError, match="unterminated"):
        protected_lines("intro\n\n```\n[d](doctrine.md)\nno closing fence\n")


def test_planning_a_document_with_an_unterminated_fence_fails_closed():
    with pytest.raises(ParserUncertaintyError):
        plan("control-plane/index.md", "```\n[d](doctrine.md)\n")


def test_scan_propagates_parser_uncertainty():
    with pytest.raises(ParserUncertaintyError):
        scan_stale_references({"a.md": "```\n[d](/control-plane/doctrine.md)\n"},
                              MAP)


# --- scanning and reconciliation ---------------------------------------------

def test_scan_splits_unintended_from_preserved():
    corpus = {"control-plane/index.md": "[a](doctrine.md)\n\n> [b](doctrine.md)\n"}
    unintended, preserved = scan_stale_references(corpus, MAP)
    assert [r.target for r in unintended] == ["doctrine.md"]
    assert [p.reason for p in preserved] == [PreservationReason.BLOCKQUOTE]


def test_preservations_reconcile_exactly_with_a_scan_after_rewriting():
    corpus = {
        "control-plane/index.md": "[a](doctrine.md)\n\n> quoted [b](doctrine.md)\n",
        "operations/incidents/i.md": "[c](/control-plane/doctrine.md)\n",
    }
    plans = plan_corpus(corpus, MAP)
    declared = {p.key() for plan in plans for p in plan.preservations}
    rewritten = {path: (next((pl.content for pl in plans if pl.page == path and pl.changed), text))
                 for path, text in corpus.items()}
    unintended, preserved = scan_stale_references(rewritten, MAP)
    assert not unintended
    assert {p.key() for p in preserved} == declared


def test_redirects_and_rewritten_links_coexist():
    """A rewritten reference points at the new path; the old path still has a hop."""
    corpus = {"control-plane/index.md": "[d](doctrine.md)\n"}
    plans = plan_corpus(corpus, MAP)
    assert "foundational/doctrine.md" in plans[0].content
    hop = expected_redirect_hop("control-plane/doctrine.md",
                                "control-plane/foundational/doctrine.md")
    unintended, _ = scan_stale_references({"control-plane/index.md": plans[0].content}, MAP)
    assert not unintended and hop == "../foundational/doctrine/"


# --- receipts ----------------------------------------------------------------

def test_plan_receipt_is_json_serialisable_and_stable():
    result = plan("control-plane/index.md", "[a](doctrine.md)\n\n> [b](doctrine.md)\n")
    first = json.dumps(result.as_dict(), sort_keys=True)
    assert json.dumps(plan("control-plane/index.md",
                           "[a](doctrine.md)\n\n> [b](doctrine.md)\n").as_dict(),
                      sort_keys=True) == first
    payload = json.loads(first)
    assert payload["page"] == "control-plane/index.md" and payload["changed"] is True
    assert payload["rewrites"][0]["new_target"] == "foundational/doctrine.md"
    assert payload["preservations"][0]["reason"] == "blockquote"


# --- CLI ---------------------------------------------------------------------

def _run(args, **kw):
    return subprocess.run([sys.executable, str(CLI)] + args, capture_output=True,
                          text=True, **kw)


@pytest.fixture()
def workspace(tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "control-plane").mkdir(parents=True)
    (corpus / "control-plane" / "index.md").write_text(
        "[d](doctrine.md)\n\n> quoted [d](doctrine.md)\n", encoding="utf-8")
    (corpus / "control-plane" / "doctrine.md").write_text("# Doctrine\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"moves": [
        {"from_path": "control-plane/doctrine.md",
         "to_path": "control-plane/foundational/doctrine.md"}]}), encoding="utf-8")
    return corpus, manifest, tmp_path


def test_cli_plan_is_read_only_and_emits_a_receipt(workspace):
    corpus, manifest, tmp = workspace
    before = (corpus / "control-plane" / "index.md").read_text(encoding="utf-8")
    receipt = tmp / "r.json"
    result = _run(["plan", "--corpus", str(corpus), "--manifest", str(manifest),
                   "--json", str(receipt)])
    assert result.returncode == 0, result.stderr
    assert (corpus / "control-plane" / "index.md").read_text(encoding="utf-8") == before
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["rewrites"] == 1 and payload["preserved"] == 1


def test_cli_apply_defaults_to_dry_run(workspace):
    corpus, manifest, _ = workspace
    before = (corpus / "control-plane" / "index.md").read_text(encoding="utf-8")
    result = _run(["apply", "--corpus", str(corpus), "--manifest", str(manifest)])
    assert result.returncode == 0 and "DRY RUN" in result.stdout
    assert (corpus / "control-plane" / "index.md").read_text(encoding="utf-8") == before


def test_cli_apply_with_write_rewrites_only_destinations(workspace):
    corpus, manifest, _ = workspace
    before = (corpus / "control-plane" / "index.md").read_text(encoding="utf-8")
    assert _run(["apply", "--corpus", str(corpus), "--manifest", str(manifest),
                 "--write"]).returncode == 0
    after = (corpus / "control-plane" / "index.md").read_text(encoding="utf-8")
    assert after != before and mask_links(after) == mask_links(before)
    assert "foundational/doctrine.md" in after


def test_cli_scan_flags_unintended_references_with_exit_1(workspace):
    corpus, manifest, _ = workspace
    result = _run(["scan", "--corpus", str(corpus), "--manifest", str(manifest)])
    assert result.returncode == 1 and "unintended stale references: 1" in result.stdout


def test_cli_scan_is_clean_after_apply(workspace):
    corpus, manifest, _ = workspace
    _run(["apply", "--corpus", str(corpus), "--manifest", str(manifest), "--write"])
    result = _run(["scan", "--corpus", str(corpus), "--manifest", str(manifest)])
    assert result.returncode == 0 and "unintended stale references: 0" in result.stdout


def test_cli_scan_reconciles_declared_preservations(workspace):
    corpus, manifest, tmp = workspace
    _run(["apply", "--corpus", str(corpus), "--manifest", str(manifest), "--write"])
    declared = tmp / "declared.json"
    declared.write_text(json.dumps({"preserved": [
        {"page": "control-plane/index.md", "line": 3, "target": "doctrine.md"}]}),
        encoding="utf-8")
    ok = _run(["scan", "--corpus", str(corpus), "--manifest", str(manifest),
               "--expect-preserved", str(declared)])
    assert ok.returncode == 0 and "reconciles with declaration: True" in ok.stdout

    declared.write_text(json.dumps({"preserved": []}), encoding="utf-8")
    bad = _run(["scan", "--corpus", str(corpus), "--manifest", str(manifest),
                "--expect-preserved", str(declared)])
    assert bad.returncode == 1 and "found but not declared" in bad.stdout


def test_cli_routes_derives_hops(workspace):
    _, manifest, _ = workspace
    result = _run(["routes", "--manifest", str(manifest)])
    assert result.returncode == 0 and "hop=../foundational/doctrine/" in result.stdout


def test_cli_rejects_a_no_op_move(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"moves": [{"from_path": "a.md", "to_path": "a.md"}]}),
                        encoding="utf-8")
    result = _run(["routes", "--manifest", str(manifest)])
    assert result.returncode == 2 and "no-op" in result.stderr


def test_cli_rejects_duplicate_from_paths(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"moves": [
        {"from_path": "a.md", "to_path": "b.md"},
        {"from_path": "a.md", "to_path": "c.md"}]}), encoding="utf-8")
    result = _run(["routes", "--manifest", str(manifest)])
    assert result.returncode == 2 and "duplicate" in result.stderr


def test_cli_fails_closed_on_parser_uncertainty(workspace):
    corpus, manifest, _ = workspace
    (corpus / "control-plane" / "broken.md").write_text(
        "```\n[d](doctrine.md)\n", encoding="utf-8")
    result = _run(["plan", "--corpus", str(corpus), "--manifest", str(manifest)])
    assert result.returncode == 1 and "unterminated" in result.stderr


def test_cli_accepts_a_richer_manifest_with_extra_keys(tmp_path, workspace):
    corpus, _, tmp = workspace
    manifest = tmp / "rich.json"
    manifest.write_text(json.dumps({"cohort": "example", "moves": [
        {"from_path": "control-plane/doctrine.md",
         "to_path": "control-plane/foundational/doctrine.md",
         "resource_id": "x", "reason": "taxonomy", "nav_path": "A/B"}]}), encoding="utf-8")
    assert _run(["plan", "--corpus", str(corpus), "--manifest", str(manifest)]).returncode == 0


# --- moving a page: its own outbound links -----------------------------------

def test_source_move_rewrites_relative_outbound_links_to_resolve_unchanged():
    """A page moved one level deeper silently re-points every relative link."""
    text = "[a](sibling.md) and [b](../ops/x.md) and [c](sub/y.md)\n"
    result = plan_source_move("control-plane/invariants.md",
                              "control-plane/invariants/index.md", text)
    assert [r.new_target for r in result.rewrites] == \
        ["../sibling.md", "../../ops/x.md", "../sub/y.md"]
    # every link still resolves to exactly what it meant before
    for r in result.rewrites:
        assert resolve("control-plane/invariants/index.md", r.new_target) == r.resolved_old


def test_source_move_preserves_anchors_and_titles():
    text = '[a](sibling.md#sec "T")\n'
    result = plan_source_move("control-plane/invariants.md",
                              "control-plane/invariants/index.md", text)
    assert result.rewrites[0].new_target == "../sibling.md#sec"
    assert '"T"' in result.content


def test_source_move_leaves_root_absolute_and_external_links_alone():
    text = "[a](/control-plane/x.md) [b](https://example.invalid/y.md) [c](img.png)\n"
    result = plan_source_move("control-plane/invariants.md",
                              "control-plane/invariants/index.md", text)
    assert not result.rewrites and not result.changed and result.content is None


def test_source_move_skips_links_that_already_resolve_identically():
    """Same-depth rename: nothing to do, body must stay byte-identical."""
    text = "[a](sibling.md)\n"
    result = plan_source_move("control-plane/a.md", "control-plane/b.md", text)
    assert not result.rewrites and result.content is None


def test_source_move_preserves_fenced_and_quoted_links():
    text = "```\n[a](sibling.md)\n```\n\n> [b](sibling.md)\n"
    result = plan_source_move("control-plane/invariants.md",
                              "control-plane/invariants/index.md", text)
    assert not result.rewrites
    assert {p.reason for p in result.preservations} == \
        {PreservationReason.FENCED_CODE, PreservationReason.BLOCKQUOTE}


def test_source_move_satisfies_the_masking_proof():
    text = "# T {#t}\n\nprose  spaced [a](sibling.md) more\n"
    result = plan_source_move("control-plane/invariants.md",
                              "control-plane/invariants/index.md", text)
    assert mask_links(result.content) == mask_links(text)
    assert "# T {#t}" in result.content and "prose  spaced" in result.content


def test_source_move_rejects_a_no_op_path_change():
    with pytest.raises(ValueError, match="must change"):
        plan_source_move("a.md", "a.md", "[x](y.md)\n")


def test_source_move_handles_a_multiline_link():
    text = "[label\nspanning](sibling.md)\n"
    result = plan_source_move("control-plane/invariants.md",
                              "control-plane/invariants/index.md", text)
    assert result.rewrites[0].new_target == "../sibling.md"


# --- page/section conflicts ---------------------------------------------------

def test_page_section_conflict_is_detected():
    navs = {"a.md": "Top/Section", "b.md": "Top/Section/Child"}
    conflicts = find_page_section_conflicts(navs)
    assert len(conflicts) == 1
    assert conflicts[0]["nav_path"] == "Top/Section"
    assert conflicts[0]["pages"] == ["a.md"] and conflicts[0]["children"] == ["b.md"]


def test_no_conflict_when_a_section_holds_only_children():
    navs = {"i.md": "Top/Section/Index", "b.md": "Top/Section/Child"}
    assert find_page_section_conflicts(navs) == []


def test_blocking_page_for_section_names_the_occupier():
    navs = {"control-plane/invariants.md": "Control Plane/Invariants",
            "other.md": "Control Plane/Other"}
    assert blocking_page_for_section(navs, "Control Plane/Invariants") == \
        "control-plane/invariants.md"
    assert blocking_page_for_section(navs, "Control Plane/Nothing") is None


def test_landing_page_resolves_the_conflict():
    """Moving the occupier to <section>/index.md clears the block."""
    before = {"control-plane/invariants.md": "Control Plane/Invariants",
              "control-plane/invariants/i-x.md": "Control Plane/Invariants/I-X"}
    assert find_page_section_conflicts(before)
    after = {"control-plane/invariants/index.md": "Control Plane/Invariants/Index",
             "control-plane/invariants/i-x.md": "Control Plane/Invariants/I-X"}
    assert find_page_section_conflicts(after) == []


# --- redirect representability and duplicate routes ---------------------------

def test_a_redirect_between_page_and_its_index_form_is_not_representable():
    """Both render to the same URL; a stub would overwrite the page itself."""
    assert page_url("a/b.md") == page_url("a/b/index.md") == "/a/b/"
    assert not redirect_is_representable("a/b.md", "a/b/index.md")
    assert not redirect_is_representable("a/b/index.md", "a/b.md")


def test_an_ordinary_move_redirect_is_representable():
    assert redirect_is_representable("a/b.md", "a/c/b.md")


def test_duplicate_routes_are_detected():
    dupes = find_duplicate_routes(["a/b.md", "a/b/index.md", "a/c.md"])
    assert len(dupes) == 1 and dupes[0]["url"] == "/a/b/"
    assert dupes[0]["paths"] == ["a/b.md", "a/b/index.md"]


def test_distinct_paths_produce_no_duplicate_routes():
    assert find_duplicate_routes(["a/b.md", "a/c.md", "index.md"]) == []


# --- inline code is quoted text, not a hyperlink -----------------------------

def test_a_link_inside_inline_code_is_preserved_not_rewritten():
    """`[x](a.md)` renders as literal text. Rewriting it edits prose."""
    result = plan("control-plane/index.md", "See `[x](doctrine.md)` for the form.\n")
    assert not result.rewrites and not result.changed
    assert result.preservations[0].reason is PreservationReason.INLINE_CODE


def test_a_quoted_historical_link_is_not_falsified():
    """The real case: prose quoting a link precisely because it used to be wrong.
    Rewriting the quotation makes the sentence contradict itself."""
    text = ("The earlier `[I-3](doctrine.md)` link here resolved to nothing "
            "- corrected later.\n")
    result = plan("control-plane/index.md", text)
    assert not result.changed and text == result.original
    assert result.preservations[0].reason is PreservationReason.INLINE_CODE


def test_a_real_link_and_a_quoted_link_on_one_line_are_treated_differently():
    text = "See the [catalog](doctrine.md) - the earlier `[I-3](doctrine.md)` resolved to nothing.\n"
    result = plan("control-plane/index.md", text)
    assert len(result.rewrites) == 1 and len(result.preservations) == 1
    assert result.rewrites[0].new_target == "foundational/doctrine.md"
    assert "`[I-3](doctrine.md)`" in result.content, "quotation must survive verbatim"


def test_source_move_also_preserves_inline_code_links():
    result = plan_source_move("control-plane/invariants.md",
                              "control-plane/invariants/index.md",
                              "example: `[x](sibling.md)`\n")
    assert not result.rewrites
    assert result.preservations[0].reason is PreservationReason.INLINE_CODE


def test_scan_classifies_inline_code_references_as_preserved():
    corpus = {"control-plane/index.md": "quoted `[x](doctrine.md)` here\n"}
    unintended, preserved = scan_stale_references(corpus, MAP)
    assert not unintended
    assert preserved[0].reason is PreservationReason.INLINE_CODE


def test_inline_code_spans_do_not_cross_lines():
    spans = inline_code_spans("a `one` b\nc `two` d\n")
    assert len(spans) == 2


def test_an_unmatched_backtick_protects_nothing():
    """A stray backtick must not silently protect the rest of the document."""
    result = plan("control-plane/index.md", "stray ` tick then [x](doctrine.md)\n")
    assert len(result.rewrites) == 1 and not result.preservations
