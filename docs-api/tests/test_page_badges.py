"""Category identity badges: the canonical page → domain derivation and the
meta strip injected into rendered pages by the generator.

Contract under test:
- page_domain() precedence: WORK_NOTE beats path; observe/ and model/ surface
  sections carry their domain; everything else is the know corpus.
- _augment_content() injects exactly one dp-badges meta-strip line under the
  H1 — domain badge (icon anatomy), facet chips, updated/version meta — with
  data-domain on the wrapper (drives the domain page tint and context bar),
  and is idempotent when re-augmenting its own output (deploys re-render
  every page on every release).
- Facet chips: knowledge class (except WORK_NOTE, which IS the domain badge)
  and GENERATED provenance.
- The legacy italic "Last updated" marker is stripped, never re-emitted.
"""
import re

from app.generator import GENERATOR_STAMP, _augment_content, page_domain


def _page(**overrides):
    base = {
        "path": "guides/example-town.md",
        "content": "# Example Town\n\nbody\n",
        "knowledge_class": None,
        "provenance": "AUTHORED",
        "updated_at": None,
        "version": None,
    }
    base.update(overrides)
    return base


def test_domain_precedence():
    assert page_domain(_page(path="guides/example-town.md")) == "know"
    assert page_domain(_page(path="observe/meter-list/example-prometheus/index.md")) == "observe"
    assert page_domain(_page(path="model/services/edge-1.md")) == "model"
    assert page_domain(_page(path="work/roadmap.md")) == "work"
    assert page_domain(_page(path="index.md")) == "know"
    assert page_domain(_page(knowledge_class="WORK_NOTE")) == "work"
    # WORK_NOTE wins wherever the page lives, and case/whitespace don't matter.
    assert page_domain(_page(path="observe/x.md", knowledge_class=" work_note ")) == "work"


def test_meta_strip_injected_under_title():
    out = _augment_content(_page())
    lines = out.splitlines()
    strip_lines = [l for l in lines if l.startswith('<p class="dp-badges"')]
    assert len(strip_lines) == 1
    strip = strip_lines[0]
    assert strip.startswith('<p class="dp-badges" data-domain="know">')
    assert '<span class="dp-badge dp-badge--ic" data-domain="know">' in strip
    assert "<svg " in strip and ">Know</span>" in strip
    assert lines.index(strip) > lines.index("# Example Town")


def test_updated_meta_lives_in_the_strip_not_an_italic_line():
    out = _augment_content(_page(updated_at="2026-07-30T14:51:00", version=10))
    strip = next(l for l in out.splitlines() if l.startswith('<p class="dp-badges"'))
    assert '<span class="dp-meta">Updated 2026-07-30 14:51 UTC · v10</span>' in strip
    assert 'data-page-version="10"' in strip
    assert not re.search(r"^\*Last updated:", out, flags=re.M)


def test_legacy_italic_marker_is_stripped():
    content = "# Example Town\n\n*Last updated: 2026-01-01 00:00 UTC · v3*\n\nbody\n"
    out = _augment_content(_page(content=content))
    assert "Last updated:" not in out


def test_strip_without_h1_lands_on_top():
    out = _augment_content(_page(content="plain body\n"))
    first = out.splitlines()[1]  # line 0 is the generator stamp
    assert first.startswith('<p class="dp-badges"')


def test_facet_chips():
    out = _augment_content(_page(knowledge_class="OPERATION"))
    assert '<span class="dp-chip">Operation</span>' in out
    out = _augment_content(_page(path="observe/meter-list/x.md", provenance="GENERATED"))
    assert 'data-domain="observe">' in out and ">Observe</span>" in out
    assert '<span class="dp-chip dp-chip--generated">Generated</span>' in out
    # WORK_NOTE is the domain badge, never doubled as a class chip.
    out = _augment_content(_page(knowledge_class="WORK_NOTE"))
    assert 'data-domain="work">' in out and ">Work</span>" in out
    assert "dp-chip" not in out


def test_reaugmenting_is_idempotent():
    page = _page(updated_at="2026-07-30T14:51:00", version=10, knowledge_class="REFERENCE")
    once = _augment_content(page)
    again = _augment_content({**page, "content": once})
    assert again == once
    assert once.count('<p class="dp-badges"') == 1
    assert once.count(GENERATOR_STAMP) == 1
