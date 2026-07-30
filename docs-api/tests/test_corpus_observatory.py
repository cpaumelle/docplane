from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.agent_api import (
    OBSERVATORY_EXPORT_MAX_BYTES,
    OBSERVATORY_EXPORT_MAX_RESOURCES,
    _page,
)
from app.corpus_structure import build
from app.maintenance_policy import review_flags


NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


def page(path: str, **overrides):
    value = {
        "resource_id": f"id-{path}",
        "path": path,
        "title": path,
        "nav_path": path,
        "content": "# Page\n",
        "revision": f"rev-{path}",
        "version": 1,
        "status": "active",
        "workspace_key": "reference",
        "publication_state": "PUBLISHED",
        "knowledge_class": "REFERENCE",
        "verification_state": "VERIFIED",
        "owner_principal_id": "owner",
        "review_due_at": NOW + timedelta(days=90),
        "criticality": "NORMAL",
        "metadata_review_required": False,
        "provenance": "AUTHORED",
        "updated_at": NOW,
    }
    value.update(overrides)
    return value


def test_observatory_exposes_authoritative_directory_metrics_and_resources():
    pages = [
        page("operations/index.md", title="Operations"),
        page("operations/proxmox/runbook.md", content="# Runbook\n" * 3),
        page("operations/old.md", status="archived"),
    ]
    model = build(pages, now=NOW)
    proxmox = next(row for row in model["directories"] if row["path"] == "operations/proxmox")
    operations = next(row for row in model["sections"] if row["name"] == "operations")

    assert proxmox["direct_pages"] == 1
    assert proxmox["descendant_pages"] == 0
    assert proxmox["size_bytes"] == len(("# Runbook\n" * 3).encode())
    assert operations["archived_pages"] == 1
    assert model["pages"][0]["resource_id"]
    assert model["pages"][0]["revision"]


def test_review_reasons_are_bounded_deterministic_and_evidenced():
    pages = [
        page(
            f"reference/topic-{index}.md",
            title="Repeated" if index < 2 else f"Topic {index}",
            metadata_review_required=index == 0,
            review_due_at=NOW - timedelta(days=1) if index == 1 else NOW + timedelta(days=90),
        )
        for index in range(12)
    ]
    model = build(pages, now=NOW)
    candidate = next(row for row in model["review_candidates"] if row["path"] == "reference")
    codes = {reason["code"] for reason in candidate["reasons"]}

    assert codes == {
        "HIGH_DIRECT_PAGE_COUNT",
        "DUPLICATE_TITLE",
        "REPEATED_FILENAME_STEM",
        "MISSING_CLASSIFICATION",
        "REVIEW_OVERDUE",
    }
    assert candidate["confidence"] == "DETERMINISTIC"
    assert candidate["resources"]
    assert all(item["resource_id"] and item["revision"] for item in candidate["resources"])
    assert all(reason["explanation"] for reason in candidate["reasons"])


def test_lifecycle_is_explicitly_legacy_and_not_canonical():
    model = build(
        [page("reference/a.md", content="**Lifecycle:** ACTIVE\n")],
        now=NOW,
    )
    directory = next(row for row in model["directories"] if row["path"] == "reference")
    assert directory["legacy_lifecycle_signal"] == {"ACTIVE": 1}
    assert model["review_contract"]["legacy_lifecycle_canonical"] is False


def test_maintenance_flags_have_one_shared_policy_owner():
    missing = page("reference/a.md", metadata_review_required=True)
    overdue = page("reference/b.md", review_due_at=NOW - timedelta(days=1))
    assert review_flags(missing, NOW) == {"metadata_review"}
    assert review_flags(overdue, NOW) == {"verification_due"}


def test_named_after_pagination_is_explicit_and_invalid_cursors_fail_loud():
    first = _page([{"value": number} for number in range(3)], None, 2)
    assert first == {
        "items": [{"value": 0}, {"value": 1}],
        "count": 2,
        "total": 3,
        "has_more": True,
        "next_after": "Mg",
    }
    second = _page([{"value": number} for number in range(3)], first["next_after"], 2)
    assert second["items"] == [{"value": 2}]
    assert second["has_more"] is False
    assert second["next_after"] is None
    with pytest.raises(HTTPException) as failure:
        _page([], "not-a-cursor!", 100)
    assert failure.value.detail["code"] == "OBSERVATORY_CURSOR_INVALID"


def test_export_envelope_is_fixed_and_documented():
    assert OBSERVATORY_EXPORT_MAX_RESOURCES == 5000
    assert OBSERVATORY_EXPORT_MAX_BYTES == 10 * 1024 * 1024
