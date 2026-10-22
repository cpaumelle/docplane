"""Regressions from the supervised WORK reviews, without any model calls."""
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.work_brief import WorkUpdate, group_items, merge_items
from app.work_brief_api import _request, router


def item(key="cleanup", **values):
    return {"item_id": key, "title": "Qualify corrected cleanup", "state": "OPEN", "stance": "ACTIVE",
            "next_action": "Run the reviewed acceptance", "owner": None, "reason": "Source accepted; execution pending",
            "references": [{"reference_type": "PR", "reference_id": "example/project#1"}], **values}


def update(*changes):
    return WorkUpdate.model_validate({"expected_context": "a" * 64, "summary": "Record progress",
        "reported_producer": "fixture-agent", "changes": list(changes)})


def test_named_update_preserves_unmentioned_items_ids_and_holds():
    before = [item(), item("held", stance="HELD", reason="Operator approval pending")]
    saved = deepcopy(before)
    after = merge_items(before, update({"item": item(state="DONE_IN_RECORD")}))
    assert before == saved  # historical checkpoint never mutated
    assert after[1] == before[1]
    assert after[0]["item_id"] == before[0]["item_id"]
    grouped = group_items(after, "ACTIVE")
    assert not grouped["next_actions"]
    assert grouped["held"] == [before[1]]
    assert grouped["recorded_done"][0]["state"] == "DONE_IN_RECORD"


@pytest.mark.parametrize("stance", ["HELD", "DEFERRED"])
def test_hold_or_deferral_cannot_be_released_or_rewritten_by_progress(stance):
    previous = item(stance=stance)
    for changed in (item(), item(stance=stance, reason="A different decision"),
                    item(stance=stance, state="DONE_IN_RECORD")):
        with pytest.raises(HTTPException) as caught:
            merge_items([previous], update({"item": changed}))
        assert caught.value.detail["code"] == "WORK_DECISION_REQUIRED"


def test_explicit_decision_is_retained_in_update_and_can_release_hold():
    request = update({"item": item(), "decision": {"reason": "Owner released the hold",
        "references": [{"reference_type": "ACTIVITY", "reference_id": str(uuid4())}]}})
    after = merge_items([item(stance="HELD")], request)
    assert after[0]["stance"] == "ACTIVE"
    assert request.changes[0].decision.reason == "Owner released the hold"


def test_completed_work_cannot_be_reopened_without_explicit_decision():
    with pytest.raises(HTTPException):
        merge_items([item(state="DONE_IN_RECORD")], update({"item": item()}))


@pytest.mark.parametrize("state", ["BLOCKED", "PAUSED", "PARKED", "SOAKING", "COMPLETE", "ABANDONED"])
def test_initiative_state_prevents_next_action_nudges(state):
    groups = group_items([item(), item("deferred", stance="DEFERRED")], state)
    assert not groups["execution_eligible"]
    assert groups["next_actions"] == []
    assert len(groups["paused_actions"]) == 1
    assert len(groups["deferred"]) == 1


def test_unclear_and_unknown_never_present_as_next_actions():
    groups = group_items([item(state="UNCLEAR"), item("unknown", stance="UNKNOWN")], "ACTIVE")
    assert groups["next_actions"] == []
    assert len(groups["uncertain"]) == 2


@pytest.mark.parametrize("change", [
    {"item": item(next_action=None)}, {"item": item(references=[])},
    {"item": item(reason=" ")}, {"item": item(state="DEFERRED")},
    {"item": item(), "decision": {"reason": "Approved", "references": []}},
    {"item": item(), "unknown": "ignored?"},
])
def test_incomplete_or_unrecognised_updates_are_refused(change):
    with pytest.raises(ValidationError):
        update(change)


def test_duplicate_item_changes_refused_and_whole_snapshot_is_bounded():
    with pytest.raises(ValidationError):
        update({"item": item()}, {"item": item()})
    with pytest.raises(HTTPException) as caught:
        merge_items([item(str(n)) for n in range(50)], update({"item": item("new")}))
    assert caught.value.status_code == 413


def test_server_context_hash_is_typed_not_mistaken_for_a_secret():
    request = update({"item": item()}).model_dump(mode="json")
    assert _request(request).expected_context == "a" * 64
    request["changes"][0]["item"]["reason"] = "a" * 64
    with pytest.raises(HTTPException) as caught:
        _request(request)
    assert caught.value.detail["code"] == "WORK_UPDATE_TEXT_REFUSED"


def test_invalid_payload_errors_do_not_reflect_submitted_text():
    with pytest.raises(HTTPException) as caught:
        _request({"unexpected": "PRIVATE_SENTINEL"})
    assert "PRIVATE_SENTINEL" not in str(caught.value.detail)


def test_authenticated_routes_and_required_uuid_key_contract():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    path = f"/api/v1/initiatives/{uuid4()}"
    assert client.get(path + "/brief").status_code == 401
    assert client.post(path + "/updates", json={}).status_code == 401
    parameters = app.openapi()["paths"]["/api/v1/initiatives/{initiative_id}/updates"]["post"]["parameters"]
    key = next(p for p in parameters if p["name"] == "Idempotency-Key")
    assert key["required"] and key["schema"]["format"] == "uuid"


def test_top_level_invalid_body_is_not_reflected_in_http_error():
    from app.agent_auth import Principal, require_contributor
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_contributor] = lambda: Principal(str(uuid4()), "AGENT", "fixture", str(uuid4()))
    result = TestClient(app).post(f"/api/v1/initiatives/{uuid4()}/updates",
        headers={"Idempotency-Key": str(uuid4())}, json=["PRIVATE_SENTINEL"])
    assert result.status_code == 422
    assert "PRIVATE_SENTINEL" not in result.text
