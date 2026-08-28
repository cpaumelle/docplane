"""Attended runner contract: authoritative reads -> pure derivation -> one WORK PUT."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import reconcile_generated_conditions as runner  # noqa: E402
import meter_list  # noqa: E402
from app.work_conditions_api import (  # noqa: E402
    GeneratedArtifactConditionSet,
    _validate_briefings,
)


PRINCIPAL_ID = "11111111-1111-4111-8111-111111111111"
ARTIFACT_IDS = {
    "work": "22222222-2222-4222-8222-222222222222",
    "schema": "33333333-3333-4333-8333-333333333333",
    "meter": "44444444-4444-4444-8444-444444444444",
}
SOURCE_IDS = {
    "work": "55555555-5555-4555-8555-555555555555",
    "schema": "66666666-6666-4666-8666-666666666666",
    "meter": "77777777-7777-4777-8777-777777777777",
}

# This production-shaped identity is anchored independently in the owning
# generator contract. Artifact entity keys preserve dots; only page-path
# segments use the separate hyphenating path slug.
PRODUCTION_METER_SOURCE_KEY = "hub2.prometheus"
PRODUCTION_METER_ARTIFACT_KEY = "meter-list-hub2.prometheus"
CANONICAL_ARTIFACT_KEYS = {
    "work": "work-catalogue",
    "schema": "schema-catalogue-docplane",
    "meter": PRODUCTION_METER_ARTIFACT_KEY,
}


class FakeClient:
    def __init__(self, family: str, *, exact_catalogues: bool = True, oversized: bool = False):
        self.family = family
        self.spec = runner.FAMILIES[family]
        self.artifact_id = ARTIFACT_IDS[family]
        self.source_id = SOURCE_IDS[family]
        self.puts: list[tuple[str, dict, str]] = []
        self.calls: list[str] = []
        self.exact_catalogues = exact_catalogues
        self.oversized = oversized
        self.pages: dict[str, dict] = {}
        self.entities: dict[str, dict] = {}
        self.schema_entities: list[dict] = []
        self.rules: list[dict] = []
        self._seed()

    def _page(self, path: str, resource_id: str):
        self.pages[path] = {
            "resource_id": resource_id,
            "path": path,
            "status": "active",
            "provenance": "GENERATED",
        }

    def _entity(self, entity_id: str, kind: str, key: str, *, status: str = "ACTIVE", target: str | None = None, attributes=None):
        pages = [] if not target or not self.exact_catalogues else [
            {"relation": "CATALOGUES", "page_resource_id": target, "path": "bounded.md"}
        ]
        entity = {
            "entity_id": entity_id,
            "entity_kind": kind,
            "entity_key": key,
            "display_name": key,
            "status": status,
            "attributes": attributes or {},
            "pages": pages,
        }
        self.entities[entity_id] = entity
        return entity

    def _seed(self):
        index_id = "88888888-8888-4888-8888-888888888888"
        self._page(self.spec.index_path, index_id)
        self._entity(
            self.source_id, self.spec.source_kind, self.spec.source_key,
            target=index_id,
        )
        self.target_paths = [self.spec.index_path]
        if self.family == "schema":
            page_id = "99999999-9999-4999-8999-999999999999"
            path = "model/schema-catalogue/docplane/docs.md"
            self._page(path, page_id)
            entity_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            entity = self._entity(entity_id, "SCHEMA", "docplane.docs", target=page_id)
            self.schema_entities = [entity]
            self.target_paths.append(path)
        elif self.family == "meter":
            page_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
            path = "observe/meter-list/hub2-prometheus/shared.md"
            self._page(path, page_id)
            entity_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
            entity = self._entity(
                entity_id,
                "MONITOR_RULE",
                "rule.shared",
                target=page_id,
                attributes={"source_page_path": path},
            )
            self.rules = [entity]
            self.target_paths.append(path)

    def get(self, path: str):
        self.calls.append(path)
        if path == "/api/v1/me":
            return {"principal_id": PRINCIPAL_ID, "principal_kind": "AUTOMATION"}
        if path.startswith("/api/v1/model/artifacts?"):
            return {
                "artifacts": [{
                    "artifact_id": self.artifact_id,
                    "artifact_key": CANONICAL_ARTIFACT_KEYS[self.family],
                    "source_entity_id": self.source_id,
                    "status": "DECLARED",
                    "target_page_paths": self.target_paths,
                }],
                "truncated": False,
            }
        if path == f"/api/v1/model/artifacts/{self.artifact_id}/status":
            if self.oversized:
                return {
                    "artifact_id": self.artifact_id,
                    "artifact_key": self.spec.artifact_key,
                    "source_entity_id": self.source_id,
                    "execution_contract": None,
                    "execution_contract_status": "DECLARED",
                    "freshness": {
                        "state": "DRIFTED",
                        "reason": "SOURCE_CHANGED",
                        "projection_correspondence": "MISMATCH",
                        "generation": {
                            "observation_id": "observation-" + "x" * 10_000,
                            "outcome": "NOMINAL",
                        },
                        "source_observation": {
                            "observation_id": "source-1",
                            "outcome": "NOMINAL",
                        },
                    },
                }
            return {
                "artifact_id": self.artifact_id,
                "artifact_key": self.spec.artifact_key,
                "source_entity_id": self.source_id,
                "execution_contract": None,
                "execution_contract_status": (
                    "DECLARED" if self.family == "work" else "UNDECLARED_TRANSITIONAL"
                ),
                "freshness": (
                    {
                        "state": "FRESH",
                        "reason": "FINGERPRINTS_MATCH",
                        "projection_correspondence": "MATCH",
                        "source_observation_status": "CURRENT",
                    }
                    if self.family == "work"
                    else {
                        "state": "UNKNOWN",
                        "reason": "SOURCE_UNOBSERVED",
                        "projection_correspondence": "UNKNOWN",
                        "source_observation": None,
                        "source_observation_status": "ABSENT",
                    }
                ),
            }
        if path.startswith("/api/v1/model/entities?entity_kind=SCHEMA"):
            return {"entities": self.schema_entities, "truncated": False}
        if path.startswith("/api/v1/model/entities?entity_kind=MONITOR_RULE"):
            return {"entities": self.rules, "truncated": False}
        if path.startswith("/api/v1/model/entities/"):
            return self.entities[path.rsplit("/", 1)[-1]]
        if path.startswith("/api/v1/pages?"):
            from urllib.parse import parse_qs, urlsplit

            wanted = parse_qs(urlsplit(path).query)["path"][0]
            return {"pages": [self.pages[wanted]] if wanted in self.pages else [], "truncated": False}
        raise AssertionError(f"unexpected read {path}")

    def put_conditions(self, artifact_id: str, payload: dict, idempotency_key: str):
        self.puts.append((artifact_id, payload, idempotency_key))
        kinds = sorted(item["condition_kind"] for item in payload["conditions"])
        return {
            "artifact_id": artifact_id,
            "desired_condition_kinds": kinds,
            "opened": kinds,
            "reopened": [],
            "refreshed": [],
            "resolved": [],
            "continuing": [],
            "changed": bool(kinds),
            # Deliberately uncontrolled fields must not reach runner output.
            "debug": "password=must-not-print",
        }


def _invoke(family: str, *, exact_catalogues: bool = True, oversized: bool = False):
    client = FakeClient(family, exact_catalogues=exact_catalogues, oversized=oversized)
    key = str(uuid4())
    result = runner.reconcile(client, family, key, PRINCIPAL_ID)
    return client, key, result


@pytest.mark.parametrize(
    ("family", "expected"),
    [
        ("work", []),
        ("schema", ["EXECUTION_CONTRACT_MISSING", "SOURCE_OBSERVATION_MISSING"]),
        ("meter", ["EXECUTION_CONTRACT_MISSING", "SOURCE_OBSERVATION_MISSING"]),
    ],
)
def test_family_producer_to_real_work_receiver_contract(family, expected):
    client, key, result = _invoke(family)
    assert len(client.puts) == 1
    artifact_id, payload, used_key = client.puts[0]
    request = GeneratedArtifactConditionSet.model_validate(payload)
    _validate_briefings(request)
    assert used_key == key
    assert artifact_id == ARTIFACT_IDS[family]
    assert [item.condition_kind for item in request.conditions] == expected
    assert result["derived_condition_kinds"] == expected


def test_meter_family_identity_is_anchored_in_the_generator_contract():
    assert meter_list._slug(PRODUCTION_METER_SOURCE_KEY) == PRODUCTION_METER_SOURCE_KEY
    assert f"meter-list-{meter_list._slug(PRODUCTION_METER_SOURCE_KEY)}" == PRODUCTION_METER_ARTIFACT_KEY
    assert runner.FAMILIES["meter"] == runner.FamilySpec(
        PRODUCTION_METER_ARTIFACT_KEY,
        "SERVICE",
        PRODUCTION_METER_SOURCE_KEY,
        "observe/meter-list/hub2-prometheus/index.md",
    )


def test_hyphenated_meter_artifact_key_is_not_an_alias():
    client = FakeClient("meter")
    original = client.get

    def wrong_key(path):
        value = original(path)
        if path.startswith("/api/v1/model/artifacts?"):
            value["artifacts"][0]["artifact_key"] = "meter-list-hub2-prometheus"
        return value

    client.get = wrong_key
    with pytest.raises(runner.RunnerError, match="found 0"):
        runner.reconcile(client, "meter", str(uuid4()), PRINCIPAL_ID)
    assert client.puts == []


def test_meter_correct_artifact_and_source_identity_are_accepted():
    client, _key, result = _invoke("meter")
    assert result["artifact_key"] == PRODUCTION_METER_ARTIFACT_KEY
    assert client.entities[client.source_id]["entity_kind"] == "SERVICE"
    assert client.entities[client.source_id]["entity_key"] == PRODUCTION_METER_SOURCE_KEY


@pytest.mark.parametrize("field,value", [("entity_kind", "SYSTEM"), ("entity_key", "other.prometheus")])
def test_meter_conflicting_source_identity_fails_closed(field, value):
    client = FakeClient("meter")
    client.entities[client.source_id][field] = value
    with pytest.raises(runner.RunnerError, match="source identity"):
        runner.reconcile(client, "meter", str(uuid4()), PRINCIPAL_ID)
    assert client.puts == []


@pytest.mark.parametrize("shape", ["missing", "duplicate", "retired", "conflicting"])
def test_meter_missing_duplicate_retired_or_conflicting_artifact_fails_closed(shape):
    client = FakeClient("meter")
    original = client.get

    def artifact_shape(path):
        value = original(path)
        if not path.startswith("/api/v1/model/artifacts?"):
            return value
        artifact = value["artifacts"][0]
        if shape == "missing":
            value["artifacts"] = []
        elif shape == "duplicate":
            value["artifacts"] = [artifact, {**artifact, "artifact_id": str(uuid4())}]
        elif shape == "retired":
            artifact["status"] = "RETIRED"
        else:
            conflicting_source = str(uuid4())
            artifact["source_entity_id"] = conflicting_source
            client._entity(conflicting_source, "SERVICE", "other.prometheus")
        return value

    client.get = artifact_shape
    with pytest.raises(runner.RunnerError):
        runner.reconcile(client, "meter", str(uuid4()), PRINCIPAL_ID)
    assert client.puts == []


def test_work_empty_set_is_submitted_as_the_complete_set_without_invention():
    client, _key, result = _invoke("work")
    assert client.puts[0][1] == {"conditions": []}
    assert result["request_condition_count"] == 0
    assert result["reconciliation"]["changed"] is False


def test_schema_opened_two_response_is_validated_completely():
    _client, _key, result = _invoke("schema")
    expected = ["EXECUTION_CONTRACT_MISSING", "SOURCE_OBSERVATION_MISSING"]
    assert result["reconciliation"] == {
        "artifact_id": ARTIFACT_IDS["schema"],
        "desired_condition_kinds": expected,
        "opened": expected,
        "reopened": [],
        "refreshed": [],
        "resolved": [],
        "continuing": [],
        "changed": True,
    }


def test_same_schema_set_may_be_reported_as_continuing():
    client = FakeClient("schema")
    expected = ["EXECUTION_CONTRACT_MISSING", "SOURCE_OBSERVATION_MISSING"]

    def continuing(artifact_id, _payload, _key):
        return {
            "artifact_id": artifact_id,
            "desired_condition_kinds": expected,
            "opened": [],
            "reopened": [],
            "refreshed": [],
            "resolved": [],
            "continuing": expected,
            "changed": False,
        }

    client.put_conditions = continuing
    result = runner.reconcile(client, "schema", str(uuid4()), PRINCIPAL_ID)
    assert result["reconciliation"]["continuing"] == expected
    assert result["reconciliation"]["changed"] is False


def test_legitimate_resolved_condition_is_outside_desired_set():
    client = FakeClient("work")

    def resolved(artifact_id, _payload, _key):
        return {
            "artifact_id": artifact_id,
            "desired_condition_kinds": [],
            "opened": [],
            "reopened": [],
            "refreshed": [],
            "resolved": ["SOURCE_OBSERVATION_MISSING"],
            "continuing": [],
            "changed": True,
        }

    client.put_conditions = resolved
    result = runner.reconcile(client, "work", str(uuid4()), PRINCIPAL_ID)
    assert result["reconciliation"]["resolved"] == ["SOURCE_OBSERVATION_MISSING"]
    assert result["reconciliation"]["changed"] is True


def test_simultaneous_catalogues_and_evidence_conditions_are_deterministic():
    first, _, _ = _invoke("schema", exact_catalogues=False)
    second, _, _ = _invoke("schema", exact_catalogues=False)
    assert first.puts[0][1] == second.puts[0][1]
    assert [item["condition_kind"] for item in first.puts[0][1]["conditions"]] == [
        "CATALOGUES_MISSING",
        "EXECUTION_CONTRACT_MISSING",
        "SOURCE_OBSERVATION_MISSING",
    ]


def test_schema_historical_active_entity_without_generated_target_desires_empty_set():
    client = FakeClient("schema")
    stale_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    stale_page = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    stale = client._entity(stale_id, "SCHEMA", "docplane.legacy", target=stale_page)
    client.schema_entities.append(stale)
    runner.reconcile(client, "schema", str(uuid4()), PRINCIPAL_ID)
    request = GeneratedArtifactConditionSet.model_validate(client.puts[0][1])
    drifted = next(item for item in request.conditions if item.condition_kind == "CATALOGUES_DRIFTED")
    sample = next(item for item in drifted.briefing["samples"] if item["entity_id"] == stale_id)
    assert sample["entity_status"] == "ACTIVE"
    assert sample["extra_page_resource_ids"] == [stale_page]


def test_realistic_fingerprints_and_digest_shaped_paths_never_enter_request():
    client = FakeClient("work")
    digest_a = "0123456789abcdef" * 4
    digest_b = "fedcba9876543210" * 4
    original = client.get

    def get(path):
        value = original(path)
        if path.endswith("/status"):
            value["freshness"] = {
                "state": "DRIFTED",
                "reason": "SOURCE_CHANGED",
                "projection_correspondence": "MISMATCH",
                "generated_fingerprint": digest_a,
                "source_fingerprint": digest_b,
                "generation": {"observation_id": "generation-1", "outcome": "NOMINAL", "source_fingerprint": digest_a},
                "source_observation": {"observation_id": "source-1", "outcome": "NOMINAL", "source_fingerprint": digest_b},
            }
        return value

    client.get = get
    result = runner.reconcile(client, "work", str(uuid4()), PRINCIPAL_ID)
    encoded = json.dumps(client.puts[0][1], sort_keys=True)
    assert digest_a not in encoded and digest_b not in encoded
    assert result["derived_condition_kinds"] == ["DRIFTED"]


def test_oversized_identity_is_explicitly_bounded_and_receiver_accepted():
    client, _, _ = _invoke("meter", oversized=True)
    request = GeneratedArtifactConditionSet.model_validate(client.puts[0][1])
    _validate_briefings(request)
    drifted = next(item for item in request.conditions if item.condition_kind == "DRIFTED")
    assert drifted.briefing["briefing_truncation"]["long_field_count"] == 1
    assert drifted.briefing["generation"]["observation_id"]["omitted"] is True


@pytest.mark.parametrize("value", ["", "not-a-uuid"])
def test_missing_or_invalid_idempotency_key_fails_before_put(value):
    client = FakeClient("work")
    with pytest.raises(runner.RunnerError, match="idempotency key must be a UUID"):
        runner.reconcile(client, "work", value, PRINCIPAL_ID)
    assert client.puts == []


def test_exact_retry_preserves_key_and_conflict_is_not_hidden():
    client = FakeClient("work")
    key = str(uuid4())
    runner.reconcile(client, "work", key, PRINCIPAL_ID)
    runner.reconcile(client, "work", key, PRINCIPAL_ID)
    assert [put[2] for put in client.puts] == [key, key]

    def conflict(*_args):
        raise runner.RunnerError("DocPlane API request failed: HTTP 409")

    client.put_conditions = conflict
    with pytest.raises(runner.RunnerError, match="HTTP 409"):
        runner.reconcile(client, "work", key, PRINCIPAL_ID)


def test_read_failure_and_family_identity_mismatch_make_zero_puts():
    client = FakeClient("work")
    original = client.get

    def failed(path):
        if path.startswith("/api/v1/model/artifacts?"):
            raise runner.RunnerError("read failed")
        return original(path)

    client.get = failed
    with pytest.raises(runner.RunnerError, match="read failed"):
        runner.reconcile(client, "work", str(uuid4()), PRINCIPAL_ID)
    assert client.puts == []

    mismatch = FakeClient("work")
    mismatch.entities[mismatch.source_id]["entity_key"] = "wrong-family"
    with pytest.raises(runner.RunnerError, match="source identity"):
        runner.reconcile(mismatch, "work", str(uuid4()), PRINCIPAL_ID)
    assert mismatch.puts == []


def test_wrong_authenticated_identity_makes_zero_puts():
    client = FakeClient("schema")
    with pytest.raises(runner.RunnerError, match="dedicated AUTOMATION"):
        runner.reconcile(client, "schema", str(uuid4()), str(uuid4()))
    assert client.puts == []


def _valid_schema_response():
    expected = ["EXECUTION_CONTRACT_MISSING", "SOURCE_OBSERVATION_MISSING"]
    return {
        "artifact_id": ARTIFACT_IDS["schema"],
        "desired_condition_kinds": expected,
        "opened": expected,
        "reopened": [],
        "refreshed": [],
        "resolved": [],
        "continuing": [],
        "changed": True,
    }


@pytest.mark.parametrize(
    "field",
    [
        "artifact_id",
        "desired_condition_kinds",
        "opened",
        "reopened",
        "refreshed",
        "resolved",
        "continuing",
        "changed",
    ],
)
def test_each_required_receipt_field_missing_fails_closed(field):
    response = _valid_schema_response()
    response.pop(field)
    with pytest.raises(runner.RunnerError, match="WORK reconciliation response"):
        runner._bounded_receipt(
            response,
            expected_artifact_id=ARTIFACT_IDS["schema"],
            desired_condition_kinds=[
                "EXECUTION_CONTRACT_MISSING",
                "SOURCE_OBSERVATION_MISSING",
            ],
        )


def _receipt_error(response, match):
    with pytest.raises(runner.RunnerError, match=match):
        runner._bounded_receipt(
            response,
            expected_artifact_id=ARTIFACT_IDS["schema"],
            desired_condition_kinds=[
                "EXECUTION_CONTRACT_MISSING",
                "SOURCE_OBSERVATION_MISSING",
            ],
        )


def test_malformed_duplicate_unknown_and_mismatched_receipt_kinds_fail_closed():
    response = _valid_schema_response()
    response["opened"] = "SOURCE_OBSERVATION_MISSING"
    _receipt_error(response, "invalid opened")

    response = _valid_schema_response()
    response["opened"] = ["SOURCE_OBSERVATION_MISSING", "SOURCE_OBSERVATION_MISSING"]
    _receipt_error(response, "duplicate opened")

    response = _valid_schema_response()
    response["opened"] = ["NOT_A_CONDITION"]
    _receipt_error(response, "invalid opened")

    response = _valid_schema_response()
    response["desired_condition_kinds"] = ["SOURCE_OBSERVATION_MISSING"]
    _receipt_error(response, "desired set does not match request")


def test_contradictory_receipt_partitions_fail_closed():
    response = _valid_schema_response()
    response["continuing"] = ["SOURCE_OBSERVATION_MISSING"]
    _receipt_error(response, "categories overlap")

    response = _valid_schema_response()
    response["opened"] = ["EXECUTION_CONTRACT_MISSING"]
    _receipt_error(response, "does not partition the desired set")

    response = _valid_schema_response()
    response["resolved"] = ["SOURCE_OBSERVATION_MISSING"]
    _receipt_error(response, "resolves a desired condition")


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda response: response.update(changed=False), "contradictory changed status"),
        (
            lambda response: response.update(
                opened=[],
                continuing=response["desired_condition_kinds"],
                changed=True,
            ),
            "contradictory changed status",
        ),
    ],
)
def test_incorrect_changed_status_fails_closed(mutate, match):
    response = _valid_schema_response()
    mutate(response)
    _receipt_error(response, match)


def test_conflicting_receipt_artifact_fails_closed():
    response = _valid_schema_response()
    response["artifact_id"] = ARTIFACT_IDS["work"]
    _receipt_error(response, "conflicting artifact")


def test_invalid_response_never_surfaces_uncontrolled_values():
    client = FakeClient("schema")

    def invalid(artifact_id, _payload, _key):
        response = _valid_schema_response()
        response["artifact_id"] = artifact_id
        response.pop("continuing")
        response["debug"] = "password=must-not-print"
        return response

    client.put_conditions = invalid
    with pytest.raises(runner.RunnerError) as failure:
        runner.reconcile(client, "schema", str(uuid4()), PRINCIPAL_ID)
    assert "must-not-print" not in str(failure.value)
    assert "password" not in str(failure.value)


def test_unknown_family_is_a_bounded_library_error_before_any_read_or_put():
    client = FakeClient("work")
    with pytest.raises(runner.RunnerError, match="unknown generated-artifact family"):
        runner.reconcile(client, "unknown", str(uuid4()), PRINCIPAL_ID)
    assert client.calls == []
    assert client.puts == []


def test_client_has_no_model_observe_publication_or_catalogues_mutation_method():
    public = {name for name in dir(runner.Client) if not name.startswith("_")}
    assert public == {"get", "put_conditions"}
    source = (ROOT / "scripts" / "reconcile_generated_conditions.py").read_text()
    assert '"POST"' not in source
    assert "/page-links/catalogues" not in source
    assert "/api/v1/observations" not in source
    assert "/api/v1/changes" not in source


def test_output_is_allowlisted_and_omits_uncontrolled_response_body():
    _client, _key, result = _invoke("schema")
    encoded = json.dumps(result, sort_keys=True)
    assert "must-not-print" not in encoded
    assert "debug" not in encoded
    assert "Authorization" not in encoded
    assert set(result) == {
        "artifact_id",
        "artifact_key",
        "family",
        "idempotency_key",
        "derived_condition_kinds",
        "request_condition_count",
        "reconciliation",
        "replay_status",
    }
