#!/usr/bin/env python3
"""Mandatory existence-aware release gate for Issue #44 publication sessions.

WHY THIS EXISTS
---------------
The per-cohort scan checks only stale references to the pages that cohort moved.
It reported CORPUS SCAN CLEAN through a session that took the corpus from 9 to 34
broken targets. Path resolution returning a normalised string is NOT proof the
target exists; conflating the two is what let 23 regressions through.

WHAT IT DOES
------------
1. Extracts every internal link from every ACTIVE page.
2. Resolves each to a corpus path.
3. Verifies the resolved target EXISTS as an active page.
4. Classifies each surviving break against an explicit, owned baseline.
5. Fails hard on any newly introduced, unowned broken target.

Exit 0 = releasable. Exit 1 = a new unowned broken target exists.
Run before every lane/session release.
"""
import json, os, re, subprocess, sys

sys.path.insert(0, os.path.expanduser("~/docplane-dev-redirects"))
from migration.links import (find_links, protected_lines,  # noqa: E402
                             inline_code_spans, page_url)

B = "https://docplane.charliehub.internal"
HOME = os.path.expanduser("~")
BASELINE = os.path.join(HOME, "i44/link-baseline.json")


def get(token, p):
    r = subprocess.run(["curl", "-s", "--max-time", "180",
                        "-H", "Authorization: Bearer " + token,
                        "-w", "\n%{http_code}", B + p],
                       capture_output=True, text=True)
    b, _, c = r.stdout.rpartition("\n")
    return int(c), (json.loads(b) if b.strip() else {})


def route_status(path):
    return subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", B + page_url(path)],
        capture_output=True, text=True).stdout.strip()


def audit(active, bodies):
    """Every broken internal link. Broken == resolved target is not an active page."""
    out = []
    for src in sorted(bodies):
        md = bodies[src]
        prot = protected_lines(md)
        spans = inline_code_spans(md)
        for r in find_links(src, md):
            if not r.resolved or r.resolved in active:
                continue
            if any(l in prot for l in range(r.line - 1, r.end_line)):
                continue
            if any(s <= r.start and r.end <= e for s, e in spans):
                continue
            out.append({"source_path": src, "line": r.line,
                        "raw_target": r.target, "resolved_target": r.resolved})
    return out


def key(f):
    return "%s::%s" % (f["source_path"], f["resolved_target"])


def check(findings, baseline):
    owned = {e["key"] for e in baseline["exceptions"]}
    new = [f for f in findings if key(f) not in owned]
    stale = owned - {key(f) for f in findings}
    return new, sorted(stale)


def main():
    token = open(os.path.join(HOME, "i44/move-token")).read().strip()
    c, pg = get(token, "/api/v1/pages?status=all&limit=2000")
    if pg["count"] != pg["total"]:
        sys.exit("GATE ABORT: page listing truncated (count != total)")
    active = {p["path"]: p for p in pg["pages"] if p["status"] == "active"}
    bodies = {}
    for path, p in active.items():
        c, f = get(token, "/api/v1/pages/%s" % p["resource_id"])
        bodies[path] = f.get("content_markdown") or f.get("content") or ""

    findings = audit(active, bodies)
    baseline = json.load(open(BASELINE))
    new, stale = check(findings, baseline)

    print("active pages        : %d" % len(active))
    print("broken targets      : %d" % len(findings))
    print("owned exceptions    : %d" % len(baseline["exceptions"]))
    print("NEW unowned breaks  : %d" % len(new))
    if stale:
        print("baseline entries no longer breaking (safe to retire): %d" % len(stale))
        for s in stale:
            print("   %s" % s)
    for f in new:
        print("  FAIL %s L%d %r -> %s (route %s)"
              % (f["source_path"], f["line"], f["raw_target"],
                 f["resolved_target"], route_status(f["resolved_target"])))
    if new:
        print("\nGATE FAILED: %d newly introduced broken target(s). Not releasable." % len(new))
        return 1
    print("\nGATE PASSED: every broken target is an owned baseline exception.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
