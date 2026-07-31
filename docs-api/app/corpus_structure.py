"""Structural corpus review helpers.

This module reports counts and naming patterns without inferring document
quality or authorising mutation from structural signals alone.
"""
from __future__ import annotations

import collections
import hashlib
import posixpath
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.maintenance_policy import review_flags

GENERATOR_VERSION = "1.0.0"
VALID_LIFECYCLES = {
    "REFERENCE",
    "OPERATION",
    "ACTIVE",
    "PAUSED",
    "BACKLOG",
    "ARCHIVED",
}
_FIELD_RE = re.compile(r"\*\*Lifecycle:\*\*\s*([A-Za-z_-]+)", re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--\s*lifecycle:\s*([A-Za-z_-]+)\s*-->", re.IGNORECASE)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_STEM_RE = re.compile(r"^[a-z0-9]+", re.IGNORECASE)
DIRECT_PAGE_THRESHOLD = 10


def lifecycle_of(content: str | None) -> str | None:
    field = _FIELD_RE.search(content or "")
    comment = _COMMENT_RE.search(content or "")
    value = field.group(1) if field else (comment.group(1) if comment else None)
    return value.strip().upper() if value else None


def corpus_fingerprint(pages: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for page in sorted(pages, key=lambda item: str(item.get("path", ""))):
        digest.update(
            f"{page.get('path', '')}\t{page.get('revision', '')}\n".encode("utf-8")
        )
    return digest.hexdigest()[:16]


def _section(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "(root)"


def _directory(path: str) -> str:
    return posixpath.dirname(path) or "(root)"


def _depth(path: str) -> int:
    return path.count("/") + 1


def build(
    pages: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a structural review model without interpreting document meaning."""
    normalized = [dict(page) for page in pages if page.get("path")]
    active = sorted(
        (page for page in normalized if page.get("status") == "active"),
        key=lambda page: page["path"],
    )
    archived = sorted(
        (page for page in normalized if page.get("status") == "archived"),
        key=lambda page: page["path"],
    )

    for page in active:
        page["lifecycle"] = lifecycle_of(page.get("content"))

    section_rows: dict[str, dict[str, Any]] = {}
    for page in active:
        name = _section(page["path"])
        row = section_rows.setdefault(
            name,
            {
                "name": name,
                "direct": 0,
                "subtree": 0,
                "subdirectories": set(),
                "max_depth": 0,
                "lifecycle": collections.Counter(),
                "knowledge_class": collections.Counter(),
                "archived": 0,
            },
        )
        row["subtree"] += 1
        row["max_depth"] = max(row["max_depth"], _depth(page["path"]))
        if _depth(page["path"]) <= 2:
            row["direct"] += 1
        elif "/" in page["path"]:
            row["subdirectories"].add(page["path"].split("/")[1])
        row["lifecycle"][page["lifecycle"] or "(missing)"] += 1
        row["knowledge_class"][page.get("knowledge_class") or "(missing)"] += 1

    for page in archived:
        name = _section(page["path"])
        row = section_rows.setdefault(
            name,
            {
                "name": name,
                "direct": 0,
                "subtree": 0,
                "subdirectories": set(),
                "max_depth": 0,
                "lifecycle": collections.Counter(),
                "knowledge_class": collections.Counter(),
                "archived": 0,
            },
        )
        row["archived"] += 1

    sections = []
    for row in section_rows.values():
        subtree = row["subtree"]
        sections.append(
            {
                "name": row["name"],
                "direct_pages": row["direct"],
                "subtree_pages": subtree,
                "concentration": round(row["direct"] / subtree, 4) if subtree else 0.0,
                "subdirectories": sorted(row["subdirectories"]),
                "max_depth": row["max_depth"],
                "lifecycle": dict(sorted(row["lifecycle"].items())),
                "knowledge_class": dict(sorted(row["knowledge_class"].items())),
                "archived_pages": row["archived"],
            }
        )
    sections.sort(key=lambda row: (-row["subtree_pages"], row["name"]))

    titles: dict[str, list[str]] = collections.defaultdict(list)
    directories: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for page in active:
        title = str(page.get("title") or "").strip()
        if title:
            titles[title].append(page["path"])
        directories[_directory(page["path"])].append(page)
        parts = page["path"].split("/")[:-1]
        for index in range(1, len(parts)):
            directories.setdefault("/".join(parts[:index]), [])
    for page in archived:
        parts = page["path"].split("/")[:-1]
        for index in range(1, len(parts) + 1):
            directories.setdefault("/".join(parts[:index]) or "(root)", [])

    now = now or datetime.now(timezone.utc)
    due_cutoff = now + timedelta(days=30)
    directory_rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for directory, members in directories.items():
        descendants = [
            page for page in active
            if directory == "(root)" or page["path"].startswith(directory + "/")
        ]
        archived_descendants = [
            page for page in archived
            if directory == "(root)" or page["path"].startswith(directory + "/")
        ]
        lifecycle = collections.Counter(
            page.get("lifecycle") or "(missing)" for page in descendants
        )
        knowledge = collections.Counter(
            page.get("knowledge_class") or "(missing)" for page in descendants
        )
        stems: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        for page in members:
            match = _STEM_RE.match(posixpath.basename(page["path"]))
            if match:
                stems[match.group(0).lower()].append(page)
        repeated_stems = {
            stem: values for stem, values in stems.items() if len(values) >= 3
        }
        duplicate_here = {
            title: values
            for title, values in titles.items()
            if len(values) > 1 and any(_directory(path) == directory for path in values)
        }
        flags = collections.Counter(
            flag
            for page in descendants
            for flag in review_flags(page, due_cutoff)
        )
        row = {
            "path": directory,
            "direct_pages": len(members),
            "descendant_pages": len(descendants) - len(members),
            "active_pages": len(descendants),
            "archived_pages": len(archived_descendants),
            "max_depth": max((_depth(page["path"]) for page in descendants), default=0),
            "size_bytes": sum(len((page.get("content") or "").encode("utf-8")) for page in descendants),
            "knowledge_class": dict(sorted(knowledge.items())),
            "legacy_lifecycle_signal": dict(sorted(lifecycle.items())),
            "maintenance": dict(sorted(flags.items())),
            "protected": False,
            "protection_basis": [],
        }
        directory_rows.append(row)

        reasons: list[dict[str, Any]] = []
        if len(members) > DIRECT_PAGE_THRESHOLD:
            reasons.append({
                "code": "HIGH_DIRECT_PAGE_COUNT",
                "measured": len(members),
                "threshold": DIRECT_PAGE_THRESHOLD,
                "explanation": (
                    f"{directory} contains {len(members)} direct pages; "
                    f"the review threshold is more than {DIRECT_PAGE_THRESHOLD}."
                ),
            })
        if duplicate_here:
            affected = sorted({
                path for values in duplicate_here.values() for path in values
            })
            reasons.append({
                "code": "DUPLICATE_TITLE",
                "measured": len(duplicate_here),
                "threshold": 1,
                "explanation": (
                    f"{len(duplicate_here)} title value(s) occur on multiple pages "
                    f"including pages directly in {directory}."
                ),
                "affected_paths": affected,
            })
        if repeated_stems:
            affected = sorted({
                page["path"] for values in repeated_stems.values() for page in values
            })
            reasons.append({
                "code": "REPEATED_FILENAME_STEM",
                "measured": len(repeated_stems),
                "threshold": 3,
                "explanation": (
                    f"{len(repeated_stems)} filename stem family/families contain "
                    "at least three direct pages."
                ),
                "affected_paths": affected,
            })
        if flags["metadata_review"]:
            reasons.append({
                "code": "MISSING_CLASSIFICATION",
                "measured": flags["metadata_review"],
                "threshold": 1,
                "explanation": (
                    f"{flags['metadata_review']} page(s) are in the authoritative "
                    "metadata-review queue."
                ),
            })
        overdue = flags["verification_due"] + flags["expired"] + flags["outdated"]
        if overdue:
            reasons.append({
                "code": "REVIEW_OVERDUE",
                "measured": overdue,
                "threshold": 1,
                "explanation": (
                    f"{overdue} page(s) are due, expired, or outdated under the "
                    "shared maintenance policy."
                ),
            })
        if not descendants and archived_descendants:
            reasons.append({
                "code": "ARCHIVED_ONLY_SECTION",
                "measured": len(archived_descendants),
                "threshold": 1,
                "explanation": (
                    f"{directory} contains {len(archived_descendants)} archived page(s) "
                    "and no active pages."
                ),
            })
        if reasons:
            affected_pages = sorted(
                descendants or archived_descendants, key=lambda page: page["path"]
            )
            score = sum(int(reason["measured"]) for reason in reasons)
            candidates.append({
                "candidate_id": hashlib.sha256(
                    f"{corpus_fingerprint(normalized)}:{directory}".encode()
                ).hexdigest()[:16],
                "path": directory,
                "score": score,
                "severity": "HIGH" if score >= 20 else ("MEDIUM" if score >= 8 else "LOW"),
                "confidence": "DETERMINISTIC",
                "protected": row["protected"],
                "reasons": reasons,
                "evidence_count": len(affected_pages),
                "resources": [
                    {
                        "resource_id": page.get("resource_id"),
                        "path": page["path"],
                        "revision": page.get("revision"),
                    }
                    for page in affected_pages
                ],
            })

    directory_rows.sort(key=lambda row: row["path"])
    candidates.sort(key=lambda row: (-row["score"], row["path"]))

    duplicate_titles = [
        {"title": title, "paths": sorted(paths)}
        for title, paths in sorted(titles.items())
        if len(paths) > 1
    ]
    missing_lifecycle = sorted(
        page["path"] for page in active if page["lifecycle"] is None
    )
    unknown_lifecycle = sorted(
        page["path"]
        for page in active
        if page["lifecycle"] is not None and page["lifecycle"] not in VALID_LIFECYCLES
    )
    missing_index = sorted(
        directory
        for directory, members in directories.items()
        if directory != "(root)"
        and len(members) >= 3
        and not any(posixpath.basename(page["path"]) == "index.md" for page in members)
    )
    dated_paths = sorted(page["path"] for page in active if _DATE_RE.search(page["path"]))

    return {
        "contract_version": "1",
        "generator_version": GENERATOR_VERSION,
        "fingerprint": corpus_fingerprint(normalized),
        "summary": {
            "active_pages": len(active),
            "archived_pages": len(archived),
            "sections": len(sections),
            "directories": len(directories),
            "classification": {
                "classified": sum(1 for page in active if page.get("knowledge_class")),
                "missing": sum(1 for page in active if not page.get("knowledge_class")),
                "missing_by_section": {
                    row["name"]: row["knowledge_class"].get("(missing)", 0)
                    for row in sections
                    if row["knowledge_class"].get("(missing)", 0)
                },
            },
        },
        "facets": {
            "knowledge_class": sorted({
                str(page["knowledge_class"]) for page in normalized
                if page.get("knowledge_class")
            }),
            "identifier_family": sorted({
                match.group(0).lower()
                for page in normalized
                if (match := _STEM_RE.match(posixpath.basename(page["path"])))
            }),
            "status": sorted({str(page.get("status")) for page in normalized}),
        },
        "sections": sections,
        "directories": directory_rows,
        "review_candidates": candidates,
        "review_contract": {
            "reason_codes": sorted({
                reason["code"] for candidate in candidates for reason in candidate["reasons"]
            }),
            "maintenance_policy": "shared-maintenance-policy-v1",
            "protected_surfaces": [],
            "exclusions": [],
            "legacy_lifecycle_canonical": False,
        },
        "signals": {
            "duplicate_titles": duplicate_titles,
            "missing_lifecycle": missing_lifecycle,
            "unknown_lifecycle": unknown_lifecycle,
            "missing_index": missing_index,
            "dated_paths": dated_paths,
        },
        "pages": [
            {
                "path": page["path"],
                "resource_id": page.get("resource_id"),
                "title": page.get("title"),
                "nav_path": page.get("nav_path"),
                "status": page.get("status"),
                "revision": page.get("revision"),
                "version": page.get("version"),
                "workspace_key": page.get("workspace_key"),
                "publication_state": page.get("publication_state"),
                "knowledge_class": page.get("knowledge_class"),
                "verification_state": page.get("verification_state"),
                "criticality": page.get("criticality"),
                "provenance": page.get("provenance"),
                "review_due_at": page.get("review_due_at"),
                "metadata_review_required": page.get("metadata_review_required"),
                "updated_at": page.get("updated_at"),
                "identifier_family": (
                    match.group(0).lower()
                    if (match := _STEM_RE.match(posixpath.basename(page["path"])))
                    else None
                ),
                "lifecycle": page.get("lifecycle")
                if page.get("status") == "active"
                else "ARCHIVED",
            }
            for page in sorted(normalized, key=lambda item: item["path"])
        ],
        "interpretation": (
            "Structural review signals only. Counts and naming patterns do not establish "
            "documentation quality or authorise mutation without contributor judgement."
        ),
    }
