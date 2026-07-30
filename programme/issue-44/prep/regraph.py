#!/usr/bin/env python3
"""Rebuild the complete link graph with the corrected resolver and classify
every noncanonical or absent target. READ-ONLY."""
import json, os, subprocess, sys, collections

sys.path.insert(0, os.path.expanduser("~/docplane-dev-redirects"))
from migration.links import (find_links, absolute_candidates, protected_lines,  # noqa: E402
                             inline_code_spans, is_evidence_surface, page_url)

B = "https://docplane.charliehub.internal"
HOME = os.path.expanduser("~")
T = open(os.path.join(HOME, "i44/move-token")).read().strip()


def get(p):
    r = subprocess.run(["curl", "-s", "--max-time", "180",
                        "-H", "Authorization: Bearer " + T,
                        "-w", "\n%{http_code}", B + p],
                       capture_output=True, text=True)
    b, _, c = r.stdout.rpartition("\n")
    return int(c), (json.loads(b) if b.strip() else {})


def route(u):
    return subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", B + u],
                          capture_output=True, text=True).stdout.strip()


c, pg = get("/api/v1/pages?status=all&limit=2000")
if pg["count"] != pg["total"]:
    sys.exit("truncated")
active = {p["path"]: p for p in pg["pages"] if p["status"] == "active"}

c, ch = get("/api/v1/changes?limit=500")
moved = {}
for x in ch["changes"]:
    if x["status"] != "PUBLISHED":
        continue
    for o in x.get("operations") or []:
        if o["operation_type"] != "MOVE_PAGE":
            continue
        c2, h = get("/api/v1/pages/%s/history" % o["page_resource_id"])
        pr = [v for v in h.get("versions", []) if v.get("change_id") == x["change_id"]]
        if pr:
            moved[pr[0]["path"]] = {"to": o["payload"]["to_path"], "change": x["change_id"]}

inbound = collections.Counter()
rows = []
for path, p in sorted(active.items()):
    c, f = get("/api/v1/pages/%s" % p["resource_id"])
    md = f.get("content_markdown") or f.get("content") or ""
    prot = protected_lines(md)
    spans = inline_code_spans(md)
    for r in find_links(path, md):
        if not r.resolved:
            continue
        is_abs = bool(absolute_candidates(r.target))
        cands = absolute_candidates(r.target) if is_abs else [r.resolved]
        hit = next((x for x in cands if x in active), None)
        in_code = any(s <= r.start and r.end <= e for s, e in spans)
        in_prot = any(l in prot for l in range(r.line - 1, r.end_line))
        if hit and not in_code:
            inbound[hit] += 1
        if hit:
            continue
        old = next((x for x in cands if x in moved), None)
        rows.append({
            "source_resource_id": p["resource_id"], "source_path": path,
            "line": r.line, "raw_target": r.target, "resolved_target": r.resolved,
            "candidates": cands,
            "anchor": ("#" + r.target.split("#", 1)[1]) if "#" in r.target else "",
            "root_absolute": is_abs,
            "current_canonical": moved[old]["to"] if old else None,
            "originating_publication": moved[old]["change"] if old else None,
            "redirect_status": route(page_url(cands[0])),
            "protected": in_prot, "inline_code": in_code,
            "evidence_surface": is_evidence_surface(path),
            "disposition": ("rewrite-to-canonical" if old and not (in_code or is_evidence_surface(path))
                            else "owned-preservation" if old
                            else "absent-needs-classification"),
        })

json.dump({"inbound": dict(inbound), "findings": rows},
          open(os.path.join(HOME, "i44/cohorts/corrected-graph.json"), "w"), indent=1)

stale = [r for r in rows if r["current_canonical"]]
absent = [r for r in rows if not r["current_canonical"]]
print("CORRECTED GRAPH (root-absolute now resolved)")
print("  total noncanonical/absent targets : %d" % len(rows))
print("    stale old Issue #44 paths       : %d" % len(stale))
print("      of which root-absolute        : %d" % len([r for r in stale if r["root_absolute"]]))
print("      to rewrite                    : %d" % len([r for r in stale if r["disposition"] == "rewrite-to-canonical"]))
print("      owned preservation            : %d" % len([r for r in stale if r["disposition"] == "owned-preservation"]))
print("    genuinely absent                : %d" % len(absent))
print("      of which root-absolute        : %d" % len([r for r in absent if r["root_absolute"]]))
print("\nSTALE by target:")
for t, n in collections.Counter(r["candidates"][0] for r in stale).most_common(14):
    ex = next(r for r in stale if r["candidates"][0] == t)
    print("   x%-3d %-54s -> %s" % (n, t, ex["current_canonical"]))
print("\nCORRECTED INBOUND for previously-misreported pages:")
for q in ["control-plane/hub2-authority-crosswalk.md", "control-plane/ccm.md",
          "control-plane/architecture-overview.md", "control-plane/cp-net-1-driver.md",
          "control-plane/sd-wan-invariants.md"]:
    print("   %-48s inbound = %d" % (q.split("/", 1)[1], inbound[q]))
