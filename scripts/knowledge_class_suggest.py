#!/usr/bin/env python3
"""Knowledge-class backfill suggestions — names-only, performs no writes.

Sprint 10 precursor. The domain model (issue #64, answer 2) sequences the
knowledge_class rollout as audit → backfill → CHECK constraint. This tool is
the middle step's evidence: it proposes a knowledge class for every page that
lacks one, from three signal tiers, and emits a curatable report in the same
names-only contract style as the meter-list service-map suggestions. It never
mutates anything; the governed apply path is the trust classify verb, driven
by an operator from the CURATED report.

Signal tiers (strongest wins):
1. Legacy inline lifecycle — the deprecated markdown vocabulary
   (**Lifecycle:** X / <!-- lifecycle: x -->) that much of the corpus already
   carries. REFERENCE and OPERATION map directly; the GTD work-state values
   (ACTIVE/PAUSED/BACKLOG) mark work-domain page forms → WORK_NOTE; ARCHIVED
   is an archive-state signal, not a knowledge class, and proposes nothing.
2. Content shape — ADR anatomy → DECISION; runbook content-contract headings
   → OPERATION; an invariants register (INV-n entries) → POLICY.
3. Path prefix — the section conventions, at lower confidence.

Pages that already carry a class are skipped, EXCEPT when a tier-1 legacy
field disagrees with the stored class: those surface in `conflicts`, because
one of the two is wrong and a human should look.

Usage:
  DOCPLANE_API=https://… DOCPLANE_TOKEN=dp_… \
      python3 scripts/knowledge_class_suggest.py --out suggestions.json
  python3 scripts/knowledge_class_suggest.py --from-dir /path/to/markdown

The token is read from the environment, never argv (bearer-in-argv is a
recorded runbook failure mode).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from schema_catalogue import ApiError, Client  # noqa: E402  (shared API client)

CONTRACT_VERSION = "knowledge-class-suggestions-v1"

KNOWLEDGE_CLASSES = (
    "ARCHITECTURE", "OPERATION", "REFERENCE", "POLICY",
    "DECISION", "EVIDENCE", "DESIGN", "WORK_NOTE",
)

# Lockstep with app.corpus_structure lifecycle_of() — copied rather than
# imported so this script has no docs-api package dependency.
_LIFECYCLE_FIELD_RE = re.compile(r"\*\*Lifecycle:\*\*\s*([A-Za-z_-]+)", re.IGNORECASE)
_LIFECYCLE_COMMENT_RE = re.compile(r"<!--\s*lifecycle:\s*([A-Za-z_-]+)\s*-->", re.IGNORECASE)

LEGACY_LIFECYCLE_TO_CLASS = {
    "REFERENCE": "REFERENCE",
    "OPERATION": "OPERATION",
    "ACTIVE": "WORK_NOTE",
    "PAUSED": "WORK_NOTE",
    "BACKLOG": "WORK_NOTE",
    # ARCHIVED: archive state, not a knowledge class — deliberately absent.
}

_ADR_TITLE_RE = re.compile(r"\bADR[-_ ]?\d+\b", re.IGNORECASE)
_INVARIANT_RE = re.compile(r"\bINV-\d+\b")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)

_RUNBOOK_HEADINGS = {"preconditions", "procedure", "rollback", "success checks", "success check"}
_ADR_HEADINGS = {"context", "decision", "consequences"}

# Section conventions → (class, confidence). First matching prefix wins;
# order matters. Weak entries (0.5) exist to leave no obvious section
# unproposed — curation, not the tool, is the authority.
PATH_PREFIX_MAP: tuple[tuple[str, str, float], ...] = (
    ("operations/", "OPERATION", 0.6),
    ("runbooks/", "OPERATION", 0.6),
    ("disaster-recovery/", "OPERATION", 0.6),
    ("reference/", "REFERENCE", 0.6),
    ("architecture/", "ARCHITECTURE", 0.6),
    ("decisions/", "DECISION", 0.6),
    ("adr/", "DECISION", 0.6),
    ("policies/", "POLICY", 0.6),
    ("policy/", "POLICY", 0.6),
    ("evidence/", "EVIDENCE", 0.6),
    ("design/", "DESIGN", 0.6),
    ("getting-started/", "REFERENCE", 0.5),
    ("sites/", "REFERENCE", 0.5),
    ("network/", "REFERENCE", 0.5),
    ("services/", "REFERENCE", 0.5),
    ("infrastructure/", "REFERENCE", 0.5),
    ("agent-guides/", "REFERENCE", 0.5),
    ("agent-context/", "REFERENCE", 0.5),
)


def legacy_lifecycle(content: str | None) -> str | None:
    field = _LIFECYCLE_FIELD_RE.search(content or "")
    comment = _LIFECYCLE_COMMENT_RE.search(content or "")
    value = field.group(1) if field else (comment.group(1) if comment else None)
    return value.strip().upper() if value else None


def _headings(content: str) -> set[str]:
    return {match.strip().casefold() for match in _HEADING_RE.findall(content or "")}


def content_signal(title: str, content: str) -> tuple[str, float, str] | None:
    """Tier-2: shape of the document body. Returns (class, confidence, rationale)."""
    headings = _headings(content)
    if _ADR_TITLE_RE.search(title or "") or len(_ADR_HEADINGS & headings) >= 3:
        return ("DECISION", 0.85, "ADR anatomy (ADR-n title or Context/Decision/Consequences headings)")
    if len(_RUNBOOK_HEADINGS & headings) >= 3:
        return ("OPERATION", 0.8, "runbook content-contract headings present")
    if len(_INVARIANT_RE.findall(content or "")) >= 2:
        return ("POLICY", 0.75, "invariants register entries (INV-n)")
    return None


def path_signal(path: str) -> tuple[str, float, str] | None:
    """Tier-3: section convention."""
    for prefix, proposed, confidence in PATH_PREFIX_MAP:
        if path.startswith(prefix):
            return (proposed, confidence, f"section convention {prefix!r}")
    return None


def propose(path: str, title: str, content: str) -> dict[str, Any] | None:
    """Strongest signal wins: legacy lifecycle > content shape > path prefix.

    Returns {proposed, confidence, signal, rationale} or None (no signal)."""
    legacy = legacy_lifecycle(content)
    if legacy is not None and legacy in LEGACY_LIFECYCLE_TO_CLASS:
        proposed = LEGACY_LIFECYCLE_TO_CLASS[legacy]
        return {
            "proposed": proposed,
            "confidence": 0.9,
            "signal": "legacy-lifecycle",
            "rationale": f"inline Lifecycle field says {legacy}",
        }
    shaped = content_signal(title, content)
    if shaped:
        proposed, confidence, rationale = shaped
        return {"proposed": proposed, "confidence": confidence, "signal": "content-shape", "rationale": rationale}
    pathed = path_signal(path)
    if pathed:
        proposed, confidence, rationale = pathed
        return {"proposed": proposed, "confidence": confidence, "signal": "path-prefix", "rationale": rationale}
    return None


def conflict_for(path: str, current: str, content: str) -> dict[str, Any] | None:
    """A stored class contradicted by a tier-1 legacy field deserves a human."""
    legacy = legacy_lifecycle(content)
    if legacy is None or legacy not in LEGACY_LIFECYCLE_TO_CLASS:
        return None
    implied = LEGACY_LIFECYCLE_TO_CLASS[legacy]
    if implied == current:
        return None
    return {
        "path": path,
        "current": current,
        "legacy_lifecycle": legacy,
        "implied": implied,
        "note": "stored knowledge_class disagrees with the page's inline Lifecycle field",
    }


def corpus_stamp(pages: list[dict[str, Any]]) -> str:
    """Lockstep with app.corpus_structure.corpus_fingerprint (path+revision)."""
    digest = hashlib.sha256()
    for page in sorted(pages, key=lambda item: str(item.get("path", ""))):
        digest.update(f"{page.get('path', '')}\t{page.get('revision', '')}\n".encode("utf-8"))
    return digest.hexdigest()[:16]


def build_report(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure: pages carry path/title/content/knowledge_class (+optional
    resource_id/revision). Deterministic, sorted, write-free."""
    suggestions: list[dict[str, Any]] = []
    no_signal: list[str] = []
    conflicts: list[dict[str, Any]] = []
    classified = 0
    for page in sorted(pages, key=lambda item: str(item.get("path", ""))):
        path = str(page.get("path", ""))
        current = (page.get("knowledge_class") or "").strip() or None
        content = str(page.get("content") or "")
        if current:
            classified += 1
            conflict = conflict_for(path, current, content)
            if conflict:
                conflicts.append(conflict)
            continue
        proposal = propose(path, str(page.get("title") or ""), content)
        if proposal is None:
            no_signal.append(path)
            continue
        entry = {"path": path, **proposal}
        if page.get("resource_id"):
            entry["resource_id"] = str(page["resource_id"])
        if page.get("metadata_version") is not None:
            # Bind the curated proposal to the trust-metadata snapshot. The
            # apply driver passes this to the existing optimistic-lock verb.
            entry["metadata_version"] = int(page["metadata_version"])
        suggestions.append(entry)
    return {
        "contract_version": CONTRACT_VERSION,
        "corpus_stamp": corpus_stamp(pages),
        "counts": {
            "pages": len(pages),
            "classified": classified,
            "missing": len(pages) - classified,
            "proposed": len(suggestions),
            "no_signal": len(no_signal),
            "conflicts": len(conflicts),
        },
        "suggestions": suggestions,
        "no_signal": sorted(no_signal),
        "conflicts": conflicts,
    }


def _pages_from_dir(directory: Path) -> list[dict[str, Any]]:
    pages = []
    for md_file in sorted(directory.rglob("*.md")):
        relative = md_file.relative_to(directory).as_posix()
        content = md_file.read_text(encoding="utf-8", errors="replace")
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        pages.append({
            "path": relative,
            "title": title_match.group(1).strip() if title_match else md_file.stem,
            "content": content,
            "knowledge_class": None,
        })
    return pages


def _pages_from_api(client: Client) -> list[dict[str, Any]]:
    listing = client.call("GET", "/api/v1/pages?status=active&limit=2000")
    pages = listing.get("pages", [])
    # Content is fetched only for pages that need a proposal or a conflict
    # check: the unclassified, plus the classified (for tier-1 disagreement).
    # The list endpoint strips content, so this is one detail call per page —
    # bounded by corpus size and read-only.
    for page in pages:
        detail = client.call("GET", f"/api/v1/pages/{page['resource_id']}")
        page["content"] = detail.get("content", "")
    return pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dir", type=Path, default=None,
                        help="offline mode: propose over a directory of markdown instead of the API")
    parser.add_argument("--out", type=Path, default=None,
                        help="write the JSON report here (default: stdout)")
    args = parser.parse_args(argv)

    if args.from_dir:
        pages = _pages_from_dir(args.from_dir)
    else:
        api = os.environ.get("DOCPLANE_API")
        token = os.environ.get("DOCPLANE_TOKEN")
        if not api or not token:
            print("set DOCPLANE_API and DOCPLANE_TOKEN (env only — never argv), or use --from-dir", file=sys.stderr)
            return 2
        try:
            pages = _pages_from_api(Client(api, token))
        except ApiError as error:
            print(str(error), file=sys.stderr)
            return 1

    report = build_report(pages)
    rendered = json.dumps(report, indent=2, sort_keys=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
        print(f"wrote {args.out} — {report['counts']}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
