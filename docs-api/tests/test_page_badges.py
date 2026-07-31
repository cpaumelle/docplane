"""Category identity badges: the canonical page → domain derivation and the
badge row injected into rendered pages by the generator.

Contract under test:
- page_domain() precedence: WORK_NOTE beats path; observe/ and model/ surface
  sections carry their domain; everything else is the know corpus.
- _augment_content() injects exactly one dp-badges line, under the H1 and
  before the Last-updated marker, and is idempotent when re-augmenting its
  own output (deploys re-render every page on every release).
- Facet chips: knowledge class (except WORK_NOTE, which IS the domain badge)
  and GENERATED provenance.
"""
import re

from app.generator import GENERATOR_STAMP, _augment_content, page_domain


def _page(**overrides):
    base = {
        "path": "guides/dinard.md",
        "content": "# Dinard\n\nbody\n",
        "knowledge_class": None,
        "provenance": "AUTHORED",
        "updated_at": None,
        "version": None,
    }
    base.update(overrides)
    return base


def test_domain_precedence():
    assert page_domain(_page(path="guides/dinard.md")) == "know"
    assert page_domain(_page(path="observe/meter-list/hub2-prometheus/index.md")) == "observe"
    assert page_domain(_page(path="model/services/fr-edge-1.md")) == "model"
    assert page_domain(_page(path="index.md")) == "know"
    assert page_domain(_page(knowledge_class="WORK_NOTE")) == "work"
    # WORK_NOTE wins wherever the page lives, and case/whitespace don't matter.
    assert page_domain(_page(path="observe/x.md", knowledge_class=" work_note ")) == "work"


def test_badge_injected_under_title():
    out = _augment_content(_page())
    lines = out.splitlines()
    badge_lines = [l for l in lines if l.startswith('<p class="dp-badges">')]
    assert len(badge_lines) == 1
    assert '<span class="dp-badge" data-domain="know">Know</span>' in badge_lines[0]
    assert lines.index(badge_lines[0]) > lines.index("# Dinard")


def test_badge_precedes_last_updated_marker():
    out = _augment_content(_page(updated_at="2026-07-30T14:51:00", version=10))
    badge_at = out.index('<p class="dp-badges">')
    marker_at = out.index("*Last updated: 2026-07-30 14:51 UTC · v10*")
    assert badge_at < marker_at


def test_badge_without_h1_lands_on_top():
    out = _augment_content(_page(content="plain body\n"))
    first = out.splitlines()[1]  # line 0 is the generator stamp
    assert first.startswith('<p class="dp-badges">')


def test_facet_chips():
    out = _augment_content(_page(knowledge_class="OPERATION"))
    assert '<span class="dp-chip">Operation</span>' in out
    out = _augment_content(_page(path="observe/meter-list/x.md", provenance="GENERATED"))
    assert 'data-domain="observe">Observe</span>' in out
    assert '<span class="dp-chip dp-chip--generated">Generated</span>' in out
    # WORK_NOTE is the domain badge, never doubled as a class chip.
    out = _augment_content(_page(knowledge_class="WORK_NOTE"))
    assert 'data-domain="work">Work</span>' in out
    assert "dp-chip" not in out


def test_reaugmenting_is_idempotent():
    page = _page(updated_at="2026-07-30T14:51:00", version=10, knowledge_class="REFERENCE")
    once = _augment_content(page)
    again = _augment_content({**page, "content": once})
    assert again == once
    assert once.count('<p class="dp-badges">') == 1
    assert once.count(GENERATOR_STAMP) == 1
    assert len(re.findall(r"\*Last updated:", once)) == 1
