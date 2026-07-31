import json, hashlib, re

d = json.load(open("/tmp/docplane-lifecycle-policy-audit/page-roadmap-fresh.json"))
content = d["content"]
prior = json.load(open("/tmp/docplane-lifecycle-policy-audit/roadmap-split-new-page.json"))
spans = {k: tuple(v) for k, v in prior["spans"].items()}
new_page_body = prior["new_page_body"]

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

DURABLE_SECTIONS = ["When to add a new invariant", "Explicit non-goals", "Design principles", "Maturity model"]

# Remove the 4 durable spans in descending start-offset order.
ordered = sorted(spans.items(), key=lambda kv: -kv[1][0])
modified = content
for name, (s, e) in ordered:
    modified = modified[:s] + modified[e:]

pointer = (
    "## Durable methodology has moved\n\n"
    "The invariant-justification principles, non-goals, design principles, and maturity "
    "model that used to live on this page are now on\n"
    "[Invariant Governance](../control-plane/foundational/invariant-governance.md) "
    "(docplane#108) -- they were timeless methodology mixed in with this page's dated "
    "status/backlog content, and needed a canonical, non-duplicated home. Nothing was "
    "reworded in the move; see the extraction manifest for exact content hashes.\n\n"
    "---\n\n"
)

insert_at = spans["When to add a new invariant"][0]
modified = modified[:insert_at] + pointer + modified[insert_at:]

# Header: title, lifecycle marker, last-updated date, per the "stay in operations,
# revised title and lifecycle" disposition (docplane#108) -- this page is now purely
# a status/backlog/history tracker, not a mixed doctrine+planning document.
modified = modified.replace(
    "# CharlieHub Control Plane — Invariant Roadmap",
    "# CharlieHub Control Plane — Invariant Enforcement Status & Backlog",
    1,
)
modified = modified.replace(
    "**Lifecycle:** REFERENCE\n<!-- lifecycle: REFERENCE -->",
    "**Lifecycle:** BACKLOG\n<!-- lifecycle: BACKLOG -->",
    1,
)
modified = modified.replace(
    "*Last updated: 2026-05-28 00:00*",
    "*Last updated: 2026-07-31 -- durable methodology split out to "
    "control-plane/foundational/invariant-governance.md (docplane#108); the status/"
    "backlog content below is otherwise unchanged from 2026-05-28 and has not been "
    "re-verified against current shipped state as part of this split.*",
    1,
)
# See Also: add the new canonical page, drop the now-absent internal duplicate context.
modified = modified.replace(
    "- [Enforcement Model](../control-plane/foundational/enforcement-model.md) — seven-layer enforcement architecture",
    "- [Invariant Governance](../control-plane/foundational/invariant-governance.md) — durable invariant-justification methodology (split out docplane#108)\n"
    "- [Enforcement Model](../control-plane/foundational/enforcement-model.md) — seven-layer enforcement architecture",
    1,
)

json.dump({"modified_body": modified}, open("/tmp/docplane-lifecycle-policy-audit/roadmap-split-modified-page.json", "w"), indent=1)

print("modified length:", len(modified), "(original", len(content), ")")

# --- Content-conservation proof: every character of the original is accounted for ---
# Reconstruct: original == (modified with pointer+header/footer edits reverted) plumbed
# back together with the 4 durable spans reinserted at their original offsets.
recon = modified
recon = recon.replace(pointer, "", 1)
recon = recon.replace(
    "# CharlieHub Control Plane — Invariant Enforcement Status & Backlog",
    "# CharlieHub Control Plane — Invariant Roadmap", 1)
recon = recon.replace("**Lifecycle:** BACKLOG\n<!-- lifecycle: BACKLOG -->", "**Lifecycle:** REFERENCE\n<!-- lifecycle: REFERENCE -->", 1)
recon = recon.replace(
    "- [Invariant Governance](../control-plane/foundational/invariant-governance.md) — durable invariant-justification methodology (split out docplane#108)\n"
    "- [Enforcement Model](../control-plane/foundational/enforcement-model.md) — seven-layer enforcement architecture",
    "- [Enforcement Model](../control-plane/foundational/enforcement-model.md) — seven-layer enforcement architecture", 1)
old_stamp = ("*Last updated: 2026-07-31 -- durable methodology split out to "
    "control-plane/foundational/invariant-governance.md (docplane#108); the status/"
    "backlog content below is otherwise unchanged from 2026-05-28 and has not been "
    "re-verified against current shipped state as part of this split.*")
recon = recon.replace(old_stamp, "*Last updated: 2026-05-28 00:00*", 1)

ordered_asc = sorted(spans.items(), key=lambda kv: kv[1][0])
rebuilt = recon
offset_shift = 0
# Reinsert spans in ascending order at their ORIGINAL offsets against the ORIGINAL content,
# by rebuilding from recon's own structure: recon should now equal content minus the 4 spans.
expected_stripped = content
for name, (s, e) in sorted(spans.items(), key=lambda kv: -kv[1][0]):
    expected_stripped = expected_stripped[:s] + expected_stripped[e:]

assert recon == expected_stripped, "reconstruction mismatch -- header/pointer edits not cleanly reversible"
print("content-conservation proof: OK -- modified page reduces to exactly "
      "(original minus the 4 durable spans) once the pointer/header edits are reverted")
