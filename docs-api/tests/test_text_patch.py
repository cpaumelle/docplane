import pytest

from app.text_patch import apply_text_patch


def test_exact_patch_applies_and_reports_hash_only_impacts():
    result = apply_text_patch("alpha beta alpha", [{
        "old_text": "alpha", "new_text": "gamma", "expected_occurrences": 2,
    }])
    assert result.content == "gamma beta gamma"
    assert result.occurrences_replaced == 2
    assert result.before_sha256 != result.after_sha256
    assert "old_text" not in result.impacts[0]


@pytest.mark.parametrize("edits, code", [
    ([{"old_text": "missing", "new_text": "x"}], "PATCH_OCCURRENCE_MISMATCH"),
    ([{"old_text": "", "new_text": "x"}], "PATCH_OLD_TEXT_EMPTY"),
    ([{"old_text": "alpha", "new_text": "alpha"}], "PATCH_NO_CHANGE"),
    ([
        {"old_text": "alpha beta", "new_text": "x"},
        {"old_text": "beta", "new_text": "y"},
    ], "PATCH_EDITS_OVERLAP"),
])
def test_exact_patch_fails_closed(edits, code):
    with pytest.raises(ValueError, match=code):
        apply_text_patch("alpha beta", edits)
