"""Markdown link discovery, resolution and bounded link-only rewriting.

Corpus reorganisation moves pages between paths. Every move invalidates the
references that point at the old path, and repairing them is the step most likely
to cause collateral damage: a careless rewrite edits prose, a careless parser
misses references, and both failures look like success.

This module is the deterministic, executable form of the rules that survived
several real migrations. Four historical defects motivate it. They are stated
generically so the regression suite can prove they cannot recur:

  (a) Line-oriented parsing under-reports references.
      A Markdown link may span a line break, with its label on one line and its
      destination on the next. A per-line parser silently skips such links,
      leaving a stale reference behind while every check still reports success.
      All matching here runs over the whole document.

  (b) Rewrites can change more than they claim.
      ``mask_links`` replaces every link DESTINATION with a constant. If the
      masked before and after are byte-identical, nothing outside link
      destinations changed - prose, headings, anchors, labels and whitespace are
      all intact. ``plan_rewrites`` refuses to emit a body that fails this, so
      the property is proven per document rather than asserted once.

  (c) Navigation labels derived from titles corrupt the tree.
      A title is prose and may contain the navigation separator, which silently
      invents an extra navigation level and discards a curated label.
      ``nav_leaf`` and ``retarget_nav`` take the leaf from the existing
      navigation path and refuse to accept a separator inside a leaf.

  (d) Expected paths hardcoded to one migration's destination.
      A verifier that looks for a literal directory passes for the cohort it was
      written against and false-fails every other. ``expected_redirect_hop``
      derives the expected relative hop from source and target depth.

Not every reference should be rewritten. Fenced examples, quoted material and
evidence surfaces record a path deliberately, and rewriting them falsifies the
record. Those are PRESERVED, and preservation is explicit and classifiable
(:class:`Preservation`) rather than a silent skip, so a later corpus scan can
reconcile exactly against what a rewrite declared.

The module is pure: no I/O, no clock, no network, no global state. Callers supply
a corpus mapping of ``path -> markdown``. Ordering of every returned collection is
deterministic.
"""
from __future__ import annotations

import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "AmbiguousIdentifierError",
    "LinkRef",
    "MASK",
    "ParserUncertaintyError",
    "Preservation",
    "PreservationReason",
    "RewritePlan",
    "Rewrite",
    "blocking_page_for_section",
    "expected_redirect_hop",
    "find_duplicate_routes",
    "find_page_section_conflicts",
    "find_links",
    "mask_links",
    "nav_leaf",
    "nav_path_from_leaf",
    "page_url",
    "plan_rewrites",
    "plan_source_move",
    "redirect_is_representable",
    "protected_lines",
    "resolve",
    "resolve_identifier_prefixes",
    "retarget",
    "retarget_nav",
    "scan_stale_references",
]

#: Link destination captured non-greedily, optional bracketed title, DOTALL so a
#: link may span lines. A destination may contain neither whitespace nor ')'.
LINK = re.compile(r"\[([^\]]*)\]\(\s*([^)\s]+?)\s*(?:\s+[\"'][^\"']*[\"'])?\s*\)", re.S)

#: Constant substituted for every link destination by :func:`mask_links`.
MASK = "<DOCPLANE-LINK-DESTINATION>"

NAV_SEPARATOR = "/"

_FENCE = re.compile(r"^\s*(```|~~~)")

#: Path shapes that record references deliberately and must not be rewritten.
EVIDENCE_SURFACE = re.compile(
    r"(^|/)(evidence|receipts?|registers?|history|changelog|incidents?|archive|superseded)(/|-|\.md$)"
    r"|(^|/)[^/]*-(register|receipt|history|baseline|inventory|evidence)\.md$",
    re.I,
)


class ParserUncertaintyError(RuntimeError):
    """The document cannot be parsed with confidence; callers must fail closed.

    Raised rather than guessing, because a wrong guess about document structure
    silently changes which references are considered protected.
    """


class AmbiguousIdentifierError(RuntimeError):
    """An identifier prefix does not uniquely determine one resource."""


class PreservationReason(str, Enum):
    """Why a reference was deliberately not rewritten."""

    FENCED_CODE = "fenced_code"
    BLOCKQUOTE = "blockquote"
    EVIDENCE_SURFACE = "evidence_surface"


@dataclass(frozen=True)
class LinkRef:
    """One Markdown link occurrence."""

    page: str
    line: int  # 1-based, of the link's first line
    end_line: int  # 1-based, of the link's last line (differs when it spans lines)
    label: str
    target: str
    resolved: str | None  # corpus path, or None for external/non-Markdown links
    start: int = field(repr=False, default=0)
    end: int = field(repr=False, default=0)

    @property
    def spans_lines(self) -> bool:
        return self.end_line != self.line


@dataclass(frozen=True)
class Rewrite:
    page: str
    line: int
    old_target: str
    new_target: str
    resolved_old: str
    resolved_new: str


@dataclass(frozen=True)
class Preservation:
    page: str
    line: int
    target: str
    resolved: str
    reason: PreservationReason

    def key(self) -> tuple[str, int, str]:
        """Stable identity used to reconcile a rewrite against a later scan."""
        return (self.page, self.line, self.target)


@dataclass(frozen=True)
class RewritePlan:
    """Result of planning one document. ``content`` is None when nothing changed."""

    page: str
    original: str = field(repr=False, default="")
    content: str | None = field(repr=False, default=None)
    rewrites: tuple[Rewrite, ...] = ()
    preservations: tuple[Preservation, ...] = ()

    @property
    def changed(self) -> bool:
        return self.content is not None

    def as_dict(self) -> dict:
        """Deterministic, machine-readable form suitable for a receipt."""
        return {
            "page": self.page,
            "changed": self.changed,
            "rewrites": [
                {
                    "line": r.line,
                    "old_target": r.old_target,
                    "new_target": r.new_target,
                    "resolved_old": r.resolved_old,
                    "resolved_new": r.resolved_new,
                }
                for r in self.rewrites
            ],
            "preservations": [
                {
                    "line": p.line,
                    "target": p.target,
                    "resolved": p.resolved,
                    "reason": p.reason.value,
                }
                for p in self.preservations
            ],
        }


# --------------------------------------------------------------------------
# masking - the link-only proof
# --------------------------------------------------------------------------

def mask_links(text: str) -> str:
    """``text`` with every link destination replaced by :data:`MASK`.

    Two documents with equal masks differ only in link destinations.
    """
    return LINK.sub(lambda m: "[%s](%s)" % (m.group(1), MASK), text)


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------

def protected_lines(text: str) -> dict[int, PreservationReason]:
    """0-based line numbers inside fenced code or blockquotes, with the reason.

    Raises :class:`ParserUncertaintyError` on an unterminated fence: the rest of
    the document could be code or prose, and the answer changes which references
    are protected.
    """
    out: dict[int, PreservationReason] = {}
    fence_marker: str | None = None
    fence_line = 0
    for index, line in enumerate(text.splitlines()):
        match = _FENCE.match(line)
        if match:
            if fence_marker is None:
                fence_marker, fence_line = match.group(1), index
            elif line.strip().startswith(fence_marker):
                fence_marker = None
            out[index] = PreservationReason.FENCED_CODE
            continue
        if fence_marker is not None:
            out[index] = PreservationReason.FENCED_CODE
        elif line.lstrip().startswith(">"):
            out[index] = PreservationReason.BLOCKQUOTE
    if fence_marker is not None:
        raise ParserUncertaintyError(
            "unterminated %r code fence opened at line %d" % (fence_marker, fence_line + 1)
        )
    return out


def is_evidence_surface(page_path: str) -> bool:
    """True when the page records references deliberately (evidence, receipts…)."""
    return bool(EVIDENCE_SURFACE.search(page_path))


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

def split_target(target: str) -> tuple[str, str, bool]:
    """``(base, '#fragment' or '', root_absolute)``."""
    base, _, fragment = target.partition("#")
    return base, ("#" + fragment if fragment else ""), base.startswith("/")


def resolve(page_path: str, target: str) -> str | None:
    """Corpus path a link points at, or None for external/non-Markdown links."""
    base, _, root_absolute = split_target(target)
    if not base.endswith(".md"):
        return None
    if "://" in base:
        return None
    if root_absolute:
        return base[1:]
    return posixpath.normpath(posixpath.join(posixpath.dirname(page_path), base))


def retarget(page_path: str, target: str, new_path: str) -> str:
    """New destination preserving the original form and anchor fragment."""
    _, fragment, root_absolute = split_target(target)
    if root_absolute:
        return "/" + new_path + fragment
    return posixpath.relpath(new_path, posixpath.dirname(page_path)) + fragment


def find_links(page_path: str, text: str):
    """Yield every :class:`LinkRef` in ``text``, in document order."""
    for match in LINK.finditer(text):
        start_line = text.count("\n", 0, match.start())
        end_line = text.count("\n", 0, match.end())
        yield LinkRef(
            page=page_path,
            line=start_line + 1,
            end_line=end_line + 1,
            label=match.group(1),
            target=match.group(2),
            resolved=resolve(page_path, match.group(2)),
            start=match.start(2),
            end=match.end(2),
        )


# --------------------------------------------------------------------------
# navigation
# --------------------------------------------------------------------------

def nav_leaf(nav_path: str) -> str:
    """Last segment of a navigation path - the curated label for a page.

    Never derive this from a page title: titles are prose and may contain the
    navigation separator, which would invent an extra navigation level and
    discard the curated label.
    """
    parts = [part.strip() for part in str(nav_path or "").split(NAV_SEPARATOR) if part.strip()]
    if not parts:
        raise ValueError("cannot derive a navigation leaf from %r" % (nav_path,))
    return parts[-1]


def nav_path_from_leaf(section: str, leaf: str) -> str:
    """Join ``section`` and an explicit ``leaf``, rejecting a leaf with a separator.

    This is the only sanctioned way to build a navigation path from a value that
    did not already come from one. It exists to make a specific mistake
    impossible: passing a page TITLE as the leaf. Titles are prose and may
    contain the separator, which would silently create an extra navigation level
    and discard the curated label.
    """
    leaf = str(leaf).strip()
    if not leaf:
        raise ValueError("navigation leaf must be non-empty")
    if NAV_SEPARATOR in leaf:
        raise ValueError(
            "navigation leaf %r contains %r; a leaf must name exactly one level "
            "(page titles are not valid leaves)" % (leaf, NAV_SEPARATOR)
        )
    section = section.strip(NAV_SEPARATOR).strip()
    if not section:
        raise ValueError("section must be a non-empty navigation path")
    return "%s%s%s" % (section, NAV_SEPARATOR, leaf)


def retarget_nav(nav_path: str, section: str) -> str:
    """Place a page's existing curated leaf under ``section``.

    The leaf is taken verbatim from ``nav_path``, never from a title, so a
    curated label survives a re-sectioning unchanged.
    """
    return nav_path_from_leaf(section, nav_leaf(nav_path))


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

def page_url(md_path: str) -> str:
    """Served URL for a Markdown path under directory URLs."""
    if md_path.endswith("/index.md"):
        return "/" + md_path[: -len("index.md")]
    if md_path == "index.md":
        return "/"
    return "/" + md_path[: -len(".md")] + "/"


def expected_redirect_hop(old_path: str, new_path: str) -> str:
    """Relative hop a compatibility route at ``old_path`` should point at.

    Derived from the depth of both paths. Never assert a literal destination
    directory: such a check passes for the migration it was written against and
    false-fails every other one.
    """
    old_url, new_url = page_url(old_path), page_url(new_path)
    return posixpath.relpath(new_url, old_url) + "/"


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------

def resolve_identifier_prefixes(identifiers, length: int = 8) -> dict[str, str]:
    """Map each identifier's ``length``-character prefix to the full identifier.

    Raises :class:`AmbiguousIdentifierError` if any prefix is shared. Matching on
    a truncated identifier is only sound while prefixes are unique, and that is a
    property of the data, not a guarantee - so it is proven, never assumed.
    """
    identifiers = list(identifiers)
    counts = Counter(identifier[:length] for identifier in identifiers)
    collisions = sorted(prefix for prefix, count in counts.items() if count > 1)
    if collisions:
        raise AmbiguousIdentifierError(
            "%d identifier prefix collision(s) at length %d: %s"
            % (len(collisions), length, ", ".join(collisions[:5]))
        )
    return {identifier[:length]: identifier for identifier in sorted(identifiers)}


# --------------------------------------------------------------------------
# planning and scanning
# --------------------------------------------------------------------------

def plan_rewrites(page_path: str, text: str, mapping: dict[str, str]) -> RewritePlan:
    """Plan link-only rewrites of ``mapping`` (old corpus path -> new corpus path).

    Returns a plan whose ``content`` is None when nothing changes. Raises
    :class:`RuntimeError` if the rewritten body would differ outside link
    destinations, and :class:`ParserUncertaintyError` if the document's structure
    cannot be determined.
    """
    protected = protected_lines(text)
    evidence = is_evidence_surface(page_path)
    edits: list[tuple[int, int, str]] = []
    rewrites: list[Rewrite] = []
    preservations: list[Preservation] = []

    for ref in find_links(page_path, text):
        if ref.resolved is None or ref.resolved not in mapping:
            continue
        reason = None
        for line in range(ref.line - 1, ref.end_line):
            if line in protected:
                reason = protected[line]
                break
        if reason is None and evidence:
            reason = PreservationReason.EVIDENCE_SURFACE
        if reason is not None:
            preservations.append(
                Preservation(page_path, ref.line, ref.target, ref.resolved, reason)
            )
            continue
        new_target = retarget(page_path, ref.target, mapping[ref.resolved])
        edits.append((ref.start, ref.end, new_target))
        rewrites.append(
            Rewrite(page_path, ref.line, ref.target, new_target, ref.resolved,
                    mapping[ref.resolved])
        )

    if not edits:
        return RewritePlan(page=page_path, original=text, content=None,
                           rewrites=(), preservations=tuple(preservations))

    content = text
    for start, end, replacement in reversed(edits):
        content = content[:start] + replacement + content[end:]
    if mask_links(content) != mask_links(text):
        raise RuntimeError(
            "rewrite of %s would alter more than link destinations" % page_path
        )
    return RewritePlan(page=page_path, original=text, content=content,
                       rewrites=tuple(rewrites), preservations=tuple(preservations))


def plan_corpus(corpus: dict[str, str], mapping: dict[str, str]) -> list[RewritePlan]:
    """Plan every page, in deterministic path order. Pages with no links are skipped."""
    plans = []
    for path in sorted(corpus):
        plan = plan_rewrites(path, corpus[path], mapping)
        if plan.changed or plan.preservations:
            plans.append(plan)
    return plans


def scan_stale_references(corpus: dict[str, str], old_paths):
    """Find references to ``old_paths`` still present, split by intent.

    Returns ``(unintended, preserved)``. A reference in a preserved context is not
    a defect when a compatibility route exists; the split lets a caller reconcile
    a scan against exactly what a rewrite declared it preserved.
    """
    mapping = {path: path for path in old_paths}
    unintended: list[LinkRef] = []
    preserved: list[Preservation] = []
    for path in sorted(corpus):
        text = corpus[path]
        protected = protected_lines(text)
        evidence = is_evidence_surface(path)
        for ref in find_links(path, text):
            if ref.resolved is None or ref.resolved not in mapping:
                continue
            reason = None
            for line in range(ref.line - 1, ref.end_line):
                if line in protected:
                    reason = protected[line]
                    break
            if reason is None and evidence:
                reason = PreservationReason.EVIDENCE_SURFACE
            if reason is None:
                unintended.append(ref)
            else:
                preserved.append(
                    Preservation(path, ref.line, ref.target, ref.resolved, reason)
                )
    return unintended, preserved


# --------------------------------------------------------------------------
# moving a page: its own outbound links
# --------------------------------------------------------------------------

def plan_source_move(old_page_path: str, new_page_path: str, text: str) -> RewritePlan:
    """Rewrite a page's OWN relative outbound links so they resolve unchanged.

    ``plan_rewrites`` handles the other direction: links whose *target* moved.
    This handles the page that *itself* moves. Moving ``a/b.md`` to
    ``a/b/index.md`` changes the directory every relative link resolves against,
    so ``[x](c.md)`` silently stops meaning ``a/c.md`` and starts meaning
    ``a/b/c.md``. The rendered URL can be identical while every relative link
    inside the page quietly breaks.

    Root-absolute links, external links and links in protected contexts are left
    alone; relative links that already resolve identically are left alone too, so
    a move that needs no change produces a zero-op plan and a byte-identical body.

    Returns a plan whose ``page`` is the NEW path.
    """
    if old_page_path == new_page_path:
        raise ValueError("source move must change the page path")
    protected = protected_lines(text)
    edits: list[tuple[int, int, str]] = []
    rewrites: list[Rewrite] = []
    preservations: list[Preservation] = []

    for ref in find_links(old_page_path, text):
        if ref.resolved is None:
            continue
        base, _, _fragment = split_target(ref.target)
        if base.startswith("/"):
            continue  # root-absolute: unaffected by depth
        reason = None
        for line in range(ref.line - 1, ref.end_line):
            if line in protected:
                reason = protected[line]
                break
        if reason is not None:
            preservations.append(
                Preservation(new_page_path, ref.line, ref.target, ref.resolved, reason)
            )
            continue
        if resolve(new_page_path, ref.target) == ref.resolved:
            continue  # resolution-equivalent at the new depth
        corrected = retarget(new_page_path, ref.target, ref.resolved)
        edits.append((ref.start, ref.end, corrected))
        rewrites.append(Rewrite(new_page_path, ref.line, ref.target, corrected,
                                ref.resolved, ref.resolved))

    if not edits:
        return RewritePlan(page=new_page_path, original=text, content=None,
                           rewrites=(), preservations=tuple(preservations))
    content = text
    for start, end, replacement in reversed(edits):
        content = content[:start] + replacement + content[end:]
    if mask_links(content) != mask_links(text):
        raise RuntimeError(
            "source-move rewrite of %s would alter more than link destinations" % old_page_path
        )
    return RewritePlan(page=new_page_path, original=text, content=content,
                       rewrites=tuple(rewrites), preservations=tuple(preservations))


# --------------------------------------------------------------------------
# navigation and route conflicts
# --------------------------------------------------------------------------

def find_page_section_conflicts(nav_paths: dict[str, str]) -> list[dict]:
    """Navigation paths used as BOTH a page and a section.

    ``nav_paths`` maps page path -> nav path. A navigation path cannot be both a
    leaf and a container: a page sitting at ``A/B`` prevents ``A/B/C`` from ever
    being created. Detecting this before planning turns a late validation failure
    into a scheduling fact - the occupying page has to move first.
    """
    sections: set[str] = set()
    for nav in nav_paths.values():
        parts = [part for part in str(nav or "").split(NAV_SEPARATOR) if part]
        for index in range(1, len(parts)):
            sections.add(NAV_SEPARATOR.join(parts[:index]))
    conflicts = []
    for nav in sorted(set(nav_paths.values())):
        if nav in sections:
            conflicts.append({
                "nav_path": nav,
                "pages": sorted(p for p, n in nav_paths.items() if n == nav),
                "children": sorted(p for p, n in nav_paths.items()
                                   if str(n or "").startswith(nav + NAV_SEPARATOR)),
            })
    return conflicts


def blocking_page_for_section(nav_paths: dict[str, str], section: str) -> str | None:
    """The page occupying ``section`` as a leaf, if any, else None."""
    for path, nav in sorted(nav_paths.items()):
        if nav == section:
            return path
    return None


def redirect_is_representable(from_path: str, to_path: str) -> bool:
    """False when a redirect between these paths cannot be rendered.

    ``a/b.md`` and ``a/b/index.md`` render to the same URL, so a compatibility
    route between them would be written exactly where the target renders -
    replacing the page with a stub that redirects to itself.
    """
    return from_path != to_path and page_url(from_path) != page_url(to_path)


def find_duplicate_routes(paths) -> list[dict]:
    """Source paths that render to the same URL - a split canonical identity."""
    seen: dict[str, list[str]] = {}
    for path in sorted(paths):
        seen.setdefault(page_url(path), []).append(path)
    return [{"url": url, "paths": group} for url, group in sorted(seen.items())
            if len(group) > 1]
