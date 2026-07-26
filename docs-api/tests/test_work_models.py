from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.work_models import InitiativeCreate, PromotionUpdate, WorkTransition


def _initiative(**overrides):
    value = {
        "initiative_key": "agent-interface",
        "workspace_id": str(uuid4()),
        "title": "Agent interface",
        "objective": "Build endpoint-first agent operation",
    }
    value.update(overrides)
    return value


def test_soaking_requires_a_complete_observation_contract():
    with pytest.raises(ValidationError, match="SOAKING requires"):
        InitiativeCreate(**_initiative(work_state="SOAKING"))

    model = InitiativeCreate(
        **_initiative(
            work_state="SOAKING",
            soak_started_at=datetime.now(timezone.utc),
            soak_review_at=datetime.now(timezone.utc),
            soak_success_criteria="No failed writes for 48 hours",
            soak_failure_conditions="Any lost or duplicated mutation",
        )
    )
    assert model.work_state == "SOAKING"


def test_parking_requires_reason_and_reconsideration_contract():
    with pytest.raises(ValidationError, match="PARKED requires parked_reason"):
        InitiativeCreate(**_initiative(work_state="PARKED", parked_indefinitely=True))
    with pytest.raises(ValidationError, match="parked_review_at"):
        InitiativeCreate(**_initiative(work_state="PARKED", parked_reason="Await upstream"))

    model = InitiativeCreate(
        **_initiative(
            work_state="PARKED",
            parked_reason="Await upstream schema release",
            parked_indefinitely=True,
        )
    )
    assert model.parked_indefinitely is True


def test_blocked_transition_and_promotion_are_explicit():
    with pytest.raises(ValidationError, match="BLOCKED requires blocker_summary"):
        WorkTransition(expected_version=1, work_state="BLOCKED")

    transition = WorkTransition(
        expected_version=1,
        work_state="BLOCKED",
        blocker_summary="Waiting for WP8 merge guard",
    )
    assert transition.blocker_summary

    with pytest.raises(ValidationError, match="PROMOTED requires promotion_change_id"):
        PromotionUpdate(expected_version=1, promotion_state="PROMOTED")
