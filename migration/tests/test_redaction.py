"""Unit tests for the generic redaction transform.

All fixtures are synthetic. Secret-SHAPED tokens are non-secret example shapes.
"""
from __future__ import annotations

import pytest

from migration.redaction import (
    APPROVED_PLACEHOLDERS,
    DEFAULT_RULES,
    DocumentRefusedError,
    MalformedMarkerError,
    REDACTION_MARKER_RE,
    assert_no_malformed_markers,
    braces_balanced,
    count_redaction_markers,
    find_malformed_markers,
    redact,
)

# Synthetic secret-shaped tokens (non-secret example shapes).
ACCESS_KEY_SHAPED = "AKIAABCDEFGHIJKLMNOP"
TOKEN_SHAPED = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"


# --------------------------------------------------------------------------
# Approved placeholders survive untouched.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "placeholder",
    ["{{password}}", "<VAR>", "$ENV", "$DATABASE_URL", "${SERVICE_ENDPOINT}", "changeme"],
)
def test_approved_placeholders_are_not_redacted(placeholder):
    source = f"config value is {placeholder} here"
    result = redact(source, label="example-0001")
    assert placeholder in result.sanitised
    assert result.marker_count == 0
    assert not result.changed


def test_declared_approved_placeholders_all_survive():
    for literal in APPROVED_PLACEHOLDERS:
        result = redact(f"x {literal} y", label="example-0001")
        assert literal in result.sanitised


# --------------------------------------------------------------------------
# Service names / credential-resembling identifiers are not rewritten.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "identifier",
    ["password-service", "token-broker", "api-key-rotation", "secret-store", "credential-cache"],
)
def test_service_names_resembling_credentials_are_preserved(identifier):
    source = f"The {identifier} handles routing."
    result = redact(source, label="example-0001")
    assert identifier in result.sanitised
    assert not result.changed


# --------------------------------------------------------------------------
# Secret-shaped tokens are redacted into well-formed markers.
# --------------------------------------------------------------------------
def test_secret_shaped_tokens_are_redacted():
    source = f"key {ACCESS_KEY_SHAPED} and token {TOKEN_SHAPED}"
    result = redact(source, label="example-0001")
    assert ACCESS_KEY_SHAPED not in result.sanitised
    assert TOKEN_SHAPED not in result.sanitised
    assert result.marker_count == 2
    assert result.changed
    for marker in REDACTION_MARKER_RE.findall(result.sanitised):
        assert REDACTION_MARKER_RE.fullmatch(marker)


# --------------------------------------------------------------------------
# Brace balance is preserved (regression for {<REDACTED:...>}} defect).
# --------------------------------------------------------------------------
def test_output_braces_are_balanced():
    source = f"before {{{{password}}}} {ACCESS_KEY_SHAPED} after"
    result = redact(source, label="example-0001")
    assert braces_balanced(result.sanitised)


def test_redaction_never_emits_a_marker_wrapped_in_braces():
    source = f"{{{{password}}}} {TOKEN_SHAPED}"
    result = redact(source, label="example-0001")
    # No emitted marker may be immediately wrapped by a brace on either side.
    assert "{<REDACTED" not in result.sanitised
    assert ">}" not in result.sanitised.replace("}}", "")


# --------------------------------------------------------------------------
# Malformed marker input is rejected, never produced.
# --------------------------------------------------------------------------
def test_malformed_marker_input_is_rejected():
    malformed = "text {<REDACTED:PASSWORD:example-0001>}} more"
    assert find_malformed_markers(malformed)
    with pytest.raises(MalformedMarkerError):
        assert_no_malformed_markers(malformed)
    with pytest.raises(MalformedMarkerError):
        redact(malformed, label="example-0001")


def test_wellformed_marker_input_is_accepted():
    good = "text <REDACTED:PASSWORD:example-0001> more"
    assert find_malformed_markers(good) == []
    # An already-well-formed marker is idempotent (not re-wrapped, not doubled).
    result = redact(good, label="example-0001")
    assert result.sanitised == good
    assert result.marker_count == 1


# --------------------------------------------------------------------------
# REVISED fenced-code policy: secret-shaped values inside fences ARE redacted,
# with a syntax-preserving replacement where it can be guaranteed, and a
# fail-closed refusal where it cannot. Approved placeholders inside a fence are
# still preserved. (See docs/architecture/REDACTION_INVARIANTS.md invariant 4.)
# --------------------------------------------------------------------------
def test_fenced_secret_in_safe_position_is_redacted():
    # A shaped token in a shell-assignment RHS is a syntax-safe position: the
    # bare marker replaces it without breaking the assignment.
    fence = f"```bash\nexport TOKEN={TOKEN_SHAPED}\necho done\n```"
    source = f"intro\n\n{fence}\n\noutro {TOKEN_SHAPED}"
    result = redact(source, label="example-0001")
    # Both the fenced and unfenced token are redacted now.
    assert TOKEN_SHAPED not in result.sanitised
    assert result.marker_count == 2
    # Replacement stays syntactically valid: assignment form is preserved.
    assert "export TOKEN=<REDACTED:TOKEN:example-0001>" in result.sanitised
    for marker in REDACTION_MARKER_RE.findall(result.sanitised):
        assert REDACTION_MARKER_RE.fullmatch(marker)


def test_fenced_approved_placeholder_survives():
    # An approved placeholder inside a fence must remain unchanged.
    fence = "```yaml\nauth:\n  password: {{password}}\n```"
    result = redact(fence, label="example-0001")
    assert "{{password}}" in result.sanitised
    assert result.marker_count == 0
    assert not result.changed


def test_fenced_unsafe_replacement_fails_closed():
    # A shaped token with no safe syntactic position (embedded in a URL, no
    # delimiter) cannot be replaced without risking broken output => the
    # document is REFUSED with a content-free remediation finding.
    fence = (
        "```bash\n"
        f"curl https://{TOKEN_SHAPED}@example.invalid/path\n"
        "```\n"
    )
    with pytest.raises(DocumentRefusedError) as exc:
        redact(fence, label="example-0001")
    findings = exc.value.findings
    assert findings, "refusal must carry at least one finding"
    for f in findings:
        assert f.reason == "unsafe-fence-replacement"
        assert f.marker_class == "TOKEN"
        # Content-free: the finding must NOT echo the secret bytes or the line.
        assert TOKEN_SHAPED not in repr(f)
        assert "example.invalid" not in repr(f)
    # The exception message itself must not leak the secret either.
    assert TOKEN_SHAPED not in str(exc.value)


# --------------------------------------------------------------------------
# Determinism.
# --------------------------------------------------------------------------
def test_transform_is_deterministic():
    source = f"a {ACCESS_KEY_SHAPED} b {{{{password}}}} c {TOKEN_SHAPED}"
    first = redact(source, rules=DEFAULT_RULES, label="example-0001")
    second = redact(source, rules=DEFAULT_RULES, label="example-0001")
    assert first.sanitised == second.sanitised
    assert first.sanitised_sha256 == second.sanitised_sha256
    assert first.rules_fingerprint == second.rules_fingerprint


# --------------------------------------------------------------------------
# Marker count / drift observability.
# --------------------------------------------------------------------------
def test_marker_count_matches_counter_and_is_stable():
    source = f"{ACCESS_KEY_SHAPED} {TOKEN_SHAPED}"
    result = redact(source, label="example-0001")
    assert result.marker_count == count_redaction_markers(result.sanitised)
    # Re-running the transform on already-sanitised output introduces no drift.
    again = redact(result.sanitised, label="example-0001")
    assert again.marker_count == result.marker_count


# --------------------------------------------------------------------------
# No invalid Markdown structure: headings / anchors stay stable.
# --------------------------------------------------------------------------
def test_headings_and_anchors_are_stable():
    source = (
        "# Title {#top}\n\n"
        "## Section {#sec-one}\n\n"
        f"Body with {ACCESS_KEY_SHAPED} token.\n"
    )
    result = redact(source, label="example-0001")
    assert "# Title {#top}" in result.sanitised
    assert "## Section {#sec-one}" in result.sanitised
    assert braces_balanced(result.sanitised)
