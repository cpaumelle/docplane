import re

import pytest

from app import agent_auth


def test_issued_tokens_are_random_prefixed_and_hashed():
    first, first_hash, first_prefix = agent_auth.issue_token()
    second, second_hash, second_prefix = agent_auth.issue_token()
    assert first.startswith("dp_")
    assert second.startswith("dp_")
    assert first != second
    assert first_hash == agent_auth.hash_token(first)
    assert second_hash == agent_auth.hash_token(second)
    assert first_hash != second_hash
    assert re.fullmatch(r"[0-9a-f]{64}", first_hash)
    assert first.startswith(first_prefix)
    assert second.startswith(second_prefix)


def test_scope_validation_is_sorted_deduplicated_and_fail_closed():
    assert agent_auth.validate_scopes(["docs:read", "docs:propose", "docs:read"]) == [
        "docs:propose",
        "docs:read",
    ]
    with pytest.raises(ValueError, match="unknown scopes"):
        agent_auth.validate_scopes(["docs:read", "shell:ssh"])
    with pytest.raises(ValueError, match="at least one scope"):
        agent_auth.validate_scopes([])
