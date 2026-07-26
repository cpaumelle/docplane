from __future__ import annotations

import pathlib
import sys

import pytest


DOCS_API = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DOCS_API))

from app import deployment_attempts as attempts  # noqa: E402


@pytest.mark.parametrize(
    ("current", "next_status"),
    [
        ("PINNED", "BUILDING"),
        ("PINNED", "FAILED"),
        ("BUILDING", "VALIDATED"),
        ("BUILDING", "FAILED"),
        ("VALIDATED", "PROMOTED"),
        ("VALIDATED", "FAILED"),
        ("PROMOTED", "COMPLETED"),
        ("PROMOTED", "RECONCILIATION_REQUIRED"),
        ("RECONCILIATION_REQUIRED", "COMPLETED"),
        ("RECONCILIATION_REQUIRED", "FAILED"),
    ],
)
def test_allowed_transition_edges(current, next_status):
    attempts.check_transition(current, next_status)


@pytest.mark.parametrize(
    ("current", "next_status"),
    [
        ("PINNED", "VALIDATED"),
        ("BUILDING", "PROMOTED"),
        ("VALIDATED", "COMPLETED"),
        ("PROMOTED", "BUILDING"),
        ("COMPLETED", "FAILED"),
        ("FAILED", "PINNED"),
        ("FAILED", "COMPLETED"),
    ],
)
def test_skips_backwards_moves_and_terminal_rewrites_fail_closed(current, next_status):
    with pytest.raises(attempts.DeploymentAttemptError) as caught:
        attempts.check_transition(current, next_status)
    assert caught.value.rule == "ATTEMPT_ILLEGAL_TRANSITION"


def test_unknown_current_status_fails_closed():
    with pytest.raises(attempts.DeploymentAttemptError) as caught:
        attempts.check_transition("MYSTERY", "FAILED")
    assert caught.value.rule == "ATTEMPT_STATUS_UNKNOWN"


def test_all_product_modes_are_explicit_and_bounded():
    assert set(attempts.MODES) == {
        "CATCH_UP",
        "REDEPLOY",
        "BOOTSTRAP",
        "RECERTIFY_POLICY",
    }
    assert "legacy" not in {mode.lower() for mode in attempts.MODES}
