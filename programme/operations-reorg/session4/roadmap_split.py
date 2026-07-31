import json, hashlib, re

d = json.load(open("/tmp/docplane-lifecycle-policy-audit/page-roadmap-fresh.json"))
content = d["content"]
lines = content.splitlines(keepends=True)

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

# Locate exact section boundaries by heading text (## level headings), robust to
# line-number drift -- search on the actual fetched content, not a manual line count.
headings = [(m.start(), m.group(1)) for m in re.finditer(r"^## (.+)$", content, re.MULTILINE)]
headings.append((len(content), "__EOF__"))

def section_span(name):
    for i, (start, h) in enumerate(headings[:-1]):
        if h.strip() == name:
            return start, headings[i + 1][0]
    raise KeyError(name)

DURABLE_SECTIONS = ["When to add a new invariant", "Explicit non-goals", "Design principles", "Maturity model"]
spans = {name: section_span(name) for name in DURABLE_SECTIONS}

durable_blocks = {}
for name, (s, e) in spans.items():
    block = content[s:e].rstrip("\n") + "\n"
    durable_blocks[name] = block
    print(f"--- {name} --- span=({s},{e}) len={len(block)} sha256_16={sha(block)}")

# Whole-document accounting: every durable span removed from the original must be
# replaced by nothing else claiming new meaning -- verify no overlap between spans.
ordered = sorted(spans.values())
for i in range(len(ordered) - 1):
    assert ordered[i][1] <= ordered[i + 1][0], f"overlap {ordered[i]} vs {ordered[i+1]}"
print("no span overlaps: OK")

# Build the new canonical durable page.
new_page_body = (
    "# Invariant Governance\n\n"
    "**Lifecycle:** REFERENCE\n"
    "<!-- lifecycle: REFERENCE -->\n\n"
    "*Extracted 2026-07-31 from `operations/invariant-roadmap.md` (docplane#108) -- the "
    "durable methodology that page mixed with a dated status/backlog table. This page is "
    "the canonical home; the operations/ page now tracks status only and points here.*\n\n"
    "---\n\n"
    f"## {DURABLE_SECTIONS[0]}\n\n" + durable_blocks[DURABLE_SECTIONS[0]].split("\n", 1)[1].lstrip("\n") +
    "\n---\n\n" +
    f"## {DURABLE_SECTIONS[1]}\n\n" + durable_blocks[DURABLE_SECTIONS[1]].split("\n", 1)[1].lstrip("\n") +
    "\n---\n\n" +
    f"## {DURABLE_SECTIONS[2]}\n\n" + durable_blocks[DURABLE_SECTIONS[2]].split("\n", 1)[1].lstrip("\n") +
    "\n---\n\n" +
    f"## {DURABLE_SECTIONS[3]}\n\n" + durable_blocks[DURABLE_SECTIONS[3]].split("\n", 1)[1].lstrip("\n") +
    "\n---\n\n"
    "## See Also\n\n"
    "- [SD-WAN Invariants](../sd-wan-invariants.md) -- formal invariant catalogue (I-1 through I-28+)\n"
    "- [Enforcement Model](enforcement-model.md) -- seven-layer enforcement architecture\n"
    "- [Invariant Enforcement -- Status & Backlog](../../operations/invariant-roadmap.md) -- live enforcement status, filed-but-not-implemented backlog, and closed-phase history this methodology governs\n"
)

# Verify each extracted block's full text (minus its own heading line) is present
# verbatim in the new page -- proves no silent rewrite during assembly.
for name in DURABLE_SECTIONS:
    body_only = durable_blocks[name].split("\n", 1)[1]
    assert body_only.strip() in new_page_body, f"content mismatch for {name}"
print("verbatim-inclusion check: OK (all 4 durable blocks present unmodified in new page)")

json.dump({"new_page_body": new_page_body, "durable_hashes": {k: sha(v) for k, v in durable_blocks.items()},
           "spans": {k: list(v) for k, v in spans.items()}},
          open("/tmp/docplane-lifecycle-policy-audit/roadmap-split-new-page.json", "w"), indent=1)
print("\nnew page length:", len(new_page_body))
