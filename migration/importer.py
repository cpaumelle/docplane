"""Import-time revision minting with redaction.

This is a generic, storage-agnostic model of the importer invariant that the
Phase-4 regression suite protects. It is intentionally free of any database
dependency so the invariant can be proven in CI without standing up storage.

Invariant (defect (a) — revision-identity retention):
    A page has a content-version identity (``revision``). When importing a
    source document, the redaction transform may rewrite the content. If the
    transformed bytes differ from the source bytes, the imported page MUST
    carry a NEWLY MINTED revision distinct from the source revision. Retaining
    the source revision after a byte-changing rewrite is forbidden.

    Conversely, if redaction changed nothing, the source revision is retained
    (idempotent import mints no gratuitous revision).

The revision minted here is a deterministic function of ``(resource_id,
source_revision, sanitised_content)`` so that replaying an identical import
yields an identical revision — the determinism guarantee — while any change in
sanitised content produces a different revision.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from migration.redaction import DEFAULT_RULES, RedactionRule, redact


@dataclass(frozen=True)
class SourcePage:
    resource_id: str
    revision: str  # source revision identity
    content: str


@dataclass(frozen=True)
class ImportedPage:
    resource_id: str
    source_revision: str
    revision: str  # revision AFTER import
    content: str  # sanitised content
    content_sha256: str
    changed: bool  # True iff redaction altered the bytes

    @property
    def revision_retained(self) -> bool:
        """True iff the post-import revision equals the source revision."""
        return self.revision == self.source_revision


def _mint_revision(resource_id: str, source_revision: str, sanitised: str) -> str:
    """Deterministic content-bound revision identity.

    Bound to sanitised content so identical replays are stable and any content
    change necessarily changes the revision.
    """
    digest = hashlib.sha256(
        b"docplane-import-revision\x00"
        + resource_id.encode("utf-8")
        + b"\x00"
        + source_revision.encode("utf-8")
        + b"\x00"
        + sanitised.encode("utf-8")
    ).hexdigest()
    return f"rev-{digest[:32]}"


def import_page(
    page: SourcePage,
    *,
    rules: tuple[RedactionRule, ...] = DEFAULT_RULES,
) -> ImportedPage:
    """Import ``page``, applying redaction and enforcing the revision invariant."""
    result = redact(page.content, rules=rules, label=page.resource_id)

    if result.changed:
        # Byte-changing rewrite => a fresh revision is mandatory.
        revision = _mint_revision(page.resource_id, page.revision, result.sanitised)
        # Defensive: minting must not collide with the source revision.
        if revision == page.revision:  # pragma: no cover - cryptographically unreachable
            raise AssertionError("minted revision collided with source revision")
    else:
        # No byte change => idempotent import retains the source revision.
        revision = page.revision

    imported = ImportedPage(
        resource_id=page.resource_id,
        source_revision=page.revision,
        revision=revision,
        content=result.sanitised,
        content_sha256=result.sanitised_sha256,
        changed=result.changed,
    )

    # Hard post-condition: this is the exact defect (a) guard. If content
    # changed, the source revision identity must NOT survive.
    if imported.changed and imported.revision_retained:
        raise AssertionError(
            "revision-identity retention: content changed during import but "
            "the source revision was retained"
        )
    return imported
