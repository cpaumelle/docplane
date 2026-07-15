#!/usr/bin/env python3
"""Remediate nav_path irregularities in the live Docs API.

Classifies every active page against the stricter nav_path rules introduced
in v1.2.x and splits them into three buckets:

  • auto-fix    — mechanical whitespace issues (leading/trailing space on a
                  segment, consecutive spaces). Safe to rewrite without
                  changing hierarchy. Applied when --apply is set.
  • review      — case/whitespace sibling collisions and other soft smells.
                  Reported, never auto-modified.
  • clean       — passes all rules.

Pass --audit for a read-only structural health report (7 checks, no writes).
Pass --normalize-leaves to detect leaf segments whose tokens differ from the
canonical Title Case + acronym-set form. Use with --apply to rewrite them.
Pass --apply to actually PUT the fixed entries; a deploy is triggered by the
API after the last write.

Usage (run from hub2 or any fabric node):

    KEY=$(curl -s https://docs-api.charliehub.net/api/agent-config \\
              | jq -r .auth.key)
    python3 cleanup_nav.py --api-key "$KEY"                             # dry run
    python3 cleanup_nav.py --api-key "$KEY" --apply                     # rewrite whitespace
    python3 cleanup_nav.py --api-key "$KEY" --audit                     # structural audit
    python3 cleanup_nav.py --api-key "$KEY" --normalize-leaves          # leaf casing dry-run
    python3 cleanup_nav.py --api-key "$KEY" --normalize-leaves --apply  # apply leaf fixes

Exit codes:
    0  no changes needed / audit clean, or changes applied cleanly
    1  review items / audit findings exist (human attention needed)
       OR apply saw errors
    2  API / network failure
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from itertools import combinations
from typing import Any


DEFAULT_API = "https://docs-api.charliehub.net"

# ---------------------------------------------------------------------------
# Canonical acronym and preserved-brand sets for --normalize-leaves
#
# ACRONYMS  — upper → canonical. Token is uppercased for lookup; if matched,
#             emit the canonical form. Add entries freely; single-char codes
#             (RF, IO, PG, …) belong here too.
# PRESERVED — lower → canonical. For brand/product names with intentional
#             internal capitalisation that cannot be inferred from the acronym
#             set alone (WireGuard, UniFi, …). Keys are all-lowercase.
#
# Per-token pipeline (rules applied in order):
#   1. token contains "-" → recurse on each hyphen-segment, rejoin with "-"
#   2. token matches ^v\d+(\.\d+)*$ → keep verbatim  (v3, v1.2)
#   3. token.upper() in ACRONYMS   → emit canonical acronym form
#   4. token.lower() in PRESERVED  → emit canonical brand form
#   5. token has non-initial uppercase → CamelCase-split; merge consecutive
#      single-uppercase-letter runs; recurse on each chunk; join with space
#   6. else → str.capitalize()
# ---------------------------------------------------------------------------

_ACRONYMS_RAW: list[str] = [
    "ADR", "API", "BMC", "CBRE", "CPU", "DEV", "DNS", "DNSMASQ", "FR",
    "HA", "HTTP", "HTTPS", "IPMI", "IO", "LAN", "MDS", "MON", "MQTT",
    "NAS", "NTP", "OCC", "OSD", "PBS", "PG", "PROD", "PSU", "RAID", "RAM",
    "RBAC", "RF", "SDK", "SD", "SSH", "SSL", "TCP", "TLS", "TV", "UCG",
    "UK", "URL", "US", "VLAN", "VPN", "WAN", "WG",
]
_ACRONYM_LOOKUP: dict[str, str] = {a.upper(): a for a in _ACRONYMS_RAW}

# lower → canonical  (extend as new brands are discovered via --normalize-leaves)
_PRESERVED: dict[str, str] = {
    "charlihub":   "CharlieHub",
    "charliehub":  "CharlieHub",
    "ebay":        "eBay",
    "filebrowser": "FileBrowser",
    "github":      "GitHub",
    "idnet":       "IDNet",
    "imbuildings": "IMBuildings",
    "lorawan":     "LoRaWAN",
    "lora":        "LoRa",
    "macos":       "macOS",
    "mkdocs":      "MkDocs",
    "optiplex":    "OptiPlex",
    "ovhcloud":    "OVHcloud",
    "pestscan":    "PestScan",
    "pikvm":       "PiKVM",
    "pommetv":     "PommeTV",
    "postgresql":  "PostgreSQL",
    "sitescan":    "SiteScan",
    "unifi":       "UniFi",
    "wireguard":   "WireGuard",
    "ch-transit-core": "ch-transit-core",
}

_VERSION_RE = re.compile(r"^v\d+(\.\d+)*$")


def _camel_split(token: str) -> list[str]:
    """Split a CamelCase/PascalCase token into constituent words.

    Uses two-pass regex:
      - [A-Z]{2,}(?=[A-Z][a-z]|\b)  → uppercase acronym run  (OSD, PG)
      - [A-Z]?[a-z]+                  → capitalized/lowercase word  (Ceph, Down)
      - [A-Z]                         → stray single uppercase letter

    After splitting, consecutive single-uppercase-letter tokens are merged
    into a run so that e.g. ["O","S","D"] → ["OSD"] before the caller
    checks the acronym lookup.
    """
    raw = re.findall(r"[A-Z]{2,}(?=[A-Z][a-z]|)|[A-Z]?[a-z]+|[A-Z]", token)
    if not raw:
        return [token]
    # Merge runs of single uppercase chars
    merged: list[str] = []
    run: list[str] = []
    for part in raw:
        if len(part) == 1 and part.isupper():
            run.append(part)
        else:
            if run:
                merged.append("".join(run))
                run = []
            merged.append(part)
    if run:
        merged.append("".join(run))
    return merged


def _normalize_token(tok: str) -> str:
    """Apply the 6-step per-token pipeline. Called recursively for hyphens/parens."""
    if not tok:
        return tok

    # Paren stripping: strip leading "(" and trailing ")", process inner, restore.
    prefix, suffix = "", ""
    while tok.startswith("("):
        prefix += "("
        tok = tok[1:]
    while tok.endswith(")"):
        suffix += ")"
        tok = tok[:-1]
    if prefix or suffix:
        return prefix + _normalize_token(tok) + suffix

    # Rule 4 — preserved brand/product (checked before hyphen-split so that
    # multi-segment slugs like "ch-transit-core" are matched whole).
    if tok.lower() in _PRESERVED:
        return _PRESERVED[tok.lower()]

    # Rule 1 — hyphenated compound: recurse on each segment
    if "-" in tok:
        return "-".join(_normalize_token(p) for p in tok.split("-"))

    # Rule 2 — version string: keep verbatim
    if _VERSION_RE.match(tok):
        return tok

    # Rule 3 — known acronym
    if tok.upper() in _ACRONYM_LOOKUP:
        return _ACRONYM_LOOKUP[tok.upper()]

    # Rule 3b — unlisted all-caps abbreviation: preserve as-is.
    # isupper() returns True when all *cased* chars are uppercase (digits/symbols
    # are ignored), so VPS3, PC4.0 etc. are correctly caught here.
    if tok.isupper() and len(tok) > 1:
        return tok

    # Rule 5 — mixed-case CamelCase (has non-initial uppercase AND is not all-caps)
    if len(tok) > 1 and not tok.isupper() and any(c.isupper() for c in tok[1:]):
        parts = _camel_split(tok)
        if len(parts) > 1:
            return " ".join(_normalize_token(p) for p in parts)

    # Rule 6 — plain word: Title Case
    return tok.capitalize()


def normalize_leaf(leaf: str) -> str:
    """Normalise a nav_path leaf using the 6-step per-token pipeline."""
    return " ".join(_normalize_token(tok) for tok in leaf.split())


def _unknown_mixed_case_tokens(leaf: str) -> list[str]:
    """Return tokens that have non-initial uppercase but are not in ACRONYMS or PRESERVED.

    All-caps unlisted abbreviations (rule 3b) are included — they are preserved
    correctly but may warrant an explicit ACRONYMS entry for documentation.
    Mixed-case tokens that fell into rule 5 are also included — they may need
    an entry in PRESERVED if they are brand names.
    """
    unknowns = []
    for tok in leaf.split():
        # Strip parens before checking
        inner = tok.lstrip("(").rstrip(")")
        if not inner:
            continue
        if "-" in inner:
            for part in inner.split("-"):
                unknowns.extend(_unknown_mixed_case_tokens(part))
            continue
        if _VERSION_RE.match(inner):
            continue
        if inner.upper() in _ACRONYM_LOOKUP:
            continue
        if inner.lower() in _PRESERVED:
            continue
        if len(inner) > 1 and any(c.isupper() for c in inner[1:]):
            unknowns.append(inner)
    return unknowns


def compute_leaf_fixes(pages: list[dict]) -> tuple[list[dict], dict[str, list[str]]]:
    """Return (fixes, unknown_tokens) where:

    fixes          — pages whose nav_path leaf changes after normalisation
    unknown_tokens — token → [page paths] for tokens that triggered rule 5
                     but are not in _ACRONYM_LOOKUP or _PRESERVED
    """
    fixes: list[dict] = []
    unknowns: dict[str, list[str]] = {}
    for p in pages:
        nav = p["nav_path"]
        if nav == "Home":
            continue
        parts = nav.split("/")
        leaf = parts[-1]
        canonical = normalize_leaf(leaf)
        if canonical != leaf:
            fixes.append({
                "path": p["path"],
                "old_nav": nav,
                "new_nav": "/".join(parts[:-1] + [canonical]),
                "old_leaf": leaf,
                "new_leaf": canonical,
            })
        for tok in _unknown_mixed_case_tokens(leaf):
            unknowns.setdefault(tok, []).append(p["path"])
    return fixes, unknowns


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def _normalize_segment(seg: str) -> str:
    """Mechanical whitespace cleanup — strip, then collapse runs of spaces."""
    out = seg.strip()
    while "  " in out:
        out = out.replace("  ", " ")
    return out


def classify(nav_path: str) -> dict[str, Any]:
    """Return {'kind': 'clean'|'auto-fix'|'review', ...}."""
    if nav_path == "Home":
        return {"kind": "clean"}

    parts = nav_path.split("/")

    if any(not p for p in parts):
        return {"kind": "review", "reason": "empty segment (contains '//')"}
    if len(parts) < 2:
        return {"kind": "review", "reason": "no '/' — must be 'Section/Title'"}

    cleaned_parts = [_normalize_segment(p) for p in parts]
    if cleaned_parts != parts:
        return {
            "kind": "auto-fix",
            "new_nav_path": "/".join(cleaned_parts),
            "reason": "whitespace-edge or consecutive-space segments",
        }

    return {"kind": "clean"}


def sibling_collisions(pages: list[dict]) -> list[dict]:
    """Detect sibling segments whose canonical form collides."""
    prefix_re = re.compile(r"^\d+\s+")

    def canon(s: str) -> str:
        return prefix_re.sub("", s).strip().casefold()

    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    for p in pages:
        nav = p["nav_path"]
        if nav == "Home":
            continue
        parts = nav.split("/")
        for i, seg in enumerate(parts):
            parent = "/".join(parts[:i])
            seen[(parent, canon(seg))].add(seg)

    collisions = []
    for (parent, c), raws in seen.items():
        if len(raws) > 1:
            collisions.append({
                "parent": parent or "(root)",
                "canonical": c,
                "variants": sorted(raws),
            })
    return collisions


def _single_child_heuristic(pages: list[dict]) -> list[tuple[dict, str]]:
    """One-child section with a short/ALL-CAPS name that reads like a compound title."""
    section_leaf_counts: Counter = Counter()
    for p in pages:
        if p["nav_path"] == "Home":
            continue
        parts = p["nav_path"].split("/")
        if len(parts) >= 2:
            section_leaf_counts[tuple(parts[:-1])] += 1

    suspects = []
    for p in pages:
        if p["nav_path"] == "Home":
            continue
        parts = p["nav_path"].split("/")
        if len(parts) < 3:
            continue
        parent = tuple(parts[:-1])
        if section_leaf_counts[parent] == 1:
            section_name = parts[-2]
            if section_name.isupper() and len(section_name) <= 4:
                suspects.append((p, section_name))
    return suspects


# ---------------------------------------------------------------------------
# Audit checks
# ---------------------------------------------------------------------------

_DOMAIN_SUFFIX_RE = re.compile(r"\b(hub2|legacy|archive|old)\b", re.IGNORECASE)
_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*")


def _strip_domain(name: str) -> str:
    """Remove parentheticals and known domain suffixes; normalise, casefold."""
    s = _PAREN_RE.sub(" ", name)
    s = _DOMAIN_SUFFIX_RE.sub(" ", s)
    return " ".join(s.split()).casefold()


def _edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for ch in a:
        curr = [prev[0] + 1]
        for j, d in enumerate(b):
            curr.append(min(prev[j] + (0 if ch == d else 1),
                            prev[j + 1] + 1, curr[-1] + 1))
        prev = curr
    return prev[-1]


def _edit_threshold(s: str) -> int:
    return 1 if len(s) < 8 else 2


def _parent_sections(pages: list[dict]) -> dict[str, set[str]]:
    """Map parent_path → set of direct child section names (non-leaf segments)."""
    parent_to_children: dict[str, set[str]] = defaultdict(set)
    for p in pages:
        nav = p["nav_path"]
        if nav == "Home":
            continue
        parts = nav.split("/")
        # Every segment except the last is a section name
        for depth in range(len(parts) - 1):
            parent = "/".join(parts[:depth]) if depth > 0 else ""
            child = parts[depth]
            parent_to_children[parent].add(child)
    return parent_to_children


def check1_near_duplicate_sections(pages: list[dict]) -> list[dict]:
    """Near-duplicate sibling section names via edit distance on stripped forms.

    Threshold: ≤1 if either stripped string is <8 chars; ≤2 otherwise.
    Reports both raw and stripped forms so the caller can judge.
    """
    findings = []
    for parent, children in _parent_sections(pages).items():
        children_list = sorted(children)
        for a, b in combinations(children_list, 2):
            a_s = _strip_domain(a)
            b_s = _strip_domain(b)
            threshold = 1 if (len(a_s) < 8 or len(b_s) < 8) else 2
            dist = _edit_distance(a_s, b_s)
            if dist <= threshold:
                findings.append({
                    "parent": parent or "(root)",
                    "a": {"raw": a, "stripped": a_s},
                    "b": {"raw": b, "stripped": b_s},
                    "edit_distance": dist,
                })
    return findings


def check2_prefix_superset_siblings(pages: list[dict]) -> list[dict]:
    """Siblings where one's word-token set is a strict subset of another's."""
    findings = []
    for parent, children in _parent_sections(pages).items():
        tokenized = {c: set(c.casefold().split()) for c in children}
        for a, b in combinations(sorted(children), 2):
            ta, tb = tokenized[a], tokenized[b]
            if ta < tb:
                findings.append({"parent": parent or "(root)", "subset": a, "superset": b})
            elif tb < ta:
                findings.append({"parent": parent or "(root)", "subset": b, "superset": a})
    return findings


_KEYWORD_STOP = {
    "about", "appendix", "architecture", "changelog", "config", "content",
    "design", "detail", "getting", "guide", "howto", "index", "intro",
    "introduction", "local", "misc", "model", "notes", "other", "overview",
    "quick", "reference", "runbook", "setup", "start", "started", "using",
}


def check3_keyword_crossover(pages: list[dict], top_n: int = 10) -> list[dict]:
    """Nav-leaf keywords appearing across ≥3 distinct top-level sections.

    Returns top_n results sorted by cross-section count descending.
    """
    word_sections: dict[str, set[str]] = defaultdict(set)
    for p in pages:
        nav = p["nav_path"]
        if nav == "Home":
            continue
        parts = nav.split("/")
        top_section = parts[0]
        leaf_title = parts[-1]
        for word in leaf_title.split():
            w = re.sub(r"[^a-z]", "", word.casefold())
            if len(w) >= 5 and w not in _KEYWORD_STOP:
                word_sections[w].add(top_section)

    candidates = [
        {"keyword": w, "section_count": len(secs), "sections": sorted(secs)}
        for w, secs in word_sections.items()
        if len(secs) >= 3
    ]
    candidates.sort(key=lambda x: (-x["section_count"], x["keyword"]))
    return candidates[:top_n]


def check4_filesystem_nav_drift(pages: list[dict]) -> list[dict]:
    """Directories whose pages span ≥2 nav top-level sections, and vice versa.

    Reports minority_count and minority_ratio so the caller can distinguish
    1-of-21 outliers from genuine deliberate splits.
    """
    findings: list[dict] = []

    # Direction 1: filesystem dir → nav sections
    dir_navs: dict[str, list[str]] = defaultdict(list)
    for p in pages:
        nav = p["nav_path"]
        if nav == "Home":
            continue
        fs_dir = p["path"].rsplit("/", 1)[0] if "/" in p["path"] else "__root__"
        dir_navs[fs_dir].append(nav.split("/")[0])

    for fs_dir, navs in sorted(dir_navs.items()):
        counts = Counter(navs)
        if len(counts) < 2:
            continue
        total = sum(counts.values())
        majority_n = counts.most_common(1)[0][1]
        minority = total - majority_n
        findings.append({
            "type": "dir→nav",
            "dir": fs_dir,
            "total": total,
            "minority_count": minority,
            "minority_ratio": round(minority / total, 2),
            "breakdown": dict(counts.most_common()),
        })

    # Direction 2: nav sub-section → filesystem dirs (depth ≥ 2 only)
    # Groups by full nav section path (all but leaf segment).
    nav_section_dirs: dict[str, list[str]] = defaultdict(list)
    for p in pages:
        nav = p["nav_path"]
        if nav == "Home":
            continue
        parts = nav.split("/")
        if len(parts) < 3:
            continue  # skip top-level sections — expected to span many dirs
        section = "/".join(parts[:-1])
        fs_dir = p["path"].rsplit("/", 1)[0] if "/" in p["path"] else "__root__"
        nav_section_dirs[section].append(fs_dir)

    for section, dirs in sorted(nav_section_dirs.items()):
        counts = Counter(dirs)
        if len(counts) < 2:
            continue
        total = sum(counts.values())
        majority_n = counts.most_common(1)[0][1]
        minority = total - majority_n
        findings.append({
            "type": "nav→dir",
            "section": section,
            "total": total,
            "minority_count": minority,
            "minority_ratio": round(minority / total, 2),
            "breakdown": dict(counts.most_common()),
        })

    return findings


def check5_single_child_sections(pages: list[dict]) -> list[dict]:
    """Section nodes with exactly one direct child (leaf or sub-section).

    Returns every such section with its single child name and one example page.
    """
    section_children: dict[str, set[str]] = defaultdict(set)
    section_example: dict[str, str] = {}

    for p in pages:
        nav = p["nav_path"]
        if nav == "Home":
            continue
        parts = nav.split("/")
        for depth in range(len(parts) - 1):
            section_path = "/".join(parts[: depth + 1])
            child = parts[depth + 1] if depth + 1 < len(parts) else None
            if child:
                section_children[section_path].add(child)
                section_example.setdefault(section_path, p["path"])

    return [
        {
            "section": sec,
            "child": next(iter(children)),
            "example_page": section_example.get(sec, ""),
        }
        for sec, children in sorted(section_children.items())
        if len(children) == 1
    ]


def check6_numeric_prefix_inconsistency(pages: list[dict]) -> list[dict]:
    """Siblings inconsistently using '01 Foo' ordering, or with numeric gaps."""
    num_re = re.compile(r"^(\d+)\s+(.+)")
    findings: list[dict] = []

    for parent, children in _parent_sections(pages).items():
        numbered = []
        unnumbered = []
        for c in sorted(children):
            m = num_re.match(c)
            if m:
                numbered.append((int(m.group(1)), c))
            else:
                unnumbered.append(c)

        if numbered and unnumbered:
            findings.append({
                "type": "mixed-style",
                "parent": parent or "(root)",
                "numbered": [c for _, c in numbered],
                "unnumbered": unnumbered,
            })
        elif len(numbered) >= 2:
            nums = sorted(n for n, _ in numbered)
            expected = list(range(nums[0], nums[0] + len(nums)))
            gaps = sorted(set(expected) - set(nums))
            if gaps:
                findings.append({
                    "type": "gap",
                    "parent": parent or "(root)",
                    "numbers_found": nums,
                    "gaps": gaps,
                })

    return findings


_BASENAME_STOP = {
    "index", "overview", "architecture", "operations", "readme",
    "changelog", "reference", "introduction", "getting-started",
}


def check7_multi_home_suspects(pages: list[dict]) -> list[dict]:
    """Pages sharing an exact basename stem but living in different nav sections.

    Groups by `basename-without-extension`. Flags groups spanning ≥2 top-level
    nav sections. Common generic basenames (index, overview, …) are excluded.
    """
    stem_pages: dict[str, list[dict]] = defaultdict(list)
    for p in pages:
        basename = p["path"].rsplit("/", 1)[-1]
        stem = basename.rsplit(".", 1)[0]
        if stem not in _BASENAME_STOP:
            stem_pages[stem].append(p)

    findings = []
    for stem, pgs in sorted(stem_pages.items()):
        if len(pgs) < 2:
            continue
        nav_sections = {p["nav_path"].split("/")[0] for p in pgs}
        if len(nav_sections) >= 2:
            findings.append({
                "stem": stem,
                "pages": [
                    {"path": p["path"], "nav": p["nav_path"]}
                    for p in sorted(pgs, key=lambda x: x["path"])
                ],
            })

    return findings


# ---------------------------------------------------------------------------
# Audit report printer
# ---------------------------------------------------------------------------


def run_audit(pages: list[dict]) -> bool:
    """Run all 7 checks and print a structured report. Returns True if clean."""
    any_finding = False

    def header(n: int, title: str) -> None:
        print(f"Check {n} — {title}")

    def no_findings() -> None:
        print("  (none)")

    print(f"Auditing {len(pages)} active pages …\n")

    # --- Check 1 ---
    header(1, "Near-duplicate section names")
    c1 = check1_near_duplicate_sections(pages)
    if c1:
        any_finding = True
        for f in c1:
            print(f"  under {f['parent']}:")
            print(f"    {f['a']['raw']!r}  →  stripped: {f['a']['stripped']!r}")
            print(f"    {f['b']['raw']!r}  →  stripped: {f['b']['stripped']!r}")
            print(f"    edit distance (stripped): {f['edit_distance']}")
    else:
        no_findings()
    print()

    # --- Check 2 ---
    header(2, "Prefix/superset sibling sections")
    c2 = check2_prefix_superset_siblings(pages)
    if c2:
        any_finding = True
        for f in c2:
            print(f"  under {f['parent']}:")
            print(f"    {f['subset']!r} tokens ⊂ {f['superset']!r} tokens")
    else:
        no_findings()
    print()

    # --- Check 3 ---
    header(3, "Cross-section keyword overlap (top 10 by section count)")
    c3 = check3_keyword_crossover(pages)
    if c3:
        any_finding = True
        for f in c3:
            print(f"  {f['keyword']!r}  ({f['section_count']} sections: "
                  f"{', '.join(f['sections'])})")
    else:
        no_findings()
    print()

    # --- Check 4 ---
    header(4, "Filesystem ↔ nav section drift")
    c4 = check4_filesystem_nav_drift(pages)
    if c4:
        any_finding = True
        for f in c4:
            if f["type"] == "dir→nav":
                pct = int(f["minority_ratio"] * 100)
                print(f"  [dir→nav]  {f['dir']}/"
                      f"  ({f['minority_count']}/{f['total']} = {pct}% in minority nav)")
                for nav, cnt in sorted(f["breakdown"].items(), key=lambda x: -x[1]):
                    print(f"             {cnt:3d} pages → {nav!r}")
            else:
                pct = int(f["minority_ratio"] * 100)
                print(f"  [nav→dir]  {f['section']}"
                      f"  ({f['minority_count']}/{f['total']} = {pct}% from minority dir)")
                for d, cnt in sorted(f["breakdown"].items(), key=lambda x: -x[1]):
                    print(f"             {cnt:3d} pages ← {d!r}")
    else:
        no_findings()
    print()

    # --- Check 5 ---
    header(5, "Single-child sections")
    c5 = check5_single_child_sections(pages)
    if c5:
        any_finding = True
        for f in c5:
            print(f"  {f['section']!r}  →  sole child: {f['child']!r}"
                  f"  (e.g. {f['example_page']})")
    else:
        no_findings()
    print()

    # --- Check 6 ---
    header(6, "Inconsistent numeric-prefix ordering")
    c6 = check6_numeric_prefix_inconsistency(pages)
    if c6:
        any_finding = True
        for f in c6:
            if f["type"] == "mixed-style":
                print(f"  under {f['parent']}:  mixed numeric / non-numeric siblings")
                print(f"    numbered:   {f['numbered']}")
                print(f"    unnumbered: {f['unnumbered']}")
            else:
                print(f"  under {f['parent']}:  numeric gap — "
                      f"have {f['numbers_found']}, missing {f['gaps']}")
    else:
        no_findings()
    print()

    # --- Check 7 ---
    header(7, "Multi-home suspects (same filename, different nav sections)")
    c7 = check7_multi_home_suspects(pages)
    if c7:
        any_finding = True
        for f in c7:
            print(f"  stem: {f['stem']!r}  ({len(f['pages'])} pages across "
                  f"{len({p['nav'].split('/')[0] for p in f['pages']})} top-level sections)")
            for pg in f["pages"]:
                print(f"    {pg['path']}  →  {pg['nav']!r}")
    else:
        no_findings()
    print()

    if any_finding:
        print("Audit complete — findings above need human review.")
    else:
        print("Audit complete — nav is structurally clean.")

    return not any_finding


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


class DocsAPI:
    def __init__(self, base: str, key: str):
        self.base = base.rstrip("/")
        self.key = key

    def _req(self, method: str, path: str, body: dict | None = None,
             headers: dict | None = None) -> tuple[int, dict, dict]:
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-API-Key", self.key)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            resp = urllib.request.urlopen(req)  # noqa: S310
        except urllib.error.HTTPError as e:
            payload = e.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(payload)
            except Exception:
                pass
            return e.code, {k.lower(): v for k, v in e.headers.items()}, payload
        resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp.status, resp_headers, json.loads(resp.read() or "null")

    def list_pages(self) -> list[dict]:
        status, _, body = self._req("GET", "/api/docs/pages")
        if status != 200:
            raise RuntimeError(f"list pages: {status} {body}")
        return body

    def get_page(self, path: str) -> tuple[dict, str]:
        status, headers, body = self._req("GET", f"/api/docs/pages/{path}")
        if status != 200:
            raise RuntimeError(f"get page {path}: {status} {body}")
        etag = headers.get("etag", "").strip('"')
        return body, etag

    def put_page(self, path: str, payload: dict, etag: str) -> dict:
        status, _, body = self._req(
            "PUT", f"/api/docs/pages/{path}", body=payload,
            headers={"If-Match": f'"{etag}"'},
        )
        if status not in (200, 201):
            raise RuntimeError(f"put page {path}: {status} {body}")
        return body


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--api", default=DEFAULT_API,
                    help=f"base URL (default: {DEFAULT_API})")
    ap.add_argument("--api-key", required=True,
                    help="Docs API key (X-API-Key header)")
    ap.add_argument("--apply", action="store_true",
                    help="actually rewrite auto-fix entries (default: dry-run)")
    ap.add_argument("--audit", action="store_true",
                    help="run structural audit (read-only, 7 checks, no writes)")
    ap.add_argument("--normalize-leaves", action="store_true", dest="normalize_leaves",
                    help="detect/fix leaf casing against canonical acronym set")
    ap.add_argument("--updated-by", default="cleanup_nav",
                    help="value for updated_by on rewrites (default: cleanup_nav)")
    args = ap.parse_args()

    try:
        api = DocsAPI(args.api, args.api_key)
        pages = api.list_pages()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # --- Audit mode ---------------------------------------------------------
    if args.audit:
        clean = run_audit(pages)
        return 0 if clean else 1

    # --- Normalize-leaves mode ----------------------------------------------
    if args.normalize_leaves:
        fixes, unknowns = compute_leaf_fixes(pages)
        if not fixes and not unknowns:
            print(f"Scanned {len(pages)} pages — all leaf segments are canonical.")
            return 0
        if fixes:
            print(f"Scanned {len(pages)} pages — {len(fixes)} leaf(s) need normalisation:\n")
            for f in fixes:
                print(f"  {f['path']}")
                print(f"    from: {f['old_leaf']!r}  →  {f['new_leaf']!r}")
            print()
        if unknowns:
            print("Unknown mixed-case tokens (rule 5 — confirm or add to _PRESERVED):")
            for tok, paths in sorted(unknowns.items()):
                print(f"  {tok!r}  ({len(paths)} page(s): {', '.join(paths[:3])}"
                      f"{'…' if len(paths) > 3 else ''})")
            print()
        if not fixes:
            print("No leaf changes needed — only unknown tokens above.")
            return 1 if unknowns else 0
        if not args.apply:
            print("Dry run — re-run with --apply to rewrite.")
            return 1
        print(f"Applying {len(fixes)} fixes …\n")
        errors = 0
        for f in fixes:
            pth = f["path"]
            try:
                current, etag = api.get_page(pth)
            except Exception as exc:
                print(f"  SKIP {pth}: GET failed: {exc}")
                errors += 1
                continue
            payload = {
                "title": current["title"],
                "nav_path": f["new_nav"],
                "content": current["content"],
                "updated_by": args.updated_by,
            }
            try:
                api.put_page(pth, payload, etag)
                print(f"  OK   {pth}  ({f['old_leaf']!r} → {f['new_leaf']!r})")
            except Exception as exc:
                print(f"  FAIL {pth}: {exc}")
                errors += 1
        print()
        if errors:
            print(f"{errors} error(s) — investigate above.")
            return 1
        print("All leaf fixes applied. Deploy triggered automatically.")
        return 0 if not unknowns else 1

    # --- Cleanup mode -------------------------------------------------------
    print(f"Scanning {len(pages)} active pages from {args.api} …\n")

    buckets: dict[str, list] = defaultdict(list)
    for p in pages:
        c = classify(p["nav_path"])
        buckets[c["kind"]].append((p, c))

    auto = buckets["auto-fix"]
    review = buckets["review"]
    clean = buckets["clean"]

    print(f"  clean:    {len(clean)}")
    print(f"  auto-fix: {len(auto)}")
    print(f"  review:   {len(review)}")
    print()

    if auto:
        print("Auto-fix candidates (whitespace normalisation):")
        for p, c in auto:
            print(f"  {p['path']}")
            print(f"    from: {p['nav_path']!r}")
            print(f"    to:   {c['new_nav_path']!r}")
        print()

    if review:
        print("Review (NOT auto-modified):")
        for p, c in review:
            print(f"  {p['path']}  nav_path={p['nav_path']!r}")
            print(f"    reason: {c['reason']}")
        print()

    collisions = sibling_collisions(pages)
    if collisions:
        print("Sibling collisions (case/whitespace/prefix variants render the same):")
        for col in collisions:
            print(f"  under {col['parent']}: variants {col['variants']}")
        print()

    suspects = _single_child_heuristic(pages)
    if suspects:
        print("Likely nav_path nesting mistakes (one-child section with short/ALL-CAPS name):")
        for p, sec in suspects:
            print(f"  {p['path']}  nav_path={p['nav_path']!r}")
            print(f"    suspicion: parent {sec!r} holds one leaf"
                  " — possibly meant as a single title segment")
        print()

    if not args.apply:
        if auto or review or collisions or suspects:
            print("Dry run — re-run with --apply to rewrite the auto-fix entries.")
            print("Review / collisions / nesting suspects need a human decision and")
            print("will not be touched by this script.")
        else:
            print("Nothing to do — nav is clean.")
        return 0 if not (review or collisions or suspects) else 1

    if not auto:
        print("No auto-fix entries to apply.")
        return 0 if not (review or collisions) else 1

    print(f"Applying {len(auto)} fixes …\n")
    errors = 0
    for p, c in auto:
        path = p["path"]
        try:
            current, etag = api.get_page(path)
        except Exception as exc:
            print(f"  SKIP {path}: GET failed: {exc}")
            errors += 1
            continue
        payload = {
            "title": current["title"],
            "nav_path": c["new_nav_path"],
            "content": current["content"],
            "updated_by": args.updated_by,
        }
        try:
            api.put_page(path, payload, etag)
            print(f"  OK   {path}")
        except Exception as exc:
            print(f"  FAIL {path}: {exc}")
            errors += 1

    print()
    if errors:
        print(f"{errors} errors — investigate above.")
        return 1
    print("All fixes applied. Deploy was triggered automatically by the API.")
    return 0 if not (review or collisions) else 1


if __name__ == "__main__":
    sys.exit(main())
