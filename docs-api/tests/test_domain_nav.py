"""S9-C: the sidebar groups top-level sections under the four domains.

Contract under test:
- build_nav wraps top-level sections in Know/Model/Observe/Work groups,
  fixed order, empty groups omitted, Home always first and ungrouped.
- A section's domain follows the page-path rule (observe/ and model/ heads;
  everything else is know) via its first leaf, matching page_domain().
- A section whose own label is the domain name splices its children into
  the group — never Observe nested under Observe.
- Governed section ordering still applies within the Know group.
- Grouping changes sidebar structure only: every page path in the input
  appears exactly once in the output.
"""
from app.generator import build_nav


def _pages():
    return [
        {"path": "index.md", "nav_path": "Home", "content": "# H"},
        {"path": "reference/example.md", "nav_path": "Reference/Example", "content": "# R"},
        {"path": "operations/backup.md", "nav_path": "Operations/Backup", "content": "# O"},
        {"path": "model/services/edge.md", "nav_path": "Model/Services/Edge", "content": "# M"},
        {"path": "observe/meter-list/index.md", "nav_path": "Observe/Meter list/Overview", "content": "# G"},
    ]


def _leaves(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for item in node:
            yield from _leaves(item)
    else:
        for value in node.values():
            yield from _leaves(value)


def test_top_level_groups_are_the_four_domains_in_order():
    nav = build_nav(_pages())
    labels = [next(iter(entry)) for entry in nav]
    assert labels == ["Home", "Know", "Model", "Observe"]  # no work pages → no Work group


def test_sections_land_in_their_domain_and_domain_named_sections_splice():
    nav = build_nav(_pages())
    know = next(entry["Know"] for entry in nav if "Know" in entry)
    know_labels = [next(iter(item)) for item in know]
    assert know_labels == ["Operations", "Reference"]
    observe = next(entry["Observe"] for entry in nav if "Observe" in entry)
    # The corpus section is itself named Observe: children splice, no double nest.
    assert [next(iter(item)) for item in observe] == ["Meter list"]
    model = next(entry["Model"] for entry in nav if "Model" in entry)
    assert [next(iter(item)) for item in model] == ["Services"]


def test_every_path_survives_grouping_exactly_once():
    nav = build_nav(_pages())
    leaves = sorted(_leaves(nav))
    assert leaves == sorted(page["path"] for page in _pages())


def test_section_order_applies_within_know():
    nav = build_nav(_pages(), section_order={"Reference": 0, "Operations": 1})
    know = next(entry["Know"] for entry in nav if "Know" in entry)
    assert [next(iter(item)) for item in know] == ["Reference", "Operations"]


def test_non_strict_keeps_tuple_shape_and_grouping():
    nav, conflicts = build_nav(_pages(), strict=False)
    assert conflicts == []
    assert [next(iter(entry)) for entry in nav] == ["Home", "Know", "Model", "Observe"]


def test_explicit_page_order_is_respected_and_overview_is_always_first():
    pages = [
        {"path": "reference/z.md", "nav_path": "Reference/Topic/Zebra", "nav_order": 0, "content": "# Z"},
        {"path": "reference/index.md", "nav_path": "Reference/Topic/Overview", "nav_order": 900, "content": "# O"},
        {"path": "reference/a.md", "nav_path": "Reference/Topic/Alpha", "nav_order": 10, "content": "# A"},
    ]
    nav = build_nav(pages)
    know = nav[0]["Know"]
    topic = know[0]["Reference"][0]["Topic"]
    assert [next(iter(item)) for item in topic] == ["Overview", "Zebra", "Alpha"]


def test_page_order_uses_normalized_nav_segments():
    pages = [
        {"path": "reference/z.md", "nav_path": "Reference / Topic / Zebra", "nav_order": 0, "content": "# Z"},
        {"path": "reference/a.md", "nav_path": "Reference/Topic/Alpha", "nav_order": 10, "content": "# A"},
    ]
    topic = build_nav(pages)[0]["Know"][0]["Reference"][0]["Topic"]
    assert [next(iter(item)) for item in topic] == ["Zebra", "Alpha"]
