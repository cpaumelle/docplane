#!/usr/bin/env python3
"""Does the link graph see root-absolute links?

Every repair this session rewrote targets to root-absolute form (/a/b/). If
find_links()/resolve() only understands relative .md targets, those links vanish
from the graph -- which would mean the repairs reduced measured inbound counts,
and every hub determination built on inbound is unreliable. READ-ONLY.
"""
import json, os, re, subprocess, sys, collections

sys.path.insert(0, os.path.expanduser("~/docplane-dev-redirects"))
from migration.links import find_links, resolve  # noqa: E402

B = "https://docplane.charliehub.internal"
T = open(os.path.expanduser("~/i44/move-token")).read().strip()


def get(p):
    r = subprocess.run(["curl", "-s", "--max-time", "180",
                        "-H", "Authorization: Bearer " + T,
                        "-w", "\n%{http_code}", B + p],
                       capture_output=True, text=True)
    b, _, c = r.stdout.rpartition("\n")
    return int(c), (json.loads(b) if b.strip() else {})


print("=== unit behaviour of the resolver on a root-absolute target ===")
for raw in ["/control-plane/specs/transit-convergence/", "../foo.md", "foo.md"]:
    body = "see [x](%s)\n" % raw
    refs = find_links("control-plane/a.md", body)
    for r in refs:
        print("   raw=%-46r -> resolved=%r" % (raw, r.resolved))
    if not refs:
        print("   raw=%-46r -> NOT EXTRACTED AS A LINK AT ALL" % raw)

c, pg = get("/api/v1/pages?status=all&limit=2000")
active = {p["path"]: p for p in pg["pages"] if p["status"] == "active"}

MD = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
kinds = collections.Counter()
abs_targets = collections.Counter()
for path, p in active.items():
    c, f = get("/api/v1/pages/%s" % p["resource_id"])
    md = f.get("content_markdown") or f.get("content") or ""
    seen_by_graph = {r.target for r in find_links(path, md)}
    for m in MD.finditer(md):
        t = m.group(1)
        if t.startswith(("http://", "https://", "#", "mailto:")):
            kinds["external/anchor"] += 1
        elif t.startswith("/"):
            kinds["root-absolute"] += 1
            abs_targets[t.split("#")[0]] += 1
            kinds["root-absolute SEEN by graph" if t in seen_by_graph
                  else "root-absolute INVISIBLE to graph"] += 1
        elif t.endswith(".md") or ".md#" in t:
            kinds["relative .md"] += 1
        else:
            kinds["other"] += 1

print("\n=== corpus-wide markdown link inventory ===")
for k, v in kinds.most_common():
    print("   %-38s %d" % (k, v))

print("\n=== most-referenced root-absolute targets ===")
for t, n in abs_targets.most_common(12):
    print("   %-58s %d" % (t, n))
