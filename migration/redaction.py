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
  * Placeholder-safe: approved placeholders are never redacted (inside or
    outside code fences).
  * Structure-safe: braces stay balanced; Markdown headings/anchors are not
    rewritten.
  * Marker-well-formed: every emitted marker matches ``REDACTION_MARKER_RE``.
  * Drift-observable: the caller can compare marker counts before/after via
    ``count_redaction_markers`` to detect silent marker drift.

Fenced-code policy (REVISED — see ``docs/architecture/REDACTION_INVARIANTS.md``):

  The earlier "fenced code preserved byte-for-byte" rule was wrong for
  production: it let a confirmed-secret-shaped value survive purely because it
  sat inside a ``` fence. The revised policy is:

    * Approved placeholders / synthetic examples inside fences stay unchanged.
    * Real / confirmed-secret-shaped values inside fences ARE redacted.
    * The replacement preserves valid shell / YAML / JSON / config syntax
      wherever it can be guaranteed (the marker is emitted in a
      syntax-appropriate position, e.g. as a quoted scalar value).
    * When a safe syntactic replacement CANNOT be guaranteed, the importer
      REFUSES the document (``DocumentRefusedError``) and emits a content-free
      remediation finding. No silent pass, no broken output.
    * No importer may preserve a confirmed credential just because it is inside
      a fence.

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
# REVISED policy (see module docstring + REDACTION_INVARIANTS.md): fenced code
# is NO LONGER preserved byte-for-byte. Approved placeholders / synthetic
# examples inside a fence survive, but a confirmed-secret-shaped value inside a
# fence IS redacted — with a replacement that keeps the surrounding shell /
# YAML / JSON / config line syntactically valid where that can be guaranteed,
# and a hard REFUSAL (fail closed) where it cannot.
#
# We split the document into code and non-code spans; both are now processed,
# but code spans go through the syntax-aware fenced path (``_redact_fenced``).
# --------------------------------------------------------------------------
_FENCE_RE = re.compile(r"(^```.*?^```)", re.DOTALL | re.MULTILINE)


def _split_fences(text: str) -> list[tuple[bool, str]]:
    """Return spans as (is_code, text)."""
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
# Fail-closed refusal for fenced content
#
# When a confirmed-secret-shaped value sits inside a code fence and we cannot
# GUARANTEE a syntax-preserving replacement, we must not emit broken output and
# must not silently pass the secret through. Instead we REFUSE the document and
# surface a *content-free* remediation finding: it names the marker class and
# the fence language, but never echoes the secret bytes or the offending line.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RemediationFinding:
    """Content-free description of why a document was refused.

    Deliberately carries NO secret bytes, no offending line, no surrounding
    context — only the machine-actionable facts an operator needs to remediate:
    the reason code, the marker class that triggered it, and the fence language.
    """

    reason: str          # stable machine code, e.g. "unsafe-fence-replacement"
    marker_class: str    # the CLASS that would have been emitted, e.g. "TOKEN"
    fence_language: str   # fence info-string language, or "" if none


class DocumentRefusedError(ValueError):
    """Raised when a document cannot be safely sanitised and must be refused.

    Carries the content-free ``findings`` so the importer can record a
    remediation task without ever persisting the offending content.
    """

    def __init__(self, findings: tuple[RemediationFinding, ...]):
        self.findings = findings
        classes = ", ".join(sorted({f.marker_class for f in findings}))
        super().__init__(
            f"document refused: unsafe fenced redaction for class(es) [{classes}] "
            f"({len(findings)} finding(s))"
        )


# Info-string on the opening fence line: ```lang -> "lang" (lower-cased).
_FENCE_INFO_RE = re.compile(r"^```[ \t]*([A-Za-z0-9_.+-]*)", re.MULTILINE)

# Syntactically SAFE positions for a bare ``<REDACTED:...>`` marker inside a
# fence. A match is safe to replace iff its immediate surroundings match one of
# these shapes, because the marker then lands in a position that stays valid:
#
#   * shell / env assignment RHS, bare or double-quoted:
#         export FOO=<token>            FOO="<token>"
#   * YAML / config ``key: value`` scalar, bare or quoted:
#         key: <token>                  key: "<token>"
#   * JSON string value (already quoted):
#         "key": "<token>"
#
# In every safe shape the token is a self-contained word terminated by
# whitespace, a quote, a comma, or end-of-line — so swapping it for a bare
# marker cannot break the surrounding delimiters. Anything else is unsafe.
_SAFE_BEFORE_RE = re.compile(r"""(?x)
    (?:
        [=:]\s*      |   # assignment / mapping value position
        ["']             # opening quote (quoted scalar / JSON string)
    )
    $
""")
_SAFE_AFTER_RE = re.compile(r"""(?x)
    ^(?:
        \s   |           # whitespace terminates a bare word
        ["']  |          # closing quote
        ,     |          # JSON / flow-seq separator
        $                # end of line
    )
""")


def _fence_language(fence: str) -> str:
    match = _FENCE_INFO_RE.match(fence)
    return (match.group(1) if match else "").lower()


def _replacement_is_safe(span: str, start: int, end: int) -> bool:
    """True iff the secret at ``span[start:end]`` sits in a syntax-safe spot.

    Conservative by design: unknown context is treated as UNSAFE so the
    document is refused rather than mangled.
    """
    before = span[:start]
    after = span[end:]
    before_ok = bool(_SAFE_BEFORE_RE.search(before)) or before.endswith((" ", "\t"))
    after_ok = bool(_SAFE_AFTER_RE.match(after)) or after == ""
    return before_ok and after_ok


def _redact_fenced(
    fence: str,
    *,
    rules: tuple[RedactionRule, ...],
    label: str,
    findings: list[str],
    refusals: list[RemediationFinding],
) -> str:
    """Redact secret-shaped values inside a code fence, fail-closed on unsafe.

    Approved placeholders inside the fence are masked out first and always
    survive. Each remaining secret-shaped match is replaced with a bare marker
    IF the replacement is syntax-safe; otherwise a content-free refusal finding
    is recorded and the (caller-visible) refusal mechanism fails the document.
    """
    language = _fence_language(fence)
    masked, protected = _mask_protected(fence)

    for rule in rules:
        def _replace(match: re.Match[str], marker_class=rule.marker_class) -> str:
            if not _replacement_is_safe(masked, match.start(), match.end()):
                # Fail closed: record a content-free finding and leave the
                # ORIGINAL token in place for now. The presence of any refusal
                # is turned into a DocumentRefusedError by ``redact`` before the
                # (still-secret-bearing) output can ever be returned.
                refusals.append(
                    RemediationFinding(
                        reason="unsafe-fence-replacement",
                        marker_class=marker_class,
                        fence_language=language,
                    )
                )
                return match.group(0)
            findings.append(marker_class)
            return f"<REDACTED:{marker_class}:{label}>"

        masked = rule.pattern.sub(_replace, masked)

    return _unmask_protected(masked, protected)


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
    refusals: list[RemediationFinding] = []
    out_parts: list[str] = []

    for is_code, span in _split_fences(source):
        if is_code:
            # REVISED policy: secret-shaped values inside fences ARE redacted,
            # fail-closed when a safe syntactic replacement can't be guaranteed.
            out_parts.append(
                _redact_fenced(
                    span,
                    rules=rules,
                    label=label,
                    findings=findings,
                    refusals=refusals,
                )
            )
            continue

        masked, protected = _mask_protected(span)
        for rule in rules:
            def _replace(match: re.Match[str], marker_class=rule.marker_class) -> str:
                findings.append(marker_class)
                return f"<REDACTED:{marker_class}:{label}>"

            masked = rule.pattern.sub(_replace, masked)
        out_parts.append(_unmask_protected(masked, protected))

    # Fail closed BEFORE building any returnable output: if any fenced secret
    # could not be safely replaced, refuse the whole document. The refusal
    # findings are content-free (no secret bytes, no offending line).
    if refusals:
        raise DocumentRefusedError(tuple(refusals))

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
