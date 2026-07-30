"""Regression cover for compatibility redirect SERVING (docplane#65).

DocPlane recorded page-move redirects in docs.redirects, folded them into the
corpus state-identity hash, and then never rendered them: runtime loaded the map
only to compute the identity and called generator.run() without it, and the
generator had no redirect emitter. A published move therefore reported success
everywhere — clean validation, PUBLISHED, certification CURRENT with
working == deployed — while the old URL returned 404.

Nothing in the existing suite could catch that, because every assertion was about
pages. These tests are about the routes a move leaves behind.

Like test_generated_release.py, anything that asserts the release BUILDS must use
the shipped split-config layout (MKDOCS_BASE_CONFIG in the image, MKDOCS_CONFIG_DIR
on a volume) or it proves nothing about production.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from app import generator

ROOT = Path(__file__).resolve().parents[2]
REPO_MKDOCS = ROOT / "mkdocs"

# The three routes left behind by the Issue #44 path-move canary, published
# 2026-07-29. These are the concrete URLs the defect broke in production.
CANARY_REDIRECTS = {
    "control-plane/correctness-domains.md": "control-plane/foundational/correctness-domains.md",
    "control-plane/ccm-data-model.md": "control-plane/specs/ccm-data-model.md",
    "control-plane/event-contract.md": "control-plane/specs/event-contract.md",
}


def _page(path: str, nav: str) -> dict:
    return {"path": path, "nav_path": nav, "content": f"# {path}\n\nbody\n", "version": 1}


def _canary_pages() -> list[dict]:
    return [
        _page("index.md", "Home"),
        _page("control-plane/foundational/correctness-domains.md", "Control Plane/Foundational/Three Correctness Domains"),
        _page("control-plane/specs/ccm-data-model.md", "Control Plane/Specs/CCM Data Model"),
        _page("control-plane/specs/event-contract.md", "Control Plane/Specs/Event Contract"),
    ]


def _split_layout(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    base_dir = tmp_path / "app-mkdocs"      # stands in for /app/mkdocs (image)
    config_dir = tmp_path / "data-mkdocs"   # stands in for /data/mkdocs (volume)
    content_dir = tmp_path / "data-docs"    # stands in for /data/docs (volume)
    staging = tmp_path / "staging"
    site = tmp_path / "site"
    shutil.copytree(REPO_MKDOCS, base_dir)
    for directory in (config_dir, content_dir):
        directory.mkdir()
    monkeypatch.setattr(generator, "MKDOCS_YML", base_dir / "mkdocs.yml")
    monkeypatch.setattr(generator, "MKDOCS_CONFIG_DIR", config_dir)
    monkeypatch.setattr(generator, "MKDOCS_NAV_YML", config_dir / "mkdocs.nav.yml")
    monkeypatch.setattr(generator, "DOCS_CONTENT_DIR", content_dir)
    monkeypatch.setattr(generator, "MANIFEST_PATH", content_dir / ".docplane-manifest.json")
    monkeypatch.setattr(generator, "STAGING_SITE_DIR", staging)
    monkeypatch.setattr(generator, "SITE_DIR", site)
    return config_dir, content_dir, site


# --------------------------------------------------------------------------
# Config plumbing: the parent must stay mergeable, the child must carry the map
# --------------------------------------------------------------------------

def test_parent_config_declares_plugins_as_a_mapping():
    """Guard the premise. As a list, a child plugins block replaces it wholesale
    and silently drops `search`, so the merge this design relies on is gone."""
    raw = (REPO_MKDOCS / "mkdocs.yml").read_text(encoding="utf-8")
    plugins = raw.split("plugins:", 1)[1].lstrip("\n")
    first = next(line for line in plugins.splitlines() if line.strip() and not line.strip().startswith("#"))
    assert not first.strip().startswith("- "), "plugins must be a mapping, not a list"


def test_child_config_carries_the_redirect_map(tmp_path, monkeypatch):
    config_dir, _, _ = _split_layout(tmp_path, monkeypatch)
    generator._write_nav([{"Home": "index.md"}], CANARY_REDIRECTS)
    payload = yaml.safe_load((config_dir / "mkdocs.nav.yml").read_text(encoding="utf-8"))
    assert payload["plugins"]["redirects"]["redirect_maps"] == dict(sorted(CANARY_REDIRECTS.items()))
    # Only the redirects plugin is restated; anything else must come from the parent.
    assert set(payload["plugins"]) == {"redirects"}


# --------------------------------------------------------------------------
# Validation: unsound redirect state fails publication, never omitted silently
# --------------------------------------------------------------------------

def test_no_redirect_may_be_emitted_over_an_active_page():
    pages = _canary_pages()
    with pytest.raises(generator.RedirectConflict) as excinfo:
        generator.validate_redirects({"index.md": "control-plane/specs/event-contract.md"}, pages)
    assert excinfo.value.kind == "source-is-active-page"


def test_redirect_to_a_missing_page_fails_publication():
    with pytest.raises(generator.RedirectConflict) as excinfo:
        generator.validate_redirects({"old/gone.md": "control-plane/never-existed.md"}, _canary_pages())
    assert excinfo.value.kind == "target-missing"


def test_redirect_chains_and_cycles_are_rejected():
    """Disjointness makes these unrepresentable under the operation contract;
    assert it so a contract change fails loudly instead of emitting a redirect
    that points at another redirect."""
    pages = _canary_pages()
    chain = {
        "a.md": "b.md",
        "b.md": "control-plane/specs/event-contract.md",
    }
    with pytest.raises(generator.RedirectConflict) as excinfo:
        generator.validate_redirects(chain, pages)
    assert excinfo.value.kind in {"chain", "target-missing"}

    with pytest.raises(generator.RedirectConflict):
        generator.validate_redirects({"a.md": "b.md", "b.md": "a.md"}, pages)
    with pytest.raises(generator.RedirectConflict) as excinfo:
        generator.validate_redirects({"a.md": "a.md"}, pages)
    assert excinfo.value.kind == "self-redirect"


def test_valid_canary_redirects_pass_validation():
    assert generator.validate_redirects(CANARY_REDIRECTS, _canary_pages()) == dict(sorted(CANARY_REDIRECTS.items()))


def test_rendered_html_path_matches_mkdocs_directory_urls():
    assert generator.rendered_html_path("a/b.md") == "a/b/index.html"
    assert generator.rendered_html_path("index.md") == "index.html"
    assert generator.rendered_html_path("a/index.md") == "a/index.html"


# --------------------------------------------------------------------------
# End-to-end: the routes actually exist in the built site
# --------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("mkdocs") is None, reason="mkdocs not installed")
def test_an_old_path_resolves_to_its_new_page(tmp_path, monkeypatch):
    _, _, site = _split_layout(tmp_path, monkeypatch)
    generator.run(_canary_pages(), redirects={"control-plane/correctness-domains.md": "control-plane/foundational/correctness-domains.md"})
    emitted = site / "control-plane/correctness-domains/index.html"
    assert emitted.is_file(), "old path produced no compatibility route"
    html = emitted.read_text(encoding="utf-8")
    assert "foundational/correctness-domains" in html
    assert 'http-equiv="refresh"' in html
    assert 'rel="canonical"' in html


@pytest.mark.skipif(shutil.which("mkdocs") is None, reason="mkdocs not installed")
def test_all_three_canary_redirects_are_served(tmp_path, monkeypatch):
    _, _, site = _split_layout(tmp_path, monkeypatch)
    result = generator.run(_canary_pages(), redirects=CANARY_REDIRECTS)
    assert result["redirects"] == 3
    for source, target in CANARY_REDIRECTS.items():
        emitted = site / generator.rendered_html_path(source)
        assert emitted.is_file(), f"no compatibility route for {source}"
        # the destination page is genuinely part of the same release
        assert (site / generator.rendered_html_path(target)).is_file()


@pytest.mark.skipif(shutil.which("mkdocs") is None, reason="mkdocs not installed")
def test_removing_a_redirect_removes_the_compatibility_route(tmp_path, monkeypatch):
    _, _, site = _split_layout(tmp_path, monkeypatch)
    generator.run(_canary_pages(), redirects=CANARY_REDIRECTS)
    route = site / "control-plane/ccm-data-model/index.html"
    assert route.is_file()

    remaining = {k: v for k, v in CANARY_REDIRECTS.items() if k != "control-plane/ccm-data-model.md"}
    generator.run(_canary_pages(), redirects=remaining)
    assert not route.exists(), "withdrawn redirect still served"
    # the routes that were kept are untouched
    assert (site / "control-plane/correctness-domains/index.html").is_file()


@pytest.mark.skipif(shutil.which("mkdocs") is None, reason="mkdocs not installed")
def test_a_release_cannot_certify_when_a_redirect_is_omitted_from_output(tmp_path, monkeypatch):
    """The defect's exact shape: recorded redirect, no artifact, success reported.

    Simulate the generator failing to emit by stripping the map from the child
    config after it is written. The build must fail rather than promote — that is
    what turns a silently incomplete release into a FAILED deployment, so
    certification can never reach CURRENT with a redirect unserved.
    """
    _, _, site = _split_layout(tmp_path, monkeypatch)
    original = generator._write_nav

    def drop_redirects(nav, redirects=None):
        original(nav, None)          # write the config WITHOUT the redirect map

    monkeypatch.setattr(generator, "_write_nav", drop_redirects)
    with pytest.raises(RuntimeError, match="redirect routes missing"):
        generator.run(_canary_pages(), redirects=CANARY_REDIRECTS)
    # nothing was promoted
    assert not (site / "control-plane/correctness-domains/index.html").exists()


@pytest.mark.skipif(shutil.which("mkdocs") is None, reason="mkdocs not installed")
def test_manifest_records_the_served_redirects(tmp_path, monkeypatch):
    _, content_dir, _ = _split_layout(tmp_path, monkeypatch)
    generator.run(_canary_pages(), redirects=CANARY_REDIRECTS)
    manifest = json.loads((content_dir / ".docplane-manifest.json").read_text(encoding="utf-8"))
    assert manifest["redirects"] == dict(sorted(CANARY_REDIRECTS.items()))


# --------------------------------------------------------------------------
# a redirect between a page and its index form is unrepresentable
# --------------------------------------------------------------------------

def test_rendered_url_collapses_page_and_index_forms():
    assert generator.rendered_url("a/b.md") == "/a/b/"
    assert generator.rendered_url("a/b/index.md") == "/a/b/"
    assert generator.rendered_url("index.md") == "/"
    assert generator.rendered_url("a/index.md") == "/a/"


def test_redirect_between_a_page_and_its_index_form_is_rejected():
    """Moving `x.md` to `x/index.md` keeps the public URL, so no compatibility
    route is needed - and one cannot exist. mkdocs-redirects would write its stub
    at exactly the path the moved page renders to, replacing the page with a
    document that redirects to itself. This must fail publication, not ship."""
    pages = _canary_pages() + [_page("control-plane/invariants/index.md",
                                     "Control Plane/Invariants/Index")]
    with pytest.raises(generator.RedirectConflict) as excinfo:
        generator.validate_redirects(
            {"control-plane/invariants.md": "control-plane/invariants/index.md"}, pages)
    assert excinfo.value.kind == "same-rendered-url"
    assert "/control-plane/invariants/" in str(excinfo.value)


def test_the_reverse_direction_is_rejected_too():
    pages = _canary_pages() + [_page("a/b.md", "A/B")]
    with pytest.raises(generator.RedirectConflict) as excinfo:
        generator.validate_redirects({"a/b/index.md": "a/b.md"}, pages)
    assert excinfo.value.kind == "same-rendered-url"


def test_an_ordinary_move_redirect_is_still_accepted():
    """Guard the guard: the new check must not reject legitimate redirects."""
    pages = _canary_pages()
    assert generator.validate_redirects(
        {"control-plane/correctness-domains.md":
         "control-plane/foundational/correctness-domains.md"}, pages)


@pytest.mark.skipif(shutil.which("mkdocs") is None, reason="mkdocs not installed")
def test_a_release_with_an_index_form_landing_page_builds_and_serves_once(tmp_path, monkeypatch):
    """The move H1 performs: a section landing at <section>/index.md, no redirect."""
    _, _, site = _split_layout(tmp_path, monkeypatch)
    pages = _canary_pages() + [
        _page("control-plane/invariants/index.md", "Control Plane/Invariants/Index"),
        _page("control-plane/invariants/i-x-1.md", "Control Plane/Invariants/I-X-1"),
    ]
    generator.run(pages, redirects={})
    landing = site / "control-plane/invariants/index.html"
    assert landing.is_file()
    # the landing is real content, not a redirect stub pointing at itself
    html = landing.read_text(encoding="utf-8")
    assert 'http-equiv="refresh"' not in html
    assert (site / "control-plane/invariants/i-x-1/index.html").is_file()


# --------------------------------------------------------------------------
# one rendered URL, one output
# --------------------------------------------------------------------------

def test_two_active_pages_on_one_url_fail_validation():
    """a/b.md and a/b/index.md both render to /a/b/; one would be unreachable."""
    pages = _canary_pages() + [_page("a/b.md", "A/B"), _page("a/b/index.md", "A/B/Index")]
    with pytest.raises(generator.RouteConflict) as excinfo:
        generator.validate_routes(pages)
    assert excinfo.value.collisions == {"/a/b/": ["a/b.md", "a/b/index.md"]}


def test_the_reverse_insertion_order_fails_identically():
    forward = _canary_pages() + [_page("a/b.md", "A/B"), _page("a/b/index.md", "A/B/Index")]
    reverse = _canary_pages() + [_page("a/b/index.md", "A/B/Index"), _page("a/b.md", "A/B")]
    with pytest.raises(generator.RouteConflict) as first:
        generator.validate_routes(forward)
    with pytest.raises(generator.RouteConflict) as second:
        generator.validate_routes(reverse)
    assert first.value.collisions == second.value.collisions


def test_one_page_alone_passes():
    generator.validate_routes(_canary_pages() + [_page("a/b.md", "A/B")])
    generator.validate_routes(_canary_pages() + [_page("a/b/index.md", "A/B/Index")])


def test_ordinary_distinct_pages_pass():
    generator.validate_routes(_canary_pages() + [
        _page("a/b.md", "A/B"), _page("a/c.md", "A/C"), _page("a/c/d.md", "A/C/D")])


def test_a_redirect_whose_url_belongs_to_another_active_page_fails():
    """The shape that overwrote a category index in production: the moved page's
    old URL was another page's URL, so its stub replaced that page."""
    pages = _canary_pages() + [_page("a/b/index.md", "A/B/Index"),
                               _page("a/c.md", "A/C")]
    with pytest.raises(generator.RouteConflict) as excinfo:
        generator.validate_routes(pages, {"a/b.md": "a/c.md"})
    assert excinfo.value.collisions == {"/a/b/": ["a/b/index.md", "redirect:a/b.md"]}


def test_two_redirects_claiming_one_url_fail():
    pages = _canary_pages() + [_page("a/c.md", "A/C")]
    with pytest.raises(generator.RouteConflict):
        generator.validate_routes(pages, {"a/b.md": "a/c.md", "a/b/index.md": "a/c.md"})


def test_an_ordinary_redirect_passes_route_validation():
    pages = _canary_pages()
    generator.validate_routes(pages, {
        "control-plane/correctness-domains.md":
            "control-plane/foundational/correctness-domains.md"})


def test_redirects_remain_governed_by_the_same_rendered_url_guard():
    """Route uniqueness does not replace the redirect-specific guard; the more
    specific error must still surface first for a page/index redirect."""
    pages = _canary_pages() + [_page("a/b/index.md", "A/B/Index")]
    with pytest.raises(generator.RedirectConflict) as excinfo:
        generator.validate_redirects({"a/b.md": "a/b/index.md"}, pages)
    assert excinfo.value.kind == "same-rendered-url"


@pytest.mark.skipif(shutil.which("mkdocs") is None, reason="mkdocs not installed")
def test_an_invalid_release_cannot_be_generated_or_promoted(tmp_path, monkeypatch):
    """A colliding corpus must fail run() and promote nothing, so it can never
    reach CURRENT."""
    _, _, site = _split_layout(tmp_path, monkeypatch)
    pages = _canary_pages() + [_page("a/b.md", "A/B"), _page("a/b/index.md", "A/B/Index")]
    with pytest.raises(generator.RouteConflict):
        generator.run(pages)
    assert not (site / "a" / "b" / "index.html").exists()


def test_a_dry_run_also_fails_on_a_colliding_corpus():
    pages = _canary_pages() + [_page("a/b.md", "A/B"), _page("a/b/index.md", "A/B/Index")]
    with pytest.raises(generator.RouteConflict):
        generator.run(pages, dry_run=True)


def test_the_corrected_corpus_shape_passes():
    """Post-remediation shape: index plus a relocated page at its own path."""
    pages = _canary_pages() + [
        _page("control-plane/topology-invariants/index.md", "Control Plane/Topology Invariants/Index"),
        _page("control-plane/specs/wg-mesh-topology-correctness.md",
              "Control Plane/Specs/WG Mesh Topology Correctness"),
    ]
    generator.validate_routes(pages, {})
