from dashboard import structure


def pages():
    return [
        {
            "path": "index.md",
            "title": "Home",
            "nav_path": "Home",
            "content": "# Home\n\n**Lifecycle:** REFERENCE",
            "revision": "r1",
            "version": 1,
            "status": "active",
        },
        {
            "path": "guides/index.md",
            "title": "Guides",
            "nav_path": "Guides/Overview",
            "content": "# Guides\n\n<!-- lifecycle: REFERENCE -->",
            "revision": "r2",
            "version": 1,
            "status": "active",
        },
        {
            "path": "guides/install.md",
            "title": "Install",
            "nav_path": "Guides/Install",
            "content": "# Install\n\n**Lifecycle:** OPERATION",
            "revision": "r3",
            "version": 2,
            "status": "active",
        },
        {
            "path": "guides/install-2026-07-26.md",
            "title": "Install",
            "nav_path": "Guides/Archive/Install",
            "content": "# Old install",
            "revision": "r4",
            "version": 1,
            "status": "active",
        },
        {
            "path": "old/retired.md",
            "title": "Retired",
            "nav_path": "Archive/Retired",
            "content": "# Retired",
            "revision": "r5",
            "version": 3,
            "status": "archived",
        },
    ]


def test_build_is_deterministic_and_structural():
    first = structure.build(pages())
    second = structure.build(list(reversed(pages())))
    assert first == second
    assert first["summary"] == {
        "active_pages": 4,
        "archived_pages": 1,
        "sections": 3,
        "directories": 2,
    }
    assert first["fingerprint"] == second["fingerprint"]
    assert "content" not in first["pages"][0]


def test_lifecycle_and_review_signals_are_explicit():
    model = structure.build(pages())
    guides = next(row for row in model["sections"] if row["name"] == "guides")
    assert guides["subtree_pages"] == 3
    assert guides["lifecycle"] == {
        "(missing)": 1,
        "OPERATION": 1,
        "REFERENCE": 1,
    }
    assert model["signals"]["duplicate_titles"] == [
        {
            "title": "Install",
            "paths": ["guides/install-2026-07-26.md", "guides/install.md"],
        }
    ]
    assert model["signals"]["missing_lifecycle"] == [
        "guides/install-2026-07-26.md"
    ]
    assert model["signals"]["dated_paths"] == [
        "guides/install-2026-07-26.md"
    ]


def test_unknown_lifecycle_is_not_coerced():
    sample = pages()
    sample[0]["content"] = "# Home\n\n**Lifecycle:** FUTURE"
    model = structure.build(sample)
    assert model["signals"]["unknown_lifecycle"] == ["index.md"]
