"""Bounded, convergent knowledge-class apply driver contracts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from knowledge_class_apply import apply_report, load_curated  # noqa: E402
from schema_catalogue import ApiError  # noqa: E402


def report(*entries):
    return {"contract_version": "knowledge-class-suggestions-v1", "corpus_stamp": "stamp", "suggestions": list(entries)}


def page(resource_id="p1", path="operations/x.md", knowledge_class=None, metadata_version=3):
    return {
        "resource_id": resource_id,
        "path": path,
        "knowledge_class": knowledge_class,
        "metadata_version": metadata_version,
        "workspace_id": "00000000-0000-0000-0000-000000000001",
        "publication_state": "PUBLISHED",
        "criticality": "NORMAL",
        "owner_principal_id": None,
        "review_due_at": None,
        "provenance": "AUTHORED",
    }


class FakeClient:
    def __init__(self, pages, *, stale_ids=(), mismatch_ids=()):
        self.pages = {item["resource_id"]: dict(item) for item in pages}
        self.stale_ids = set(stale_ids)
        self.mismatch_ids = set(mismatch_ids)
        self.mutations = []

    def call(self, method, path, payload=None, idempotency_key=None):
        resource_id = path.split("/")[4]
        if method == "GET":
            return dict(self.pages[resource_id])
        self.mutations.append((resource_id, payload, idempotency_key))
        if resource_id in self.stale_ids:
            raise ApiError(409, {"detail": {"code": "PAGE_METADATA_VERSION_STALE", "current": 4}})
        if resource_id in self.mismatch_ids:
            raise ApiError(422, {"detail": {"code": "WORKSPACE_KNOWLEDGE_CLASS_MISMATCH"}})
        self.pages[resource_id]["knowledge_class"] = payload["knowledge_class"]
        self.pages[resource_id]["metadata_version"] += 1
        return dict(self.pages[resource_id])


def test_second_run_is_zero_mutation_convergence():
    curated = report({"resource_id": "p1", "path": "operations/x.md", "proposed": "OPERATION", "metadata_version": 3})
    client = FakeClient([page()])
    first = apply_report(client, curated)
    assert first["counts"]["applied"] == 1
    assert len(client.mutations) == 1
    second = apply_report(client, curated)
    assert second["counts"] == {
        "applied": 0, "already_matched": 1, "optimistic_lock_skips": 0,
        "coherence_refusals": 0, "remaining": 0,
    }
    assert len(client.mutations) == 1


def test_batch_cap_remainder_and_changed_class_skip():
    curated = report(
        {"resource_id": "p1", "proposed": "OPERATION", "metadata_version": 3},
        {"resource_id": "p2", "proposed": "REFERENCE", "metadata_version": 3},
        {"resource_id": "p3", "proposed": "DESIGN", "metadata_version": 3},
    )
    client = FakeClient([page("p1", knowledge_class="REFERENCE"), page("p2"), page("p3")])
    result = apply_report(client, curated, batch_limit=1)
    assert result["remaining"] == 1
    assert result["optimistic_lock_skips"][0]["code"] == "KNOWLEDGE_CLASS_CHANGED_SINCE_CURATION"
    assert [item[0] for item in client.mutations] == ["p2"]


def test_api_optimistic_lock_is_a_listed_skip_not_failure():
    curated = report({"resource_id": "p1", "proposed": "OPERATION", "metadata_version": 3})
    result = apply_report(FakeClient([page()], stale_ids={"p1"}), curated)
    assert result["counts"]["optimistic_lock_skips"] == 1
    assert result["optimistic_lock_skips"][0]["detail"]["code"] == "PAGE_METADATA_VERSION_STALE"


def test_coherence_refusal_is_listed_and_batch_continues():
    """docplane#123: one mis-workspaced WORK_NOTE must not halt the batch."""
    curated = report(
        {"resource_id": "p1", "proposed": "WORK_NOTE", "metadata_version": 3},
        {"resource_id": "p2", "proposed": "OPERATION", "metadata_version": 3},
    )
    client = FakeClient([page("p1", path="notes/a.md"), page("p2")], mismatch_ids={"p1"})
    result = apply_report(client, curated)
    assert result["counts"] == {
        "applied": 1, "already_matched": 0, "optimistic_lock_skips": 0,
        "coherence_refusals": 1, "remaining": 0,
    }
    refusal = result["coherence_refusals"][0]
    assert refusal["code"] == "WORKSPACE_KNOWLEDGE_CLASS_MISMATCH"
    assert refusal["path"] == "notes/a.md"
    assert refusal["workspace_id"] == page("p1")["workspace_id"]
    assert [item["resource_id"] for item in result["applied"]] == ["p2"]


def test_persistent_refusal_still_converges():
    """Replays keep the refusal visible without ever blocking convergence."""
    curated = report(
        {"resource_id": "p1", "proposed": "WORK_NOTE", "metadata_version": 3},
        {"resource_id": "p2", "proposed": "OPERATION", "metadata_version": 3},
    )
    client = FakeClient([page("p1"), page("p2")], mismatch_ids={"p1"})
    apply_report(client, curated)
    second = apply_report(client, curated)
    assert second["counts"] == {
        "applied": 0, "already_matched": 1, "optimistic_lock_skips": 0,
        "coherence_refusals": 1, "remaining": 0,
    }


def test_unknown_vocabulary_refuses_whole_file_before_calls(tmp_path):
    path = tmp_path / "curated.json"
    path.write_text(json.dumps(report(
        {"resource_id": "p1", "proposed": "OPERATION"},
        {"resource_id": "p2", "proposed": "MADE_UP"},
    )), encoding="utf-8")
    with pytest.raises(ValueError, match="MADE_UP"):
        load_curated(path)
