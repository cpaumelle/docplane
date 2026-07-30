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


def audit(active, bodies, include_protected=True):
    """Every broken internal link. Broken == resolved target is not an active page.

    include_protected=True is the CURRENT-VALIDITY reading and the default: a
    link inside a blockquote is still a link a reader can follow, and it still
    404s. The previous gate defaulted to skipping them, which hid live breaks.
    Pass include_protected=False only to ask the narrower authoring question of
    whether a historical quotation was rewritten.
    """
    out = []
    for src in sorted(bodies):
        md = bodies[src]
        prot = protected_lines(md)
        spans = inline_code_spans(md)
        for r in find_links(src, md):
            if not r.resolved or r.resolved in active:
                continue
            in_prot = any(l in prot for l in range(r.line - 1, r.end_line))
            in_code = any(s <= r.start and r.end <= e for s, e in spans)
            # code_is_never_a_link: inline/fenced code does not render as a
            # hyperlink, so it cannot 404 for a reader and must never be
            # retargeted -- doing so rewrites the meaning of the prose.
            if in_code:
                continue
            # A blockquote DOES render as a link. Skip it only when explicitly
            # asked the narrower authoring question.
            if not include_protected and in_prot:
                continue
            out.append({"source_path": src, "line": r.line,
                        "raw_target": r.target, "resolved_target": r.resolved,
                        "protected": in_prot, "inline_code": in_code})
    return out


def key(f):
    return "%s::%s" % (f["source_path"], f["resolved_target"])


def check(findings, baseline):
    owned = {e["key"] for e in baseline["exceptions"]}
    new = [f for f in findings if key(f) not in owned]
    stale = owned - {key(f) for f in findings}
    return new, sorted(stale)


def classify(findings, baseline):
    """Split findings into the categories the release report must keep separate."""
    by_key = {e["key"]: e for e in baseline["exceptions"]}
    out = {"owned_baseline": [], "current_broken_unowned": []}
    for f in findings:
        e = by_key.get(key(f))
        if e:
            out["owned_baseline"].append((f, e))
        else:
            out["current_broken_unowned"].append(f)
    out["stale_exceptions"] = sorted(set(by_key) - {key(f) for f in findings})
    return out


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

    # Concept A: current-corpus validity, protected contexts INCLUDED
    findings = audit(active, bodies, include_protected=True)
    baseline = json.load(open(BASELINE))
    cls = classify(findings, baseline)
    new = cls["current_broken_unowned"]
    stale = cls["stale_exceptions"]

    print("active pages                      : %d" % len(active))
    print("A. current broken targets (all)   : %d" % len(findings))
    print("   accepted owned baseline        : %d" % len(cls["owned_baseline"]))
    print("   CURRENT BROKEN, UNOWNED        : %d" % len(new))
    print("   stale exceptions (now repaired): %d" % len(stale))
    hidden = [f for f in findings if f["protected"] or f["inline_code"]]
    print("   of all breaks, in blockquotes  : %d  (counted: they render as links)"
          % len(hidden))
    print("   inline/fenced code             : excluded by construction "
          "(never renders as a link)")
    for s_ in stale:
        print("      stale: %s" % s_)
    for f in new:
        print("   FAIL %s L%d %r -> %s (route %s)"
              % (f["source_path"], f["line"], f["raw_target"],
                 f["resolved_target"], route_status(f["resolved_target"])))
    if new:
        print("\nGATE FAILED: %d currently broken unowned internal target(s). "
              "Not releasable." % len(new))
        return 1
    print("\nGATE PASSED: zero currently broken unowned internal targets; every remaining break is an owned exception.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
