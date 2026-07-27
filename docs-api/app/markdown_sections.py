"""Bounded Markdown retrieval helpers for contributor reads and edits.

The parser is deliberately conservative. Explicit heading IDs are preserved;
headings without explicit IDs receive deterministic GitHub-style slugs for
retrieval only. Mutation operations require explicit IDs because display text
is not a durable edit address.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


_HEADING_RE = re.compile(
    r"^(?P<marks>#{1,6})[ \t]+(?P<title>.*?)(?:[ \t]+\{#(?P<explicit>[a-zA-Z0-9_-]+)\})?[ \t]*$"
)
_CODE_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")
_NON_SLUG_RE = re.compile(r"[^a-z0-9 _-]+")


@dataclass(frozen=True)
class Section:
    heading_id: str
    title: str
    level: int
    start_line: int
    end_line: int
    content: str
    content_hash: str
    explicit_id: bool


def _slug(title: str) -> str:
    value = title.lower().strip()
    value = re.sub(r"[`*_~]", "", value)
    value = _NON_SLUG_RE.sub("", value)
    value = re.sub(r"[ _]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "section"


def sections(content: str) -> list[Section]:
    lines = content.splitlines(keepends=True)
    headings: list[tuple[int, int, str, str, bool]] = []
    in_fence = False
    fence_marker = ""
    slug_counts: dict[str, int] = {}

    for index, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        fence = _CODE_FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)[0]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group("marks"))
        title = match.group("title").strip()
        explicit = match.group("explicit")
        base = explicit or _slug(title)
        count = slug_counts.get(base, 0)
        slug_counts[base] = count + 1
        heading_id = base if count == 0 else f"{base}-{count}"
        headings.append((index, level, title, heading_id, explicit is not None))

    result: list[Section] = []
    for position, (start, level, title, heading_id, explicit) in enumerate(headings):
        end = len(lines)
        for candidate in headings[position + 1 :]:
            if candidate[1] <= level:
                end = candidate[0]
                break
        section_content = "".join(lines[start:end])
        result.append(
            Section(
                heading_id=heading_id,
                title=title,
                level=level,
                start_line=start + 1,
                end_line=end,
                content=section_content,
                content_hash=hashlib.sha256(section_content.encode("utf-8")).hexdigest(),
                explicit_id=explicit,
            )
        )
    return result


def outline(content: str) -> list[dict]:
    return [
        {
            "heading_id": section.heading_id,
            "title": section.title,
            "level": section.level,
            "start_line": section.start_line,
            "end_line": section.end_line,
            "content_hash": section.content_hash,
            "explicit_id": section.explicit_id,
        }
        for section in sections(content)
    ]


def find_section(content: str, heading_id: str) -> Section | None:
    return next((item for item in sections(content) if item.heading_id == heading_id), None)


def summary(content: str, *, limit: int = 800) -> str:
    lines = content.splitlines()
    paragraph: list[str] = []
    in_fence = False
    heading_seen = False
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not heading_seen and stripped.startswith("# "):
            heading_seen = True
            continue
        if not heading_seen:
            continue
        if stripped.startswith("<!--") or stripped.startswith("**Lifecycle:**"):
            continue
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith("#") and paragraph:
            break
        paragraph.append(stripped)
    value = " ".join(paragraph).strip()
    if len(value) > limit:
        return value[: limit - 1].rstrip() + "…"
    return value
