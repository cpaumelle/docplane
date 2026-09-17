"""CI must actually run every test file in the repository.

`fresh-instance.yml` invoked pytest against a hand-maintained list of 40 test
file paths. Any file added afterwards ran only on the author's machine, and
nothing anywhere said so. At the time this guard was written that had silently
excluded ten files:

    dashboard/tests/test_domain_views.py
    docs-api/tests/test_catalogues_page_links.py
    docs-api/tests/test_catalogues_page_links_e2e.py
    docs-api/tests/test_corpus_link_integrity.py
    docs-api/tests/test_event_channel_vocabulary.py
    docs-api/tests/test_generated_pages_lifecycle.py
    docs-api/tests/test_principal_token_management.py
    docs-api/tests/test_schema_catalogue_links.py
    docs-api/tests/test_text_patch.py
    docs-api/tests/test_work_read_surfaces.py

All ten passed once run. The cost was not broken tests; it was false comfort --
regression tests written to guard specific defects (work read surfaces, the
generated-page lifecycle marker) that would never have failed a build. A green
tick meant less than it appeared to.

This is the same failure shape as the registration-layer bug: something is
declared, the declaration is checked, and the thing itself is never executed.

The step now names directories, so discovery is automatic. This guard exists so
that reverting to an enumeration -- or adding a fourth test directory nobody
wires up -- fails loudly instead of quietly shrinking the suite. It deliberately
accepts either style: what is enforced is coverage, not syntax.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "fresh-instance.yml"

# Every directory in the repo that holds pytest files. A new one must be added
# here *and* wired into the workflow; the second assertion below enforces that.
TEST_DIRS = ("docs-api/tests", "mcp/tests", "dashboard/tests")


def _pytest_invocation() -> str:
    """The `python -m pytest ...` command from the Compile and test step,
    including any line continuations."""
    text = WORKFLOW.read_text()
    match = re.search(r"python -m pytest\b(?P<args>(?:[^\n]|\\\n)*)", text)
    assert match, "fresh-instance.yml no longer invokes pytest at all"
    return match.group("args").replace("\\\n", " ")


def _discovered_test_files() -> list[Path]:
    found: list[Path] = []
    for directory in TEST_DIRS:
        found.extend(sorted((ROOT / directory).glob("test_*.py")))
    return found


def test_every_test_directory_is_named_in_ci():
    """Directory-level coverage: the cheap, drift-proof form."""
    args = _pytest_invocation()
    missing = [d for d in TEST_DIRS if d not in args]
    assert not missing, (
        "fresh-instance.yml does not run these test directories: "
        + ", ".join(missing)
    )


def test_no_test_file_is_unreachable_from_ci():
    """Coverage, whatever the style.

    A file counts as reachable if CI names its directory or names the file
    itself. This keeps passing if someone deliberately returns to an explicit
    list -- provided the list is complete.
    """
    args = _pytest_invocation()
    unreachable = []
    for path in _discovered_test_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in args:
            continue
        if any(rel.startswith(d + "/") and d in args for d in TEST_DIRS):
            continue
        unreachable.append(rel)
    assert not unreachable, (
        "these test files exist but CI never runs them:\n  "
        + "\n  ".join(unreachable)
    )


def test_this_guard_can_see_the_workflow():
    """The assertions above pass vacuously if the workflow moves or is renamed:
    a missing file would make the regexes match nothing and the loops empty.
    Assert the inputs before trusting the result."""
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing -- the guard above proves nothing"
    assert "python -m pytest" in WORKFLOW.read_text()
    discovered = _discovered_test_files()
    assert len(discovered) > 20, (
        f"only {len(discovered)} test files discovered; the glob or TEST_DIRS is wrong"
    )
    assert any(p.name == "test_ci_runs_every_test_file.py" for p in discovered), (
        "this guard cannot find itself, so discovery is not working"
    )
