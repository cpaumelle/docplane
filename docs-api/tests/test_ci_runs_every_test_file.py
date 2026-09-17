"""Required CI must execute every pytest file in the repository.

`fresh-instance.yml` invoked pytest against 40 explicitly named test file
paths. Any file added afterwards ran only on its author's machine, and nothing
anywhere said so. Ten files were being skipped, including regression tests
written to guard specific defects -- they could never have failed a build. All
ten passed once run, so the cost was not broken tests but false comfort.

That is the registration-layer shape: something is declared, the declaration is
checked, and the thing itself is never executed.

DISCOVERY IS REPO-WIDE, DELIBERATELY.

A first version of this guard walked a hard-coded tuple of three test
directories -- which reproduced the very defect it was written to prevent. A
new pytest root (`foo/tests/test_x.py`) would have been invisible to both the
guard and the workflow, and the build would have stayed green. A registry that
must be hand-updated cannot police a list that must be hand-updated.

So this walks the repository for `test_*.py` and requires each file to be
reachable from the pytest command in the workflow. Adding a test root now
fails the build until the workflow names it. Exclusions must be explicit and
justified below, never implicit in a lookup table.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "fresh-instance.yml"

# Directories never searched for tests: not source, or not ours.
PRUNED_DIRS = {
    ".git", ".github", "node_modules", "__pycache__", ".venv", "venv",
    ".pytest_cache", "site", "dist", "build", ".mypy_cache", ".ruff_cache",
}

# Test files deliberately NOT run by the required job. Each needs a reason.
# Empty is the healthy state: prefer fixing or deleting a test over exempting
# it, because an entry here is a test nobody is watching.
INTENTIONALLY_EXCLUDED: dict[str, str] = {}


def _pytest_invocation() -> str:
    """The `python -m pytest ...` command from the Compile and test step,
    with shell line continuations folded into one line.

    Walked line by line rather than matched with one regex: a pattern like
    `(?:[^\\n]|\\\\\\n)*` stops at the first newline, because the greedy
    character class consumes the trailing backslash before the continuation
    branch can match it. That silently returns only the first line, which makes
    every file look unreachable -- a false positive here, but the same class of
    quiet truncation this file exists to catch.
    """
    lines = WORKFLOW.read_text().splitlines()
    for index, line in enumerate(lines):
        if "python -m pytest" not in line:
            continue
        collected = [line]
        cursor = index
        while collected[-1].rstrip().endswith("\\") and cursor + 1 < len(lines):
            cursor += 1
            collected.append(lines[cursor])
        return " ".join(part.rstrip().rstrip("\\").strip() for part in collected)
    raise AssertionError("fresh-instance.yml no longer invokes pytest at all")


def _discover_test_files() -> list[str]:
    """Every test_*.py in the repository, as repo-relative posix paths."""
    found: list[str] = []
    for path in ROOT.rglob("test_*.py"):
        if any(part in PRUNED_DIRS for part in path.relative_to(ROOT).parts):
            continue
        found.append(path.relative_to(ROOT).as_posix())
    return sorted(found)


def _is_reachable(rel: str, args: str) -> bool:
    """CI runs this file if it names the file, or any ancestor directory."""
    if rel in args:
        return True
    parts = rel.split("/")
    for depth in range(1, len(parts)):
        directory = "/".join(parts[:depth])
        if re.search(rf"(?<![\w/]){re.escape(directory)}(?=[\s\\]|$)", args):
            return True
    return False


def test_no_test_file_is_unreachable_from_required_ci():
    """The whole point. Repo-wide, so a new test root cannot hide."""
    args = _pytest_invocation()
    unreachable = [
        rel for rel in _discover_test_files()
        if rel not in INTENTIONALLY_EXCLUDED and not _is_reachable(rel, args)
    ]
    assert not unreachable, (
        "these test files exist but required CI never runs them:\n  "
        + "\n  ".join(unreachable)
        + "\n\nAdd their directory to the pytest command in "
          ".github/workflows/fresh-instance.yml, or record an explicit reason "
          "in INTENTIONALLY_EXCLUDED."
    )


def test_exclusions_are_justified_and_still_exist():
    """An exemption for a file that no longer exists is stale permission."""
    discovered = set(_discover_test_files())
    for rel, reason in INTENTIONALLY_EXCLUDED.items():
        assert rel in discovered, f"{rel} is excluded but no longer exists"
        assert reason.strip(), f"{rel} is excluded with no reason given"


def test_this_guard_can_see_what_it_checks():
    """Assert the inputs before trusting the result.

    If the workflow moved or the glob broke, every assertion above would pass
    vacuously on empty inputs -- which is the exact failure mode this file
    exists to stop.
    """
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing -- the assertions above prove nothing"
    assert "python -m pytest" in WORKFLOW.read_text()

    discovered = _discover_test_files()
    assert len(discovered) > 40, (
        f"only {len(discovered)} test files discovered; the walk is broken"
    )
    assert "docs-api/tests/test_ci_runs_every_test_file.py" in discovered, (
        "this guard cannot find itself, so discovery is not working"
    )
    # Every known root must be represented, or the walk is silently truncated.
    roots = {rel.split("/tests/")[0] for rel in discovered if "/tests/" in rel}
    for expected in ("docs-api", "mcp", "dashboard", "migration", "scripts"):
        assert expected in roots, f"walk found no tests under {expected}/"
