#!/usr/bin/env python3
"""Existence-check the 272 root-absolute links the graph never resolved.

Maps /a/b/ -> a/b.md or a/b/index.md and classifies each: live page, old path of
an Issue #44 move (redirect-dependent), or genuinely absent. READ-ONLY.
"""
import json, os, re, subprocess, sys, collections

B = "https://docplane.charliehub.internal"
HOME = os.path.expanduser("~")
T = open(os.path.join(HOME, "i44/move-token")).read().strip()
MD = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def get(p):
    r = subprocess.run(["curl", "-s", "--max-time", "180",
                        "-H", "Authorization: Bearer " + T,
                        "-w", "\n%{http_code}", B + p],
                       capture_output=True, text=True)
    b, _, c = r.stdout.rpartition("\n")
    return int(c), (json.loads(b) if b.strip() else {})


c, pg = get("/api/v1/pages?status=all&limit=2000")
active = {p["path"]: p for p in pg["pages"] if p["status"] == "active"}

# old-path -> new-path for every Issue #44 move, from receipts
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
            moved[pr[0]["path"]] = o["payload"]["to_path"]


def candidates(url):
    s = url.split("#")[0].strip("/")
    return [s + ".md", s + "/index.md"]


live, redirect_dep, absent = [], [], []
for path, p in sorted(active.items()):
    c, f = get("/api/v1/pages/%s" % p["resource_id"])
    md = f.get("content_markdown") or f.get("content") or ""
    for m in MD.finditer(md):
        t = m.group(1)
        if not t.startswith("/"):
            continue
        cands = candidates(t)
        hit = next((x for x in cands if x in active), None)
        rec = {"source": path, "target_url": t, "candidates": cands}
        if hit:
            live.append(rec)
        else:
            old = next((x for x in cands if x in moved), None)
            if old:
                rec["moved_to"] = moved[old]
                redirect_dep.append(rec)
            else:
                absent.append(rec)

print("root-absolute links audited : %d" % (len(live) + len(redirect_dep) + len(absent)))
print("  resolve to a live page    : %d" % len(live))
print("  OLD PATH of an I#44 move  : %d   (works only via compatibility redirect)"
      % len(redirect_dep))
print("  genuinely absent          : %d" % len(absent))

print("\n=== redirect-dependent (stale old paths in places we control) ===")
for t, n in collections.Counter(r["target_url"] for r in redirect_dep).most_common():
    ex = next(r for r in redirect_dep if r["target_url"] == t)
    print("   x%-3d %-56s -> now %s" % (n, t, ex["moved_to"]))

print("\n=== genuinely absent ===")
for t, n in collections.Counter(r["target_url"] for r in absent).most_common(15):
    srcs = sorted({r["source"] for r in absent if r["target_url"] == t})
    print("   x%-3d %-56s from %s" % (n, t, srcs[0] + (" +%d" % (len(srcs) - 1) if len(srcs) > 1 else "")))

json.dump({"live": len(live), "redirect_dependent": redirect_dep, "absent": absent},
          open(os.path.join(HOME, "i44/cohorts/absolute-link-audit.json"), "w"), indent=1)

print("\n=== inbound undercount for pages I reported ===")
for page in ["control-plane/hub2-authority-crosswalk.md", "control-plane/ccm.md",
             "control-plane/architecture-overview.md", "control-plane/cp-net-1-driver.md"]:
    url = "/" + page[:-3] + "/"
    n = sum(1 for r in live + redirect_dep + absent if r["target_url"].split("#")[0] == url)
    print("   %-50s root-absolute inbound = %d (my counter reported them as 0-15 relative only)"
          % (page.split("/", 1)[1], n))
