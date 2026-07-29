from __future__ import annotations

from fastapi.testclient import TestClient

from app.application import app
from app.operation_contract_api import CONTRACT_ENDPOINT, CONTRACT_VERSION, OPERATION_ENDPOINT
from app.publication import operation_types

client = TestClient(app)


def test_operation_contract_endpoint_covers_every_runtime_operation():
    response = client.get(CONTRACT_ENDPOINT)
    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == CONTRACT_VERSION
    assert body["operation_endpoint"] == OPERATION_ENDPOINT
    assert set(body["operations"]) == set(operation_types())

    create = body["operations"]["CREATE_PAGE"]
    assert create["binding"]["page_resource_id"] == "omit"
    assert create["payload_schema"]["required"] == ["path", "title", "nav_path", "content"]
    assert create["request_example"]["payload"]["path"] == "reference/example.md"

    section = body["operations"]["REPLACE_SECTION"]
    assert section["binding"]["page_resource_id"] == "required"
    assert section["binding"]["expected_revision"] == "required"
    assert section["binding"]["expected_section_hash"] == "required"

    archive = body["operations"]["ARCHIVE_PAGE"]
    assert archive["payload_schema"]["properties"] == {}
    assert archive["request_example"]["payload"] == {}


def test_cold_start_discovery_links_the_operation_contract_before_writes():
    response = client.get("/.well-known/docplane.json")
    assert response.status_code == 200
    body = response.json()
    assert body["operation_contracts"]["endpoint"] == CONTRACT_ENDPOINT
    assert body["operation_contracts"]["operation_endpoint"] == OPERATION_ENDPOINT
    assert body["surfaces"]["operation_contracts"] == CONTRACT_ENDPOINT
    assert body["quick_start"]["multi_operation_change"][0].startswith(f"GET {CONTRACT_ENDPOINT}")

    caller_note = body["authentication"]["token_acquisition"]["caller_policy_note"]
    assert "caller" in caller_note.lower()
    assert "framework" in caller_note.lower()
    assert "expiry" in caller_note.lower()


def test_openapi_publishes_payload_schemas_mapping_and_complete_examples():
    schema = app.openapi()
    runtime = set(operation_types())

    assert schema["x-docplane-operation-contract-version"] == CONTRACT_VERSION
    extension = schema["x-docplane-operation-contracts"]
    assert extension["endpoint"] == CONTRACT_ENDPOINT
    assert set(extension["mapping"]) == runtime

    request_model = schema["components"]["schemas"]["ChangeOperationCreate"]
    payload_variants = request_model["properties"]["payload"]["anyOf"]
    assert len(payload_variants) == len(runtime)
    assert set(request_model["x-docplane-operation-contracts"]) == runtime

    operation = schema["paths"][OPERATION_ENDPOINT]["post"]
    assert set(operation["x-docplane-operation-contracts"]) == runtime
    examples = operation["requestBody"]["content"]["application/json"]["examples"]
    assert set(examples) == runtime
    assert examples["CREATE_PAGE"]["value"]["payload"]["content"].startswith("# Example")
    assert examples["ARCHIVE_PAGE"]["value"]["payload"] == {}


def test_operation_endpoint_uses_complete_discriminated_request_union():
    schema = app.openapi()
    runtime = set(operation_types())
    media_schema = schema["paths"][OPERATION_ENDPOINT]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]

    assert len(media_schema["oneOf"]) == len(runtime)
    discriminator = media_schema["discriminator"]
    assert discriminator["propertyName"] == "operation_type"
    assert set(discriminator["mapping"]) == runtime

    for operation_type, ref in discriminator["mapping"].items():
        component = schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]
        assert component["properties"]["operation_type"]["const"] == operation_type
        assert "payload" in component["required"]
        assert component["properties"]["payload"]["$ref"].endswith("Payload")

    archive_ref = discriminator["mapping"]["ARCHIVE_PAGE"]
    restore_ref = discriminator["mapping"]["RESTORE_PAGE"]
    assert archive_ref != restore_ref


def test_openapi_accounts_for_caller_side_credential_approval_gates():
    guidance = app.openapi()["x-docplane-caller-safety"]["credential_issuance"]
    assert "caller" in guidance.lower()
    assert "framework" in guidance.lower()
    assert "expiry" in guidance.lower()
