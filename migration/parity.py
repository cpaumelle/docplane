"""Reusable redaction parity harness.

Companion to issue #60: ``migration.redaction`` is the **canonical**
sanitisation transform — the single entrypoint every importer MUST call. This
module makes that policy *enforceable* rather than aspirational.

The harness reduces any importer path to a fixed ``ParityFingerprint`` of five
observable properties. Two importer paths that produce identical fingerprints
for the same fixture are, by construction, indistinguishable on everything that
matters for public safety. The parity test asserts the canonical path is stable
across replays and that any second path (an adapter) yields the *same*
fingerprint as canonical — so two transforms can never silently diverge.

The five parity properties:

  1. sanitised bytes            — identical sanitised output
  2. marker count               — identical number of well-formed markers
  3. transformed hash           — identical ``sha256`` of the sanitised bytes
  4. revision-minting decision  — identical (changed?, minted revision)
  5. malformed / refusal outcome — identical acceptance / refusal behaviour

All inputs here are synthetic; nothing embeds a real secret, host, path, or id.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from migration.importer import SourcePage, import_page
from migration.redaction import (
    DEFAULT_RULES,
    DocumentRefusedError,
    MalformedMarkerError,
    RedactionRule,
    redact,
)

# --------------------------------------------------------------------------
# THE canonical entrypoint.
#
# This is the single sanitisation transform. Any importer that redacts content
# MUST route through ``canonical_transform`` (or ``migration.redaction.redact``
# directly). A second importer path must be a thin ADAPTER that delegates here;
# it must not re-implement detection, marker emission, fence policy, or minting.
# --------------------------------------------------------------------------
CanonicalTransform = Callable[[str, str], object]


def canonical_transform(source: str, label: str):
    """The one true sanitisation entrypoint. Delegates to ``redact``."""
    return redact(source, rules=DEFAULT_RULES, label=label)


@dataclass(frozen=True)
class ParityFingerprint:
    """Content-free-ish fingerprint of an importer's behaviour on one input.

    ``sanitised`` and ``sanitised_sha256`` only ever hold *sanitised* output
    (secrets already removed) or are ``None`` on refusal, so this fingerprint
    is safe to compare and log.
    """

    outcome: str               # "ok" | "refused" | "malformed"
    sanitised: str | None      # sanitised bytes, or None if not accepted
    marker_count: int | None
    sanitised_sha256: str | None
    changed: bool | None       # revision-minting: did bytes change?
    minted_revision: str | None  # revision the importer would assign
    refusal_reasons: tuple[str, ...]  # content-free reason codes on refusal


def fingerprint_transform(
    source: str,
    *,
    label: str,
    resource_id: str,
    source_revision: str,
    transform: CanonicalTransform = canonical_transform,
    rules: tuple[RedactionRule, ...] = DEFAULT_RULES,
) -> ParityFingerprint:
    """Reduce an importer path to its five-property parity fingerprint.

    ``transform`` is the sanitisation entrypoint under test. The importer-level
    facts (changed?, minted revision) are derived from the same source via
    ``import_page`` so the fingerprint spans transform AND importer behaviour.
    """
    # Property 5a: malformed / refusal outcome of the transform itself.
    try:
        result = transform(source, label)
    except DocumentRefusedError as exc:
        return ParityFingerprint(
            outcome="refused",
            sanitised=None,
            marker_count=None,
            sanitised_sha256=None,
            changed=None,
            minted_revision=None,
            refusal_reasons=tuple(sorted(f.reason for f in exc.findings)),
        )
    except MalformedMarkerError:
        return ParityFingerprint(
            outcome="malformed",
            sanitised=None,
            marker_count=None,
            sanitised_sha256=None,
            changed=None,
            minted_revision=None,
            refusal_reasons=("malformed-marker",),
        )

    # Properties 1-3: sanitised bytes, marker count, transformed hash.
    # Property 4: revision-minting decision, taken from the importer, which is
    # itself bound to the canonical transform (so an adapter that diverges on
    # bytes would necessarily diverge on minted revision too).
    imported = import_page(
        SourcePage(resource_id=resource_id, revision=source_revision, content=source),
        rules=rules,
    )
    return ParityFingerprint(
        outcome="ok",
        sanitised=result.sanitised,
        marker_count=result.marker_count,
        sanitised_sha256=result.sanitised_sha256,
        changed=imported.changed,
        minted_revision=imported.revision,
        refusal_reasons=(),
    )


def assert_parity(a: ParityFingerprint, b: ParityFingerprint) -> None:
    """Raise ``AssertionError`` unless two fingerprints are identical.

    Used to prove a second importer path matches canonical on all five
    properties — the guarantee that two transforms cannot silently diverge.
    """
    if a != b:
        raise AssertionError(
            "parity violation: importer paths diverge "
            f"(outcome {a.outcome!r} vs {b.outcome!r}, "
            f"markers {a.marker_count} vs {b.marker_count}, "
            f"changed {a.changed} vs {b.changed}, "
            f"revision {a.minted_revision} vs {b.minted_revision}, "
            f"refusals {a.refusal_reasons} vs {b.refusal_reasons})"
        )
