"""Link-integrity signal in the structural corpus model.

Every case here is a resolution rule the served site actually applies. The
false-positive classes (application routes, directory URLs served by index.md,
redirected paths, archived-but-alive targets) are each pinned by a negative
test, because a link checker that cries wolf gets muted and then it protects
nothing.
"""
from __future__ import annotations

import pytest

from app.corpus_structure import (
    APP_ROUTE_PREFIXES,
    analyse_links,
    build,
    follow_redirects,
    resolve_link,
)


def page(path, content="", status="active", **extra):
    row = {
        "path": path,
        "content": content,
        "status": status,
        "title": extra.pop("title", path),
        "resource_id": f"rid-{path}",
        "revision": f"rev-{path}",
    }
    row.update(extra)
    return row


# --------------------------------------------------------------------------
# resolve_link
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "source,target,expected",
    [
        # sibling, relative
        ("a/b/page.md", "other.md", "a/b/other"),
        # parent traversal
        ("a/b/page.md", "../sibling.md", "a/sibling"),
        # absolute from corpus root
        ("a/b/page.md", "/top/thing.md", "top/thing"),
        # directory-style link keeps the directory id; index fallback is applied
        # by the caller, not here
        ("a/b/page.md", "../../top/", "top"),
        # anchors and queries are not part of the page id
        ("a/page.md", "other.md#section", "a/other"),
        ("a/page.md", "other.md?v=2", "a/other"),
        # a link escaping the corpus root clamps to root, matching the served site
        ("a/page.md", "../../../escape.md", "escape"),
        # root itself
        ("a/page.md", "../", "index"),
    ],
)
def test_resolve_link(source, target, expected):
    assert resolve_link(source, target) == expected


def test_resolve_link_ignores_empty_target():
    assert resolve_link("a/page.md", "") is None
    assert resolve_link("a/page.md", "#anchor-only") is None


# --------------------------------------------------------------------------
# follow_redirects
# --------------------------------------------------------------------------

def test_follow_redirects_walks_a_chain():
    redirects = {"old.md": "middle.md", "middle.md": "new.md"}
    assert follow_redirects("old.md", redirects) == "new.md"


def test_follow_redirects_returns_none_on_cycle():
    # A cycle resolves nowhere. It must stay broken, never loop and never be
    # waved through as though it landed somewhere.
    redirects = {"a.md": "b.md", "b.md": "a.md"}
    assert follow_redirects("a.md", redirects) is None


def test_follow_redirects_passes_through_unknown():
    assert follow_redirects("free.md", {}) == "free.md"


# --------------------------------------------------------------------------
# analyse_links — the false-positive classes
# --------------------------------------------------------------------------

def test_application_routes_are_not_broken_pages():
    # /dashboard/ is served by the app, not the corpus. Reporting it as a
    # dangling page reference is the single loudest false positive available.
    active = [page("index.md", "See the [dashboard](/dashboard/#work).")]
    result = analyse_links(active, [], {})
    assert result["counts"]["broken"] == 0
    assert result["counts"]["application_route"] == 1


def test_every_app_route_prefix_is_honoured():
    active = [
        page("index.md", " ".join(f"[x](/{prefix}/)" for prefix in sorted(APP_ROUTE_PREFIXES)))
    ]
    result = analyse_links(active, [], {})
    assert result["counts"]["broken"] == 0
    assert result["counts"]["application_route"] == len(APP_ROUTE_PREFIXES)


def test_directory_link_resolves_through_index_page():
    # `foo/` is served by `foo/index.md`; without the index fallback this reads
    # as broken.
    active = [
        page("guide.md", "See [the section](/section/)."),
        page("section/index.md"),
    ]
    result = analyse_links(active, [], {})
    assert result["counts"]["broken"] == 0
    assert result["counts"]["resolved_active"] == 1


def test_redirected_link_is_not_broken():
    active = [
        page("guide.md", "See [moved](/old/place.md)."),
        page("new/place.md"),
    ]
    result = analyse_links(active, [], {"old/place.md": "new/place.md"})
    assert result["counts"]["broken"] == 0
    assert result["counts"]["resolved_via_redirect"] == 1


def test_archived_target_is_advisory_not_broken():
    # Citing archived doctrine is usually deliberate. It is reported, separately.
    active = [page("guide.md", "See [history](/legacy/thing.md).")]
    archived = [page("legacy/thing.md", status="archived")]
    result = analyse_links(active, archived, {})
    assert result["counts"]["broken"] == 0
    assert result["counts"]["resolved_archived"] == 1
    assert result["archived_reference_pages"][0]["path"] == "guide.md"


def test_external_links_and_assets_are_not_page_links():
    active = [page(
        "guide.md",
        "[out](https://example.com/x) [img](diagram.svg) [mail](mailto:a@b.c)",
    )]
    result = analyse_links(active, [], {})
    assert result["counts"]["internal"] == 0
    assert result["counts"]["broken"] == 0


def test_image_embeds_are_not_treated_as_links():
    active = [page("guide.md", "![alt](/nowhere/missing.png)")]
    result = analyse_links(active, [], {})
    assert result["counts"]["total_links"] == 0


# --------------------------------------------------------------------------
# analyse_links — true positives
# --------------------------------------------------------------------------

def test_broken_link_is_reported_with_its_resolution():
    active = [page("a/guide.md", "See [gone](missing.md).")]
    result = analyse_links(active, [], {})
    assert result["counts"]["broken"] == 1
    entry = result["broken_pages"][0]
    assert entry["path"] == "a/guide.md"
    assert entry["links"] == [{"link": "missing.md", "resolved": "a/missing"}]
    assert result["dead_targets"] == [{"target": "a/missing", "references": 1}]


def test_doubled_path_after_a_move_is_caught():
    # The real class this was built for: an index moved into its own directory
    # keeps links written from the parent, doubling the path segment.
    active = [
        page("obs/meters/index.md", "- [alerts](meters/backup-alerts.md)"),
        page("obs/meters/backup-alerts.md"),
    ]
    result = analyse_links(active, [], {})
    assert result["counts"]["broken"] == 1
    assert result["broken_pages"][0]["links"][0]["resolved"] == "obs/meters/meters/backup-alerts"


def test_redirect_cycle_stays_broken():
    active = [page("guide.md", "See [loop](/a.md).")]
    result = analyse_links(active, [], {"a.md": "b.md", "b.md": "a.md"})
    assert result["counts"]["broken"] == 1


def test_retired_documentation_hosts_are_flagged():
    active = [page("guide.md", "See [old](https://docs.charliehub.net/control-plane/).")]
    result = analyse_links(active, [], {})
    assert result["counts"]["retired_host"] == 1
    assert result["retired_host_pages"][0]["path"] == "guide.md"
    # Counted as retired, not as a generic external link.
    assert result["counts"]["external"] == 0


def test_resolver_suspect_flags_a_total_failure():
    # If every internal link reports broken, the resolver is wrong — say so
    # rather than claim the corpus is entirely dead.
    active = [page("guide.md", "[a](x.md) [b](y.md)")]
    result = analyse_links(active, [], {})
    assert result["counts"]["broken"] == result["counts"]["internal"] == 2
    assert result["resolver_suspect"] is True


def test_resolver_not_suspect_when_some_links_resolve():
    active = [page("guide.md", "[a](x.md) [b](y.md)"), page("x.md")]
    result = analyse_links(active, [], {})
    assert result["resolver_suspect"] is False


# --------------------------------------------------------------------------
# build() integration
# --------------------------------------------------------------------------

def test_build_exposes_link_integrity_signal():
    pages = [page("guide.md", "See [gone](missing.md)."), page("other.md")]
    model = build(pages)
    signal = model["signals"]["link_integrity"]
    assert signal["counts"]["broken"] == 1
    assert signal["checked_pages"] == 2


def test_build_accepts_redirects_and_clears_the_finding():
    pages = [page("guide.md", "See [moved](old.md)."), page("new.md")]
    model = build(pages, redirects={"old.md": "new.md"})
    assert model["signals"]["link_integrity"]["counts"]["broken"] == 0


def test_build_without_redirects_is_backwards_compatible():
    # Existing callers pass no redirect map; the signal must still compute.
    model = build([page("guide.md", "text with no links")])
    assert model["signals"]["link_integrity"]["counts"]["broken"] == 0


def test_broken_links_raise_the_directory_review_score():
    pages = [page("area/guide.md", "[gone](missing.md)"), page("area/other.md")]
    model = build(pages)
    candidates = {c["path"]: c for c in model["review_candidates"]}
    assert "area" in candidates
    codes = {r["code"] for r in candidates["area"]["reasons"]}
    assert "BROKEN_INTERNAL_LINKS" in codes
    reason = next(r for r in candidates["area"]["reasons"] if r["code"] == "BROKEN_INTERNAL_LINKS")
    assert reason["measured"] == 1
    assert reason["affected_paths"] == ["area/guide.md"]


def test_clean_directory_gets_no_link_reason():
    pages = [page("area/guide.md", "[fine](other.md)"), page("area/other.md")]
    model = build(pages)
    for candidate in model["review_candidates"]:
        codes = {r["code"] for r in candidate["reasons"]}
        assert "BROKEN_INTERNAL_LINKS" not in codes


def test_reason_code_is_published_in_the_contract():
    pages = [page("area/guide.md", "[gone](missing.md)"), page("area/other.md")]
    model = build(pages)
    assert "BROKEN_INTERNAL_LINKS" in model["review_contract"]["reason_codes"]


def test_link_regex_does_not_backtrack_on_long_prose():
    # A previous external checker hung here: `(?:[^\]]|\n)*?` is an ambiguous
    # alternation because `[^\]]` already matches newline. Long link-free prose
    # must stay linear.
    active = [page("big.md", ("lorem ipsum dolor sit amet " * 4000) + "[x](y.md)")]
    result = analyse_links(active, [], {})
    assert result["counts"]["total_links"] == 1
