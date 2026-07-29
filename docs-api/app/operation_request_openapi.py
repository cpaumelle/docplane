"""Publish a true operation_type-discriminated OpenAPI request union.

Several DocPlane operations intentionally share the same payload shape: archive
and restore both use an empty object, while the three bounded section operations
all use heading_id plus content. A payload-only ``oneOf`` is therefore
ambiguous. The complete request is discriminated by the sibling operation_type.
"""
from __future__ import annotations

import copy
from typing import Any

from app.operation_contract_api import (
    CONTRACT_ENDPOINT,
    CONTRACT_VERSION,
    OPERATION_ENDPOINT,
    _contracts,
    _schema_name,
)


def _request_schema_name(operation_type: str) -> str:
    return "".join(part.title() for part in operation_type.lower().split("_")) + "OperationRequest"


def _complete_request_schema(
    operation_type: str,
    contract: dict[str, Any],
    payload_ref: str,
) -> dict[str, Any]:
    binding = contract["binding"]
    properties: dict[str, Any] = {
        "operation_type": {"type": "string", "const": operation_type},
        "payload": {"$ref": payload_ref},
        "sequence": {"type": ["integer", "null"], "minimum": 0},
    }
    required = ["operation_type", "payload"]

    if binding["page_resource_id"] == "required":
        properties["page_resource_id"] = {"type": "string", "format": "uuid"}
        required.append("page_resource_id")
    if binding["expected_revision"] == "required":
        properties["expected_revision"] = {"type": "string", "minLength": 1}
        required.append("expected_revision")
    if binding["expected_section_hash"] == "required":
        properties["expected_section_hash"] = {"type": "string", "minLength": 1}
        required.append("expected_section_hash")

    return {
        "type": "object",
        "title": _request_schema_name(operation_type),
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def install_discriminated_operation_requests(app: Any) -> None:
    """Replace the generic operation request body with a valid discriminated union."""
    upstream_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        schema = upstream_openapi()
        marker = schema.get("x-docplane-operation-request-union")
        if isinstance(marker, dict) and marker.get("contract_version") == CONTRACT_VERSION:
            return schema

        contracts = _contracts()
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        request_refs: list[dict[str, str]] = []
        discriminator_mapping: dict[str, str] = {}
        payload_refs: list[dict[str, str]] = []

        for operation_type, contract in contracts.items():
            payload_name = _schema_name(operation_type)
            payload_ref = f"#/components/schemas/{payload_name}"
            payload_refs.append({"$ref": payload_ref})

            request_name = _request_schema_name(operation_type)
            request_ref = f"#/components/schemas/{request_name}"
            components[request_name] = _complete_request_schema(operation_type, contract, payload_ref)
            request_refs.append({"$ref": request_ref})
            discriminator_mapping[operation_type] = request_ref

        # The generic Pydantic component remains useful outside this endpoint,
        # but payload shapes overlap, so ``anyOf`` is the truthful payload-level
        # representation. Exact selection is made by the complete request union.
        request_model = components.get("ChangeOperationCreate")
        if isinstance(request_model, dict):
            properties = request_model.setdefault("properties", {})
            properties["payload"] = {
                "anyOf": payload_refs,
                "description": (
                    "Payload shape is selected by operation_type. Use the endpoint request-body "
                    f"discriminator or {CONTRACT_ENDPOINT} for the exact mapping."
                ),
            }

        operation = schema.get("paths", {}).get(OPERATION_ENDPOINT, {}).get("post")
        if isinstance(operation, dict):
            media = operation.setdefault("requestBody", {}).setdefault("content", {}).setdefault(
                "application/json", {}
            )
            media["schema"] = {
                "oneOf": request_refs,
                "discriminator": {
                    "propertyName": "operation_type",
                    "mapping": discriminator_mapping,
                },
            }

        schema["x-docplane-operation-request-union"] = {
            "contract_version": CONTRACT_VERSION,
            "endpoint": OPERATION_ENDPOINT,
            "discriminator": "operation_type",
            "mapping": discriminator_mapping,
        }
        return schema

    app.openapi = custom_openapi
