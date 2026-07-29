"""Parity contract tests: two transforms can never silently diverge.

Encodes the policy from issue #60: ``migration.redaction`` is the CANONICAL
sanitisation transform (the single entrypoint). These tests prove five parity
properties are stable on the canonical path and that any second importer path
(an adapter) matches canonical exactly. They also prove the harness has teeth:
a genuinely divergent re-implementation is CAUGHT.

All fixtures are synthetic. Secret-SHAPED tokens are non-secret example shapes.
"""
from __future__ import annotations

import re

import pytest

from migration.parity import (
    ParityFingerprint,
    assert_parity,
    canonical_transform,
    fingerprint_transform,
)
from migration.redaction import DEFAULT_RULES, redact

# The synthetic fixture corpus, with the importer identity each would carry.
# ``expect`` is the outcome the canonical path must produce.
_CASES = [
    # (label, resource_id, source_revision, source_text, expect_outcome)
    (
        "clean",
        "example-0001",
        "rev-a",
        "clean body with {{password}} and <VAR> only",
        "ok",
    ),
    (
        "unfenced-secret",
        "example-0002",
        "rev-b",
        "key AKIAABCDEFGHIJKLMNOP in body",
        "ok",
    ),
    (
        "fenced-safe-secret",
        "example-0003",
        "rev-c",
        "```bash\nexport TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789\n```\n",
        "ok",
    ),
    (
        "fenced-approved-placeholder",
        "example-0004",
        "rev-d",
        "```yaml\nauth:\n  password: {{password}}\n```\n",
        "ok",
    ),
    (
        "fenced-unsafe-refuse",
        "example-0005",
        "rev-e",
        "```bash\ncurl https://ghp_abcdefghijklmnopqrstuvwxyz0123456789@x.invalid/p\n```\n",
        "refused",
    ),
    (
        "malformed-marker",
        "example-0006",
        "rev-f",
        "text {<REDACTED:PASSWORD:example-0006>}} more",
        "malformed",
    ),
]


def _fp(source, *, label, resource_id, source_revision, transform=canonical_transform):
    return fingerprint_transform(
        source,
        label=label,
        resource_id=resource_id,
        source_revision=source_revision,
        transform=transform,
    )


@pytest.mark.parametrize("label,rid,rev,text,expect", _CASES)
def test_canonical_outcome_matches_expectation(label, rid, rev, text, expect):
    fp = _fp(text, label=label, resource_id=rid, source_revision=rev)
    assert fp.outcome == expect


# --------------------------------------------------------------------------
# Property stability: the canonical path is identical across replays for every
# one of the five parity properties.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("label,rid,rev,text,expect", _CASES)
def test_canonical_five_properties_are_stable_across_replay(label, rid, rev, text, expect):
    first = _fp(text, label=label, resource_id=rid, source_revision=rev)
    second = _fp(text, label=label, resource_id=rid, source_revision=rev)
    # 1 sanitised bytes, 2 marker count, 3 transformed hash,
    # 4 revision-minting decision, 5 refusal outcome — all in the fingerprint.
    assert first == second
    assert first.sanitised == second.sanitised
    assert first.marker_count == second.marker_count
    assert first.sanitised_sha256 == second.sanitised_sha256
    assert (first.changed, first.minted_revision) == (second.changed, second.minted_revision)
    assert first.outcome == second.outcome
    assert first.refusal_reasons == second.refusal_reasons


# --------------------------------------------------------------------------
# A second importer path (a thin ADAPTER that delegates to canonical) must
# produce an identical fingerprint on every fixture — the core anti-divergence
# guarantee.
# --------------------------------------------------------------------------
def _delegating_adapter(source: str, label: str):
    """A conformant second importer path: it delegates to canonical."""
    return canonical_transform(source, label)


@pytest.mark.parametrize("label,rid,rev,text,expect", _CASES)
def test_delegating_adapter_matches_canonical(label, rid, rev, text, expect):
    canonical = _fp(text, label=label, resource_id=rid, source_revision=rev)
    adapter = _fp(
        text, label=label, resource_id=rid, source_revision=rev,
        transform=_delegating_adapter,
    )
    # Never raises: the adapter matches canonical on all five properties.
    assert_parity(canonical, adapter)
    assert canonical == adapter


# --------------------------------------------------------------------------
# Teeth: a genuinely divergent re-implementation MUST be caught by the harness.
# This proves the parity contract would fail CI if a second transform drifted.
# --------------------------------------------------------------------------
_ROGUE_MARKER = "<REDACTED:TOKEN:rogue>"


def _rogue_transform(source: str, label: str):
    """A non-conformant re-implementation that redacts differently.

    It replaces the token shape with a DIFFERENT marker label, so its sanitised
    bytes (and hash, and minted revision) diverge from canonical.
    """
    class _Result:
        def __init__(self, sanitised: str):
            import hashlib
            self.source = source
            self.sanitised = sanitised
            self.marker_count = sanitised.count("<REDACTED:")
            self.sanitised_sha256 = hashlib.sha256(
                sanitised.encode("utf-8")
            ).hexdigest()

    rogue = re.sub(r"ghp_[A-Za-z0-9]{36}", _ROGUE_MARKER, source)
    rogue = re.sub(r"AKIA[0-9A-Z]{16}", _ROGUE_MARKER, rogue)
    return _Result(rogue)


def test_divergent_reimplementation_is_caught():
    # Use a fixture where the rogue transform actually differs from canonical.
    label, rid, rev = "unfenced-secret", "example-0002", "rev-b"
    text = "key AKIAABCDEFGHIJKLMNOP in body"
    canonical = _fp(text, label=label, resource_id=rid, source_revision=rev)
    rogue = _fp(
        text, label=label, resource_id=rid, source_revision=rev,
        transform=_rogue_transform,
    )
    assert canonical != rogue
    with pytest.raises(AssertionError):
        assert_parity(canonical, rogue)


# --------------------------------------------------------------------------
# The canonical entrypoint IS migration.redaction.redact (single source of
# truth), not a re-implementation.
# --------------------------------------------------------------------------
def test_canonical_entrypoint_delegates_to_redact():
    text = "key AKIAABCDEFGHIJKLMNOP in body"
    via_canonical = canonical_transform(text, "example-0002")
    via_redact = redact(text, rules=DEFAULT_RULES, label="example-0002")
    assert via_canonical.sanitised == via_redact.sanitised
    assert via_canonical.sanitised_sha256 == via_redact.sanitised_sha256
    assert via_canonical.marker_count == via_redact.marker_count


def test_fingerprint_is_content_safe_on_refusal():
    # On refusal, the fingerprint carries only reason codes — no secret bytes.
    label, rid, rev = "fenced-unsafe-refuse", "example-0005", "rev-e"
    text = "```bash\ncurl https://ghp_abcdefghijklmnopqrstuvwxyz0123456789@x.invalid/p\n```\n"
    fp = _fp(text, label=label, resource_id=rid, source_revision=rev)
    assert fp.outcome == "refused"
    assert fp.sanitised is None
    assert fp.refusal_reasons == ("unsafe-fence-replacement",)
    assert "ghp_" not in repr(fp)
    assert "x.invalid" not in repr(fp)
