from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from app import agent_contract_api
from app.agent_auth import Principal
from app.application import app

ROOT = Path(__file__).resolve().parents[2]
client = TestClient(app)


def _principal() -> Principal:
    return Principal(
        principal_id="11111111-1111-1111-1111-111111111111",
        principal_kind="AGENT",
        display_name="cold-start-test",
        token_id="22222222-2222-2222-2222-222222222222",
    )


def _page_row(content: str = "# Example\n\nThe target phrase is documented here.") -> tuple:
    return (
        UUID("33333333-3333-3333-3333-333333333333"),
        "reference/example.md",
        "Example",
        "Reference/Example",
        content,
        "revision-7",
        7,
        "active",
        UUID("44444444-4444-4444-4444-444444444444"),
        "reference",
        "PUBLISHED",
        "REFERENCE",
        "UNVERIFIED",
        None,
        None,
        "NORMAL",
        False,
        1,
        "AUTHORED",
        datetime(2026, 7, 28, tzinfo=timezone.utc),
        "agent",
    )


class FakeCursor:
    def __init__(self, *, total: int, rows: list[tuple]):
        self.total = total
        self.rows = rows
        self.mode = ""
        self.calls: list[tuple[str, list | tuple]] = []

    def execute(self, query, params):
        normalized = " ".join(str(query).split())
        self.calls.append((normalized, params))
        self.mode = "count" if normalized.startswith("SELECT count(*)") else "rows"

    def fetchone(self):
        assert self.mode == "count"
        return (self.total,)

    def fetchall(self):
        assert self.mode == "rows"
        return self.rows


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def test_discovery_is_the_complete_unauthenticated_starting_point():
    response = client.get("/.well-known/docplane.json")
    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "docplane-agent-discovery-v4"
    assert body["authentication"]["token_acquisition"]["mode"] == "operator-issued"
    assert body["authentication"]["token_acquisition"]["self_service"] is False
    assert body["authentication"]["token_acquisition"]["credentials_returned_by_discovery"] is False
    assert body["required_headers"]["mutations"] == ["Authorization", "Idempotency-Key"]
    assert "REPLACE_DOCUMENT" in body["operation_types"]
    assert body["surfaces"]["single_page_replace"] == "/api/v1/pages/{resource_id}/replace"
    assert body["surfaces"]["single_page_patch"] == "/api/v1/pages/{resource_id}/patch"
    assert set(body["document_metadata"]) == {
        "contract_version", "identity_and_location", "corpus_and_category",
        "content_discovery", "revision_and_history", "lifecycle_and_publication",
        "trust_and_maintenance",
    }
    assert "knowledge_class" in body["document_metadata"]["corpus_and_category"]
    assert "provenance" in body["document_metadata"]["lifecycle_and_publication"]
    assert "facets" in body["discoverability_and_categorisation"]
    assert body["errors"]["PAGE_REVISION_STALE"]["remedy"]
    assert body["legacy"]["legacy_docs_api_contract_applies"] is False


def test_discovery_exposes_deployment_site_name_without_changing_product_identity(monkeypatch):
    monkeypatch.setenv("DOCPLANE_SITE_NAME", "Example Documentation")
    body = agent_contract_api.discovery()
    assert body["product"] == "DocPlane"
    assert body["site_name"] == "Example Documentation"


def test_assembled_app_has_one_authoritative_handler_per_extracted_path():
    for path in agent_contract_api.REPLACED_AGENT_PATHS:
        routes = [route for route in app.routes if getattr(route, "path", None) == path]
        assert len(routes) == 1, (path, routes)


def test_openapi_marks_every_agent_mutation_idempotency_header_required():
    schema = app.openapi()
    for path in agent_contract_api.REQUIRED_IDEMPOTENCY_PATHS:
        operation = schema["paths"][path]["post"]
        header = next(
            parameter
            for parameter in operation.get("parameters", [])
            if parameter.get("in") == "header" and parameter.get("name") == "Idempotency-Key"
        )
        assert header["required"] is True, path


def test_known_errors_are_enriched_without_losing_runtime_context():
    result = agent_contract_api.enrich_error_detail(
        {"code": "PAGE_REVISION_STALE", "current": "revision-8", "expected": "revision-7"}
    )
    assert result["current"] == "revision-8"
    assert result["expected"] == "revision-7"
    assert "rebase" in result["remedy"].lower()
    assert result["docs_url"] == "/.well-known/docplane.json"


def test_pages_count_is_response_size_and_total_is_filter_total(monkeypatch):
    cursor = FakeCursor(total=508, rows=[_page_row()])
    monkeypatch.setattr(agent_contract_api, "get_conn", lambda: FakeConnection(cursor))
    result = agent_contract_api.list_pages(
        status="active",
        path=None,
        workspace_key=None,
        limit=1,
        principal=_principal(),
    )
    assert result["count"] == 1
    assert result["total"] == 508
    assert result["limit"] == 1
    assert result["pages"][0]["path"] == "reference/example.md"


def test_search_returns_total_match_count_and_context_snippet(monkeypatch):
    cursor = FakeCursor(total=12, rows=[_page_row()])
    monkeypatch.setattr(agent_contract_api, "get_conn", lambda: FakeConnection(cursor))
    result = agent_contract_api.search_pages(
        q="target phrase",
        include_archived=False,
        limit=1,
        principal=_principal(),
    )
    assert result["count"] == 1
    assert result["total"] == 12
    hit = result["results"][0]
    assert "target phrase" in hit["snippet"].lower()
    assert "content" in hit["matched_in"]


def test_search_matches_non_contiguous_terms_and_ranks_exact_phrase_first(monkeypatch):
    cursor = FakeCursor(
        total=1,
        rows=[_page_row("# Example\n\nA stale systemd unit can block Ceph-volume activation by FSID.")],
    )
    monkeypatch.setattr(agent_contract_api, "get_conn", lambda: FakeConnection(cursor))

    result = agent_contract_api.search_pages(
        q="stale ceph-volume activation fsid systemd",
        include_archived=False,
        limit=10,
        principal=_principal(),
    )

    assert result["count"] == 1
    assert "stale" in result["results"][0]["snippet"].lower()
    assert result["results"][0]["matched_in"] == ["content"]
    count_query, count_params = cursor.calls[0]
    rows_query, rows_params = cursor.calls[1]
    assert count_query.count("ILIKE %s") == 20
    assert count_params == [
        pattern
        for term in ("stale", "ceph-volume", "activation", "fsid", "systemd")
        for pattern in [f"%{term}%"] * 4
    ]
    assert " AND " in count_query
    assert "CASE WHEN p.title ILIKE %s THEN 0" in rows_query
    assert rows_params[-1] == 10


def test_search_response_exposes_the_effective_terms(monkeypatch):
    """The caller must be able to see what their query actually became.

    Filler words are not removed, so a question-shaped query carries every word
    into matching and ranking. Without this field a caller cannot tell a sparse
    topic from a query diluted by common words.
    """
    cursor = FakeCursor(total=3, rows=[_page_row()])
    monkeypatch.setattr(agent_contract_api, "get_conn", lambda: FakeConnection(cursor))

    result = agent_contract_api.search_pages(
        q="what is the DNS authority invariant",
        include_archived=False,
        limit=1,
        principal=_principal(),
    )

    assert result["terms"] == ["what", "is", "the", "dns", "authority", "invariant"]
    assert result["query"] == "what is the DNS authority invariant"


def test_search_effective_terms_reflect_the_maximum_terms_cap(monkeypatch):
    """`terms` must show the cap, so silent truncation is visible to the caller."""
    cursor = FakeCursor(total=0, rows=[])
    monkeypatch.setattr(agent_contract_api, "get_conn", lambda: FakeConnection(cursor))

    query = " ".join(f"term-{index}" for index in range(20))
    result = agent_contract_api.search_pages(
        q=query,
        include_archived=False,
        limit=1,
        principal=_principal(),
    )

    assert result["terms"] == agent_contract_api._search_terms(query)
    assert len(result["terms"]) == agent_contract_api._MAX_SEARCH_TERMS


def test_search_terms_are_unique_and_bounded():
    query = " ".join(["repeat", "repeat", *(f"term-{index}" for index in range(20))])

    terms = agent_contract_api._search_terms(query)

    assert terms[0] == "repeat"
    assert len(terms) == agent_contract_api._MAX_SEARCH_TERMS
    assert len(terms) == len(set(terms))


def test_search_keeps_archived_filter_out_when_requested(monkeypatch):
    cursor = FakeCursor(total=1, rows=[_page_row()])
    monkeypatch.setattr(agent_contract_api, "get_conn", lambda: FakeConnection(cursor))

    agent_contract_api.search_pages(
        q="target missing-gap phrase",
        include_archived=True,
        limit=5,
        principal=_principal(),
    )

    assert all("p.status = 'active'" not in query for query, _params in cursor.calls)


def test_bootstrap_helper_matches_deployed_port_and_bounds_agent_tokens():
    script = (ROOT / "scripts/bootstrap-contributor.sh").read_text(encoding="utf-8")
    assert "DOCPLANE_API_PORT:-127.0.0.1:18010" in script
    assert 'if not spec and kind == "AGENT":' in script
    assert 'spec = "24h"' in script
    assert "never, <n>h, <n>d, or RFC3339" in script


def test_contract_documents_publication_timing_and_targeted_edits() -> None:
    """A cold-start agent must be able to size timeouts and pick a bounded edit.

    Publication is synchronous and rebuilds the served corpus, so a mutation can
    outlive a default client timeout while still committing. The contract has to
    say so, state the retry rule, and point small edits at a bounded operation
    instead of a whole-document replace.
    """
    body = client.get("/.well-known/docplane.json").json()

    publication = body["publication"]
    assert publication["model"] == "synchronous"
    assert publication["client_timeout_seconds_min"] >= 60
    assert "identical Idempotency-Key" in publication["retry_rule"]

    replace_steps = " ".join(body["quick_start"]["single_page_replace"])
    assert "client_timeout_seconds_min" in replace_steps
    assert "Never retry with a new key." in replace_steps
    assert "page.revision" in replace_steps

    targeted = " ".join(body["quick_start"]["targeted_edit"])
    assert "REPLACE_SECTION" in targeted
    assert "INSERT_AFTER_HEADING" in targeted


def test_replaced_agent_paths_all_still_exist_in_the_monolithic_router():
    """A stale entry in REPLACED_AGENT_PATHS silently protects nothing.

    The set exists to strip superseded handlers out of the assembled app. If a
    path is listed but no longer defined in the monolithic router, the entry is
    dead weight and hides the fact that nothing is being replaced any more.
    """
    from app.agent_api import router as monolithic_agent_router

    monolithic_paths = {
        getattr(route, "path", None) for route in monolithic_agent_router.routes
    }
    for path in agent_contract_api.REPLACED_AGENT_PATHS:
        assert path in monolithic_paths, (
            f"{path} is in REPLACED_AGENT_PATHS but no longer exists in agent_api — "
            "remove the stale entry"
        )


def test_every_superseded_handler_is_marked_as_not_served():
    """Each superseded handler must say so in the source.

    Reading an unmarked superseded handler gives a completely wrong model of the
    API: the /api/v1/search one is a whole-string ILIKE with no tokenisation,
    while the served implementation splits the query into terms and ANDs them.
    The marker is what stops the next reader (or a future edit) trusting it.
    """
    import inspect

    from app import agent_api

    source = inspect.getsource(agent_api)
    for path in agent_contract_api.REPLACED_AGENT_PATHS:
        decorator_index = source.find(f'"{path}")')
        assert decorator_index != -1, f"no decorator found for {path}"
        preamble = source[max(0, decorator_index - 600):decorator_index]
        assert "SUPERSEDED" in preamble, (
            f"the handler for {path} is filtered out of the app but is not marked "
            "SUPERSEDED — a reader will believe it is live"
        )


def test_search_results_carry_provenance(monkeypatch):
    """A page discovered through search must say whether it is derived output.

    _page_select already retrieves provenance and _page_dict already maps it, so
    this costs no extra query — the field was simply dropped when the result
    dict was assembled. Without it a client has to spend one request per result
    to learn what it already fetched.
    """
    cursor = FakeCursor(total=1, rows=[_page_row()])
    monkeypatch.setattr(agent_contract_api, "get_conn", lambda: FakeConnection(cursor))

    result = agent_contract_api.search_pages(
        q="anything", include_archived=False, limit=1, principal=_principal(),
    )

    hit = result["results"][0]
    assert "provenance" in hit
    assert hit["provenance"] in ("AUTHORED", "GENERATED")


def test_page_row_fixture_matches_the_real_column_list():
    """The fixture is positional, so a new column in _page_select silently shifts it.

    That had already happened: the fixture carried 20 values against a 21-column
    select, so _page_dict mapped updated_at into provenance and updated_by into
    updated_at, with updated_by falling off the end. Every test passed, because
    none of them asserted on those three fields.
    """
    from app.agent_api import _page_select

    select = _page_select().split("FROM")[0]
    columns = [c.strip().split(".")[-1] for c in select.replace("SELECT", "").split(",")]
    assert len(_page_row()) == len(columns), (
        f"_page_row has {len(_page_row())} values against {len(columns)} selected columns"
    )
    # provenance must be a provenance value, not whatever fell into its slot
    from app.agent_api import _page_dict
    assert _page_dict(_page_row())["provenance"] in ("AUTHORED", "GENERATED")
