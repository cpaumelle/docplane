from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from app.application import app
from app.model_page_links_api import CataloguesPageLinkSet, _diff, reconcile_catalogues_page_links


ROUTE = "/api/v1/model/entities/{entity_id}/page-links/catalogues"


def test_catalogues_exact_set_route_is_additive_and_idempotency_bound():
    schema = app.openapi()
    assert ROUTE in schema["paths"]
    operation = schema["paths"][ROUTE]["put"]
    parameters = operation.get("parameters", [])
    assert any(item["name"] == "Idempotency-Key" for item in parameters)


def test_catalogues_request_is_an_exact_unique_set():
    one = "11111111-1111-1111-1111-111111111111"
    two = "22222222-2222-2222-2222-222222222222"
    request = CataloguesPageLinkSet(page_resource_ids=[one, two])
    assert [str(value) for value in request.page_resource_ids] == [one, two]
    with pytest.raises(ValidationError):
        CataloguesPageLinkSet(page_resource_ids=[one, one])


def test_catalogues_diff_is_exact_and_deterministic():
    current = {"a", "b", "c"}
    desired = {"b", "c", "d"}
    added, removed, continuing = _diff(current, desired)
    assert added == ["d"]
    assert removed == ["a"]
    assert continuing == ["b", "c"]


def test_catalogues_empty_set_removes_all_current_links():
    added, removed, continuing = _diff({"a", "b"}, set())
    assert added == []
    assert removed == ["a", "b"]
    assert continuing == []


def test_catalogues_semantics_do_not_alias_generated_ownership_or_other_relations():
    source = inspect.getsource(reconcile_catalogues_page_links)
    assert "generated_artifact" not in source
    assert "artifact_targets" not in source
    assert "relation = 'CATALOGUES'" in source
    assert "relation = %s" not in source
    for unrelated in ("DESCRIBES", "OPERATES", "DECIDES"):
        assert unrelated not in source
