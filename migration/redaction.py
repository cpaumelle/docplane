"""Generic redaction / sanitisation transform for document import.

This module is the single, deterministic target for the Phase-4 redaction
regression suite. It exists so the invariants that govern *rewriting content
during import* are executable and cannot silently regress.

Two historical defects motivate this module. They are described here in
GENERIC terms so the regression tests can prove they can never recur:

  (a) Revision-identity retention
      Content was rewritten during redaction but the surrounding importer
      kept the *source* revision identity. A rewrite changes bytes, so it
      MUST mint a fresh revision. See ``migration.importer`` for the
      importer-level invariant; this module exposes the byte-level fact
      (``RedactionResult.changed``) the importer relies on.

  (b) Brace imbalance / false-positive redaction
      A placeholder ``{{password}}`` was mis-rewritten into an unbalanced
      ``{<REDACTED:PASSWORD:...>}}`` marker, and ordinary placeholders and
      service-like identifiers were false-positive redacted. This module
      never touches approved placeholders, always emits balanced braces,
      and always emits well-formed ``<REDACTED:CLASS:LABEL>`` markers.

Design constraints (all asserted by the test suite):

  * Deterministic: identical input + identical rules => identical output and
    identical ``sanitised_sha256``.
  * Placeholder-safe: approved placeholders are never redacted.
  * Structure-safe: braces stay balanced; fenced code blocks are left
    byte-for-byte intact; Markdown headings/anchors are not rewritten.
  * Marker-well-formed: every emitted marker matches ``REDACTION_MARKER_RE``.
  * Drift-observable: the caller can compare marker counts before/after via
    ``count_redaction_markers`` to detect silent marker drift.

The redaction targets here are SYNTHETIC secret-shaped tokens only (e.g.
``AKIA...`` access-key-shaped strings, ``ghp_...`` token-shaped strings).
No real secret, host, path, or identifier is embedded anywhere.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Well-formed redaction marker grammar
#
# A redaction marker is exactly ``<REDACTED:CLASS:LABEL>`` where CLASS and
# LABEL are drawn from a restricted alphabet. The grammar is deliberately
# strict so that a malformed marker such as ``{<REDACTED:PASSWORD:...>}}``
# (the historical brace-imbalance defect) is detectable and rejectable
# rather than silently accepted.
# --------------------------------------------------------------------------
_MARKER_BODY = r"REDACTED:[A-Z][A-Z0-9_]*:[A-Za-z0-9][A-Za-z0-9._-]*"
REDACTION_MARKER_RE = re.compile(r"<" + _MARKER_BODY + r">")

# A permissive detector for any occurrence of the ``REDACTED`` keyword,
# together with the brackets/braces immediately surrounding it. Used to
# surface malformed markers (missing bracket, extra brace, truncated body)
# that the strict grammar would not match. Greedy on the trailing side so an
# extra ``}`` (the historical brace-imbalance defect) is captured as part of
# the candidate rather than left dangling.
REDACTION_TOKEN_RE = re.compile(r"[<{]*\s*REDACTED[^<\n]*?[>}]+", re.IGNORECASE)


# --------------------------------------------------------------------------
# Approved placeholders — these must survive redaction untouched.
#
# These are legitimate documentation placeholders and env-substitution
# tokens. Redacting them is the false-positive defect (b). The set is
# intentionally explicit and pattern-based rather than a blanket "anything
# with the word password" rule.
# --------------------------------------------------------------------------
APPROVED_PLACEHOLDERS: tuple[str, ...] = (
    "{{password}}",
    "{{ password }}",
    "<VAR>",
    "changeme",
)

# ``$ENV`` style environment references: ``$FOO``, ``${FOO}``. These are
# substitution placeholders, never literal secrets, and must be preserved.
_ENV_PLACEHOLDER_RE = re.compile(r"\$\{?[A-Z_][A-Z0-9_]*\}?")

# ``{{ mustache }}`` template placeholders. Preserved verbatim.
_MUSTACHE_RE = re.compile(r"\{\{\s*[a-zA-Z0-9_.\- ]+\s*\}\}")

# ``<angle>`` documentation placeholders such as ``<VAR>`` / ``<your-token>``.
_ANGLE_PLACEHOLDER_RE = re.compile(r"<[a-zA-Z][a-zA-Z0-9_-]*>")


# --------------------------------------------------------------------------
# Secret-shaped detectors — SYNTHETIC shapes only.
#
# Each rule pairs a detector regex with a marker CLASS. Rules never match the
# approved placeholders above because those are masked out before detection
# runs (see ``_mask_protected``).
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RedactionRule:
    marker_class: str  # goes into <REDACTED:CLASS:...>; UPPER_SNAKE
    pattern: re.Pattern[str]


def _default_rules() -> tuple[RedactionRule, ...]:
    return (
        # AWS-style access-key-shaped token (synthetic shape).
        RedactionRule("ACCESS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
        # GitHub-style personal-access-token-shaped string (synthetic shape).
        RedactionRule("TOKEN", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
        # Bearer-header-shaped secret (synthetic shape).
        RedactionRule("BEARER", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b")),
        # Hex secret of 40+ chars (synthetic shape).
        RedactionRule("HEX_SECRET", re.compile(r"\b[0-9a-f]{40,}\b")),
    )


DEFAULT_RULES: tuple[RedactionRule, ...] = _default_rules()


# --------------------------------------------------------------------------
# Result type
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RedactionResult:
    source: str
    sanitised: str
    marker_count: int
    rules_fingerprint: str
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        """True iff redaction altered the bytes of the document.

        The importer uses this to decide whether a NEW revision identity must
        be minted. Retaining the source revision when this is True is defect
        (a) and is forbidden.
        """
        return self.source != self.sanitised

    @property
    def sanitised_sha256(self) -> str:
        return hashlib.sha256(self.sanitised.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Fenced code-block handling
#
# Fenced code blocks (```lang ... ```) must survive byte-for-byte so that
# executable examples stay syntactically intact. We split the document into
# code and non-code spans and only run detection on the non-code spans.
# --------------------------------------------------------------------------
_FENCE_RE = re.compile(r"(^```.*?^```)", re.DOTALL | re.MULTILINE)


def _split_fences(text: str) -> list[tuple[bool, str]]:
    """Return spans as (is_code, text). Code spans are left untouched."""
    spans: list[tuple[bool, str]] = []
    last = 0
    for match in _FENCE_RE.finditer(text):
        if match.start() > last:
            spans.append((False, text[last:match.start()]))
        spans.append((True, match.group(0)))
        last = match.end()
    if last < len(text):
        spans.append((False, text[last:]))
    if not spans:
        spans.append((False, text))
    return spans


# --------------------------------------------------------------------------
# Protecting approved placeholders during detection
#
# Before running secret detectors we replace every approved placeholder with
# an opaque sentinel that cannot match any detector, then restore it after.
# This guarantees placeholders are never redacted (defect (b)).
# --------------------------------------------------------------------------
_SENTINEL = "\x00PH{}\x00"


def _mask_protected(text: str) -> tuple[str, list[str]]:
    protected: list[str] = []

    def _store(value: str) -> str:
        token = _SENTINEL.format(len(protected))
        protected.append(value)
        return token

    # Order matters: literal approved strings first, then structural patterns.
    for literal in APPROVED_PLACEHOLDERS:
        if literal in text:
            text = text.replace(literal, _store(literal))
    for pattern in (_MUSTACHE_RE, _ENV_PLACEHOLDER_RE, _ANGLE_PLACEHOLDER_RE):
        text = pattern.sub(lambda m: _store(m.group(0)), text)
    return text, protected


def _unmask_protected(text: str, protected: list[str]) -> str:
    for index, value in enumerate(protected):
        text = text.replace(_SENTINEL.format(index), value)
    return text


# --------------------------------------------------------------------------
# Rules fingerprint — part of determinism guarantee.
# --------------------------------------------------------------------------
def rules_fingerprint(rules: tuple[RedactionRule, ...]) -> str:
    payload = "|".join(f"{r.marker_class}={r.pattern.pattern}" for r in rules)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def count_redaction_markers(text: str) -> int:
    """Count well-formed ``<REDACTED:CLASS:LABEL>`` markers in ``text``."""
    return len(REDACTION_MARKER_RE.findall(text))


# --------------------------------------------------------------------------
# Marker well-formedness
# --------------------------------------------------------------------------
class MalformedMarkerError(ValueError):
    """Raised when text contains a malformed ``<REDACTED:...>`` marker."""


def find_malformed_markers(text: str) -> list[str]:
    """Return substrings that look like redaction markers but are malformed.

    This catches the historical brace-imbalance case
    ``{<REDACTED:PASSWORD:...>}}`` as well as truncated / bracket-missing
    variants. A substring is malformed if the permissive detector matches it
    but the strict grammar does not match it exactly.
    """
    findings: list[str] = []
    for token in REDACTION_TOKEN_RE.findall(text):
        candidate = token.strip()
        if not candidate:
            continue
        strict = REDACTION_MARKER_RE.fullmatch(candidate)
        if strict is None:
            findings.append(candidate)
    return findings


def assert_no_malformed_markers(text: str) -> None:
    malformed = find_malformed_markers(text)
    if malformed:
        raise MalformedMarkerError(
            "malformed redaction marker(s): " + " | ".join(malformed)
        )


# --------------------------------------------------------------------------
# Brace balance
# --------------------------------------------------------------------------
def braces_balanced(text: str) -> bool:
    """True iff ``{`` and ``}`` are balanced and never close before opening."""
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


# --------------------------------------------------------------------------
# The transform
# --------------------------------------------------------------------------
def redact(
    source: str,
    *,
    rules: tuple[RedactionRule, ...] = DEFAULT_RULES,
    label: str = "DOC",
) -> RedactionResult:
    """Sanitise ``source`` deterministically.

    ``label`` is the LABEL component of emitted markers; it is a synthetic,
    non-secret identifier (e.g. an example document id like ``example-0001``).
    """
    # Reject malformed markers that already exist in the source rather than
    # carrying them forward silently. This is the "reject, never produce"
    # half of the brace-imbalance guarantee.
    assert_no_malformed_markers(source)

    fp = rules_fingerprint(rules)
    findings: list[str] = []
    out_parts: list[str] = []

    for is_code, span in _split_fences(source):
        if is_code:
            # Executable examples are preserved byte-for-byte.
            out_parts.append(span)
            continue

        masked, protected = _mask_protected(span)
        for rule in rules:
            def _replace(match: re.Match[str], marker_class=rule.marker_class) -> str:
                findings.append(marker_class)
                return f"<REDACTED:{marker_class}:{label}>"

            masked = rule.pattern.sub(_replace, masked)
        out_parts.append(_unmask_protected(masked, protected))

    sanitised = "".join(out_parts)

    # Post-conditions the transform guarantees. Violating any of these is a
    # bug in this module, not in the caller — fail loudly.
    assert braces_balanced(sanitised), "redaction produced unbalanced braces"
    assert_no_malformed_markers(sanitised)

    return RedactionResult(
        source=source,
        sanitised=sanitised,
        marker_count=count_redaction_markers(sanitised),
        rules_fingerprint=fp,
        findings=tuple(findings),
    )
