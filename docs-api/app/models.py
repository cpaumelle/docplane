import re

from pydantic import BaseModel, field_validator, model_validator
from datetime import datetime
from typing import Optional


def _validate_nav_segments(value: str, *, field_name: str, allow_single_segment: bool) -> None:
    """Shared segment-level checks for nav_paths and nav prefixes.
    Raises ValueError with a fix-it hint for any rule breach."""
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if value.startswith("/") or value.endswith("/"):
        raise ValueError(f"{field_name} must not start or end with '/'")
    parts = value.split("/")
    if not allow_single_segment and len(parts) < 2:
        raise ValueError(f"{field_name} must contain at least one '/' (e.g., 'Section/Title')")
    for part in parts:
        if not part:
            raise ValueError(f"{field_name} must not contain empty segments (no '//')")
        if part != part.strip():
            raise ValueError(
                f"{field_name} segment {part!r} has leading or trailing whitespace — "
                f"use {part.strip()!r} instead"
            )
        if "  " in part:
            raise ValueError(
                f"{field_name} segment {part!r} contains consecutive spaces — "
                f"collapse to a single space"
            )


class PageIn(BaseModel):
    title: str
    nav_path: str
    content: str
    updated_by: str = "agent"

    @field_validator("content")
    @classmethod
    def content_starts_with_heading(cls, v: str) -> str:
        if not v.strip().startswith("#"):
            raise ValueError("Page content must begin with a Markdown H1 heading")
        return v

    @field_validator("nav_path")
    @classmethod
    def nav_path_valid(cls, v: str) -> str:
        if v == "Home":
            return v  # root singleton — no slash required
        _validate_nav_segments(v, field_name="nav_path", allow_single_segment=False)
        return v


class PageRow(BaseModel):
    path: str
    title: str
    nav_path: str
    content: str
    revision: str
    version: int
    status: str
    updated_at: datetime
    updated_by: str


class PageSummary(BaseModel):
    path: str
    title: str
    nav_path: str
    version: int
    status: str
    updated_at: datetime
    updated_by: str


class DeployResult(BaseModel):
    deploy_id: str
    pages_written: int
    files_removed: int
    dry_run: bool
    build_time_ms: Optional[int] = None
    nav: Optional[list] = None  # Populated when dry_run=True — preview of rendered nav tree


class NavConflict(BaseModel):
    kind: str              # "leaf-vs-section" | "case-collision"
    nav_path: str
    segment: str
    detail: str
    paths: list[str]       # offending source paths


class NavView(BaseModel):
    tree: list                       # mkdocs nav-shaped: [{"Home": "index.md"}, {"Section": [...]}, ...]
    section_paths: list[str]         # flat list of distinct intermediate sections, e.g. "Services/Spacen"
    top_level_sections: list[str]
    conflicts: list[NavConflict]


class NavMoveRequest(BaseModel):
    """Atomic bulk-rename of a nav_path prefix across all matching active pages.

    Matches every page where nav_path equals from_prefix OR starts with
    `from_prefix + '/'`, then substitutes the prefix. Runs as a single DB
    transaction with a full-tree simulation — aborts if the result would
    conflict."""
    from_prefix: str
    to_prefix: str
    updated_by: str = "agent"

    @field_validator("from_prefix", "to_prefix")
    @classmethod
    def prefix_valid(cls, v: str, info) -> str:
        # "Home" is a root singleton, not a movable prefix
        if v == "Home":
            raise ValueError(f"{info.field_name} cannot be 'Home' — Home is a root singleton, not a prefix")
        _validate_nav_segments(v, field_name=info.field_name, allow_single_segment=True)
        return v

    @model_validator(mode="after")
    def prefixes_are_distinct(self):
        if self.from_prefix == self.to_prefix:
            raise ValueError("from_prefix and to_prefix are identical — nothing to move")
        if self.to_prefix.startswith(self.from_prefix + "/"):
            raise ValueError(
                f"to_prefix {self.to_prefix!r} is nested inside from_prefix "
                f"{self.from_prefix!r} — this would cause infinite self-nesting"
            )
        return self


class NavMovePage(BaseModel):
    path: str
    old_nav_path: str
    new_nav_path: str
    revision: str
    version: int


class NavMoveResult(BaseModel):
    moved: int
    from_prefix: str
    to_prefix: str
    pages: list[NavMovePage]


class SectionInfo(BaseModel):
    name: str
    sort_order: int
    page_count: int          # active pages whose top-level section equals this name


class SectionOrderView(BaseModel):
    sections: list[SectionInfo]           # known sections in configured order
    unordered_sections: list[str]         # top-level sections in use but not in docs.sections


class SectionOrderIn(BaseModel):
    """Replace the entire section ordering. sort_order is assigned by list index."""
    order: list[str]
    updated_by: str = "agent"

    @field_validator("order")
    @classmethod
    def order_valid(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("order must contain at least one section name")
        seen: set[str] = set()
        for name in v:
            if not name:
                raise ValueError("section name must not be empty")
            if name == "Home":
                raise ValueError("'Home' is a root singleton, not an orderable section")
            if "/" in name:
                raise ValueError(
                    f"section name {name!r} must not contain '/' — top-level names only"
                )
            if name != name.strip():
                raise ValueError(
                    f"section name {name!r} has leading or trailing whitespace"
                )
            if "  " in name:
                raise ValueError(
                    f"section name {name!r} contains consecutive spaces"
                )
            if name in seen:
                raise ValueError(f"duplicate section name {name!r} in order list")
            seen.add(name)
        return v


class VersionSummary(BaseModel):
    revision: str
    version: Optional[int]
    updated_by: Optional[str]
    archived_at: datetime


class VersionDetail(BaseModel):
    path: str
    title: Optional[str]
    nav_path: Optional[str]
    content: str
    revision: str
    version: Optional[int]
    updated_by: Optional[str]
    archived_at: datetime


_PATH_RE = re.compile(r'^[a-z0-9/_-]+\.md$')


class PageMoveRequest(BaseModel):
    """Atomically relocate a page to a new canonical path (URL-identity operation).

    Differs from nav/reparent: this changes the resource identity (URL path),
    not just the sidebar presentation. Use nav/reparent to reorganise the sidebar
    without changing URLs; use this to rename or relocate a page canonically.
    """
    from_path: str
    to_path: str
    new_nav_path: Optional[str] = None
    create_redirect: bool = True
    updated_by: str = "agent"

    @field_validator("from_path", "to_path")
    @classmethod
    def path_format(cls, v: str) -> str:
        if not _PATH_RE.match(v):
            raise ValueError(f"path must match ^[a-z0-9/_-]+\\.md$ — got {v!r}")
        return v

    @model_validator(mode="after")
    def paths_differ(self):
        if self.from_path == self.to_path:
            raise ValueError("from_path and to_path must be different")
        return self


class PageMoveResult(BaseModel):
    from_path: str
    to_path: str
    nav_path: str
    revision: str
    version: int
    redirect_added: bool
    repair_required: bool = False
    recommended_action: Optional[str] = None


class AddRedirectRequest(BaseModel):
    """Idempotent repair: ensure a redirect_maps entry exists from from_path to the destination.

    Use this to repair a pages/move that committed in DB but failed to write the redirect
    (redirect_added=false in the move response). Safe to retry: no-op if redirect already present.
    """
    from_path: str

    @field_validator("from_path")
    @classmethod
    def path_format(cls, v: str) -> str:
        if not _PATH_RE.match(v):
            raise ValueError(f"path must match ^[a-z0-9/_-]+\\.md$ — got {v!r}")
        return v


class AddRedirectResult(BaseModel):
    success: bool
    redirect_added: bool   # True = newly written; False = already present (idempotent)
    from_path: str
    to_path: str


class RedirectOrphan(BaseModel):
    from_path: str
    to_path: str
    reason: str   # "destination_archived" | "destination_missing" | "source_still_active"


class RedirectReconcileResult(BaseModel):
    healthy: bool
    orphans: list[RedirectOrphan]
    summary: dict
