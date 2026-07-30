"""Single-owner maintenance policy shared by APIs and corpus observability."""
from __future__ import annotations

from datetime import datetime
from typing import Any


MAINTENANCE_PREDICATES = {
    "metadata_review": "p.metadata_review_required",
    "unowned": "p.owner_principal_id IS NULL AND p.publication_state = 'PUBLISHED'",
    "unverified": "p.verification_state = 'UNVERIFIED' AND p.publication_state = 'PUBLISHED'",
    "verification_due": (
        "p.verification_state = 'VERIFIED' "
        "AND p.review_due_at IS NOT NULL AND p.review_due_at <= %s"
    ),
    "expired": "p.verification_state = 'EXPIRED'",
    "outdated": "p.verification_state = 'OUTDATED'",
}


def review_flags(page: dict[str, Any], due_cutoff: datetime) -> set[str]:
    """Pure mirror of MAINTENANCE_PREDICATES for already-loaded snapshot rows."""
    flags: set[str] = set()
    if page.get("metadata_review_required"):
        flags.add("metadata_review")
    if page.get("owner_principal_id") is None and page.get("publication_state") == "PUBLISHED":
        flags.add("unowned")
    state = page.get("verification_state")
    if state == "UNVERIFIED" and page.get("publication_state") == "PUBLISHED":
        flags.add("unverified")
    due = page.get("review_due_at")
    if state == "VERIFIED" and due is not None and due <= due_cutoff:
        flags.add("verification_due")
    if state == "EXPIRED":
        flags.add("expired")
    if state == "OUTDATED":
        flags.add("outdated")
    return flags
