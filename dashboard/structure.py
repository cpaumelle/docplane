from __future__ import annotations

import collections
import hashlib
import posixpath
import re
from typing import Any, Iterable


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


def build(pages: list[dict[str, Any]]) -> dict[str, Any]:
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
        },
        "sections": sections,
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
                "title": page.get("title"),
                "nav_path": page.get("nav_path"),
                "status": page.get("status"),
                "revision": page.get("revision"),
                "version": page.get("version"),
                "lifecycle": page.get("lifecycle")
                if page.get("status") == "active"
                else "ARCHIVED",
            }
            for page in sorted(normalized, key=lambda item: item["path"])
        ],
        "interpretation": (
            "Structural review signals only. Counts and naming patterns do not establish "
            "documentation quality or justify mutation without human review."
        ),
    }
