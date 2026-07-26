"""Lossless human/agent Markdown preview and diff service.

This router is read-only with respect to authored state. It renders candidate Markdown, checks exact
revision/section bindings and returns the ordinary change operation that a dashboard, MCP client or SDK
should submit through the existing proposal API.
"""
from __future__ import annotations

import difflib
import hashlib
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from markdown import Markdown

from app.agent_auth import Principal, require_scopes
from app.agent_sections import find_section, outline, sections
from app.authoring_models import (
    AuthoringDiagnostic,
    AuthoringPreviewRequest,
    AuthoringPreviewResponse,
)
from app.db import get_conn
from app.workspace_access import require_page_access


router = APIRouter(prefix="/api/v1/authoring", tags=["authoring-v1"])

_RENDER_EXTENSIONS = [
    "extra",
    "admonition",
    "attr_list",
    "toc",
    "md_in_html",
    "pymdownx.details",
    "pymdownx.superfences",
    "pymdownx.tabbed",
]
_EXPLICIT_ID_RE = re.compile(r"\{#([A-Za-z0-9_-]+)\}")
_FENCE_RE = re.compile(r"^[ \t]*(```+|~~~+)", re.MULTILINE)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_page(resource_id: str, principal: Principal) -> dict[str, Any]:
    with get_conn() as conn:
        page = require_page_access(conn, principal, resource_id, minimum_role="PROPOSER")
        cur = conn.cursor()
        cur.execute(
            "SELECT content, nav_path, status, updated_at, updated_by FROM docs.pages WHERE resource_id = %s",
            (resource_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "PAGE_NOT_FOUND"})
    return {
        **page,
        "content": row[0],
        "nav_path": row[1],
        "status": row[2],
        "updated_at": row[3],
        "updated_by": row[4],
    }


def _replace_section(document: str, heading_id: str, replacement: str) -> tuple[str, str]:
    section = find_section(document, heading_id)
    if section is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SECTION_NOT_FOUND", "heading_id": heading_id},
        )
    lines = document.splitlines(keepends=True)
    start = section.start_line - 1
    end = section.end_line
    normalized = replacement
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"
    candidate = "".join(lines[:start]) + normalized + "".join(lines[end:])
    return candidate, section.content_hash


def _diagnostics(content: str) -> list[AuthoringDiagnostic]:
    findings: list[AuthoringDiagnostic] = []
    explicit: dict[str, list[int]] = {}
    in_fence = False
    fence_char = ""
    fence_line = None

    for line_number, raw in enumerate(content.splitlines(), start=1):
        fence = _FENCE_RE.match(raw)
        if fence:
            marker = fence.group(1)[0]
            if not in_fence:
                in_fence = True
                fence_char = marker
                fence_line = line_number
            elif marker == fence_char:
                in_fence = False
                fence_char = ""
                fence_line = None
            continue
        if not in_fence:
            for match in _EXPLICIT_ID_RE.finditer(raw):
                explicit.setdefault(match.group(1), []).append(line_number)

    if in_fence:
        findings.append(
            AuthoringDiagnostic(
                severity="error",
                code="UNCLOSED_CODE_FENCE",
                message="A fenced code block is not closed.",
                line=fence_line,
            )
        )

    for heading_id, lines in sorted(explicit.items()):
        if len(lines) > 1:
            findings.append(
                AuthoringDiagnostic(
                    severity="error",
                    code="DUPLICATE_EXPLICIT_ID",
                    message=f"Explicit identifier '{heading_id}' appears more than once.",
                    line=lines[0],
                    details={"heading_id": heading_id, "lines": lines},
                )
            )

    parsed = sections(content)
    implicit = [item for item in parsed if not item.explicit_id]
    if implicit:
        findings.append(
            AuthoringDiagnostic(
                severity="warning",
                code="IMPLICIT_SECTION_IDS",
                message="Precise future edits require explicit heading identifiers.",
                details={"headings": [item.heading_id for item in implicit[:50]]},
            )
        )

    if content.startswith("---\n") and "\n---\n" not in content[4:]:
        findings.append(
            AuthoringDiagnostic(
                severity="error",
                code="UNCLOSED_FRONT_MATTER",
                message="Front matter starts with '---' but has no closing delimiter.",
                line=1,
            )
        )
    return findings


def _direct_section_map(content: str) -> dict[str, dict[str, Any]]:
    """Hash each heading's own block, excluding nested child sections.

    The general bounded-read parser intentionally includes descendants in a parent section. That is the
    correct retrieval contract, but it makes a semantic diff noisy: editing one H2 would also mark its H1
    parent as changed. Authoring diffs therefore compare each heading only through the next heading.
    """
    parsed = sorted(sections(content), key=lambda item: item.start_line)
    lines = content.splitlines(keepends=True)
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(parsed):
        start = item.start_line - 1
        end = parsed[index + 1].start_line - 1 if index + 1 < len(parsed) else len(lines)
        direct_content = "".join(lines[start:end])
        result[item.heading_id] = {
            "title": item.title,
            "content_hash": _sha(direct_content),
            "explicit_id": item.explicit_id,
        }
    return result


def _semantic_diff(base: str, candidate: str) -> list[dict[str, Any]]:
    before = _direct_section_map(base)
    after = _direct_section_map(candidate)
    order = list(dict.fromkeys([*before.keys(), *after.keys()]))
    result: list[dict[str, Any]] = []
    for heading_id in order:
        old = before.get(heading_id)
        new = after.get(heading_id)
        if old is None:
            state = "added"
        elif new is None:
            state = "removed"
        elif old["content_hash"] != new["content_hash"]:
            state = "changed"
        else:
            state = "unchanged"
        result.append(
            {
                "heading_id": heading_id,
                "state": state,
                "title_before": old["title"] if old else None,
                "title_after": new["title"] if new else None,
                "hash_before": old["content_hash"] if old else None,
                "hash_after": new["content_hash"] if new else None,
                "explicit_id_before": old["explicit_id"] if old else None,
                "explicit_id_after": new["explicit_id"] if new else None,
            }
        )
    return result


def _raw_diff(base: str, candidate: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            base.splitlines(keepends=True),
            candidate.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )


def _render(content: str) -> str:
    renderer = Markdown(
        extensions=_RENDER_EXTENSIONS,
        extension_configs={
            "toc": {"permalink": False},
            "pymdownx.tabbed": {"alternate_style": True},
        },
        output_format="html5",
    )
    return renderer.convert(content)


@router.post("/preview", response_model=AuthoringPreviewResponse)
def preview_markdown(
    request: AuthoringPreviewRequest,
    principal: Principal = Depends(require_scopes("docs:propose")),
) -> AuthoringPreviewResponse:
    page: dict[str, Any] | None = None
    base = ""
    candidate = request.content
    expected_section_hash: str | None = None
    path = "new-document.md"

    if request.page_resource_id is not None:
        page = _load_page(str(request.page_resource_id), principal)
        if page["revision"] != request.expected_revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "PAGE_REVISION_STALE",
                    "expected": request.expected_revision,
                    "current": page["revision"],
                },
            )
        base = page["content"]
        path = page["path"]
        if request.scope == "section":
            candidate, expected_section_hash = _replace_section(
                base,
                request.heading_id or "",
                request.content,
            )

    diagnostics = _diagnostics(candidate)
    operation: dict[str, Any]
    if request.scope == "section":
        operation = {
            "operation_type": "REPLACE_SECTION",
            "page_resource_id": str(request.page_resource_id),
            "expected_revision": request.expected_revision,
            "expected_section_hash": expected_section_hash,
            "payload": {
                "heading_id": request.heading_id,
                "content": request.content,
            },
        }
    else:
        operation = {
            "operation_type": "REPLACE_DOCUMENT",
            "page_resource_id": str(request.page_resource_id) if request.page_resource_id else None,
            "expected_revision": request.expected_revision,
            "expected_section_hash": None,
            "payload": {"content": request.content},
        }

    return AuthoringPreviewResponse(
        source_content=request.content,
        candidate_content=candidate,
        source_hash=_sha(request.content),
        candidate_hash=_sha(candidate),
        base_revision=page["revision"] if page else None,
        page_resource_id=request.page_resource_id,
        path=page["path"] if page else None,
        title=page["title"] if page else None,
        workspace_key=page["workspace_key"] if page else None,
        scope=request.scope,
        heading_id=request.heading_id,
        rendered_html=_render(candidate),
        raw_diff=_raw_diff(base, candidate, path),
        semantic_diff=_semantic_diff(base, candidate),
        outline=outline(candidate),
        diagnostics=diagnostics,
        operation=operation,
        source_fidelity={
            "authoritative_mode": "markdown-source",
            "input_returned_byte_for_byte": True,
            "source_length": len(request.content.encode("utf-8")),
            "candidate_length": len(candidate.encode("utf-8")),
            "generated_release_mutated": False,
            "database_mutated": False,
        },
    )
