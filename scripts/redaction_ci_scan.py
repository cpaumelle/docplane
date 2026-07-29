#!/usr/bin/env python3
"""CI assertion: scan transformed fixtures for redaction defects.

This is layer (3) of the Phase-4 regression suite. It runs the redaction
transform over every synthetic fixture and asserts, on the transformed output:

  * braces are balanced (regression for the `{<REDACTED:...>}}` bug);
  * there are no malformed `<REDACTED:...>` markers;
  * the marker count is stable across an idempotent re-run (no silent drift).

It also asserts that a known-malformed fixture is REJECTED by the transform.

Exit code 0 on success, non-zero on any violation. Intended to be wired into
CI path-scoped to the redaction/importer files.

All inputs are synthetic; nothing here embeds a real secret, host, path, or
identifier.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from migration.redaction import (  # noqa: E402
    MalformedMarkerError,
    braces_balanced,
    count_redaction_markers,
    find_malformed_markers,
    redact,
)

FIXTURES = REPO_ROOT / "migration" / "fixtures"
MALFORMED_FIXTURE = "malformed-marker.md"


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)


def main() -> int:
    violations = 0

    for path in sorted(FIXTURES.glob("*.md")):
        text = path.read_text(encoding="utf-8")

        if path.name == MALFORMED_FIXTURE:
            # This fixture must be rejected — the transform must never carry a
            # malformed marker forward or produce one.
            try:
                redact(text, label=path.stem)
            except MalformedMarkerError:
                print(f"ok (rejected as required): {path.name}")
            else:
                violations += 1
                _fail(f"{path.name}: malformed-marker fixture was NOT rejected")
            continue

        result = redact(text, label=path.stem)

        if not braces_balanced(result.sanitised):
            violations += 1
            _fail(f"{path.name}: transformed output has unbalanced braces")

        malformed = find_malformed_markers(result.sanitised)
        if malformed:
            violations += 1
            _fail(f"{path.name}: transformed output has malformed markers: {malformed}")

        # Marker-count drift: an idempotent re-run must not change the count.
        rerun = redact(result.sanitised, label=path.stem)
        if rerun.marker_count != result.marker_count:
            violations += 1
            _fail(
                f"{path.name}: marker-count drift "
                f"{result.marker_count} -> {rerun.marker_count}"
            )

        # Reported count must match the independent counter.
        if result.marker_count != count_redaction_markers(result.sanitised):
            violations += 1
            _fail(f"{path.name}: reported marker_count disagrees with counter")

        print(
            f"ok: {path.name} "
            f"(markers={result.marker_count}, balanced=True, well-formed=True)"
        )

    if violations:
        print(f"\n{violations} redaction CI violation(s)", file=sys.stderr)
        return 1
    print("\nredaction CI scan: all fixtures clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
