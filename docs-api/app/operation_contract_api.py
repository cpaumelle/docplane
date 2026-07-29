"""Machine-readable payload contracts for every DocPlane change operation.

The publication engine remains the sole mutation authority. This module exposes
its operation vocabulary as JSON Schema plus complete request examples so a
cold client never has to infer a payload shape from implementation code.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from fastapi import APIRouter

from app.publication import operation_types

router = APIRouter(tags=["docplane-contract"])

CONTRACT_VERSION = "docplane-operation-contract-v1"
CONTRACT_ENDPOINT = "/api/v1/operation-contracts"
OPERATION_ENDPOINT = "/api/v1/changes/{change_id}/operations"

_PATH_SCHEMA: dict[str, Any] = {
    "type": "string",
    "pattern": r"^[a-z0-9/_-]+\.md$",
    "examples": ["reference/example.md"],
}
_STRING_SCHEMA: dict[str, Any] = {"type": "string"}


def _object_schema(
    properties: dict[str, Any] | None = None,
    *,
    required: list[str] | None = None,
    description: str,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "description": description,
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


_PAYLOAD_SCHEMAS: dict[str, dict[str, Any]] = {
    "CREATE_PAGE": _object_schema(
        {
            "path": _PATH_SCHEMA,
            "title": {"type": "string", "minLength": 1},
            "nav_path": {"type": "string", "minLength": 1},
            "content": _STRING_SCHEMA,
            "workspace_key": {"type": "string", "pattern": r"^[a-z0-9][a-z0-9_-]{0,62}$"},
            "resource_id": {"type": "string", "format": "uuid"},
            "knowledge_class": {"type": ["string", "null"]},
            "criticality": {"type": "string", "default": "NORMAL"},
        },
        required=["path", "title", "nav_path", "content"],
        description="Create one active page. resource_id is optional; the server mints one when omitted.",
    ),
    "REPLACE_DOCUMENT": _object_schema(
        {
            "content": _STRING_SCHEMA,
            "title": {"type": "string", "minLength": 1},
            "nav_path": {"type": "string", "minLength": 1},
        },
        required=["content"],
        description="Replace the complete Markdown document and optionally its title or navigation path.",
    ),
    "PATCH_METADATA": _object_schema(
        {
            "title": {"type": "string", "minLength": 1},
            "nav_path": {"type": "string", "minLength": 1},
            "workspace_key": {"type": "string", "pattern": r"^[a-z0-9][a-z0-9_-]{0,62}$"},
            "knowledge_class": {"type": ["string", "null"]},
            "criticality": {"type": "string"},
        },
        description="Patch only the listed metadata fields; content and path are not changed.",
    ),
    "REPLACE_SECTION": _object_schema(
        {"heading_id": {"type": "string", "minLength": 1}, "content": _STRING_SCHEMA},
        required=["heading_id", "content"],
        description="Replace the section identified by an explicit Markdown heading ID.",
    ),
    "INSERT_BEFORE_HEADING": _object_schema(
        {"heading_id": {"type": "string", "minLength": 1}, "content": _STRING_SCHEMA},
        required=["heading_id", "content"],
        description="Insert Markdown immediately before a section with an explicit heading ID.",
    ),
    "INSERT_AFTER_HEADING": _object_schema(
        {"heading_id": {"type": "string", "minLength": 1}, "content": _STRING_SCHEMA},
        required=["heading_id", "content"],
        description="Insert Markdown immediately after a section with an explicit heading ID.",
    ),
    "MOVE_PAGE": _object_schema(
        {
            "to_path": _PATH_SCHEMA,
            "to_nav_path": {"type": "string", "minLength": 1},
            "create_redirect": {"type": "boolean", "default": True},
        },
        required=["to_path"],
        description="Move a page to a new canonical path, optionally changing navigation and creating a redirect.",
    ),
    "REPARENT_NAV": _object_schema(
        {"to_nav_path": {"type": "string", "minLength": 1}},
        required=["to_nav_path"],
        description="Change only the page's navigation placement.",
    ),
    "ARCHIVE_PAGE": _object_schema(description="Archive the revision-bound page; the payload is empty."),
    "RESTORE_PAGE": _object_schema(description="Restore the revision-bound archived page; the payload is empty."),
    "ADD_REDIRECT": _object_schema(
        {"from_path": _PATH_SCHEMA, "to_path": _PATH_SCHEMA},
        required=["from_path", "to_path"],
        description="Add or replace one redirect from an inactive Markdown path to an active page path.",
    ),
    "REMOVE_REDIRECT": _object_schema(
        {"from_path": _PATH_SCHEMA},
        required=["from_path"],
        description="Remove one redirect by its source path.",
    ),
    "REORDER_SECTIONS": _object_schema(
        {
            "order": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "uniqueItems": True,
            }
        },
        required=["order"],
        description="Replace the complete ordered list of top-level navigation sections.",
    ),
}

_UNBOUND_OPERATIONS = {"CREATE_PAGE", "ADD_REDIRECT", "REMOVE_REDIRECT", "REORDER_SECTIONS"}
_SECTION_OPERATIONS = {"REPLACE_SECTION", "INSERT_BEFORE_HEADING", "INSERT_AFTER_HEADING"}

_PAGE_ID = "11111111-1111-1111-1111-111111111111"
_REVISION = "revision-7"
_SECTION_HASH = "sha256:current-section-hash"

_REQUEST_EXAMPLES: dict[str, dict[str, Any]] = {
    "CREATE_PAGE": {
        "operation_type": "CREATE_PAGE",
        "payload": {
            "path": "reference/example.md",
            "title": "Example",
            "nav_path": "Reference/Example",
            "content": "# Example\n\nDocumented through DocPlane.\n",
        },
    },
    "REPLACE_DOCUMENT": {
        "operation_type": "REPLACE_DOCUMENT",
        "page_resource_id": _PAGE_ID,
        "expected_revision": _REVISION,
        "payload": {"content": "# Example\n\nCorrected content.\n"},
    },
    "PATCH_METADATA": {
        "operation_type": "PATCH_METADATA",
        "page_resource_id": _PAGE_ID,
        "expected_revision": _REVISION,
        "payload": {"title": "Updated example", "criticality": "NORMAL"},
    },
    "REPLACE_SECTION": {
        "operation_type": "REPLACE_SECTION",
        "page_resource_id": _PAGE_ID,
        "expected_revision": _REVISION,
        "expected_section_hash": _SECTION_HASH,
        "payload": {"heading_id": "agent-access", "content": "## Agent access {#agent-access}\n\nUpdated.\n"},
    },
    "INSERT_BEFORE_HEADING": {
        "operation_type": "INSERT_BEFORE_HEADING",
        "page_resource_id": _PAGE_ID,
        "expected_revision": _REVISION,
        "expected_section_hash": _SECTION_HASH,
        "payload": {"heading_id": "rollback", "content": "!!! warning\n    Verify the candidate first."},
    },
    "INSERT_AFTER_HEADING": {
        "operation_type": "INSERT_AFTER_HEADING",
        "page_resource_id": _PAGE_ID,
        "expected_revision": _REVISION,
        "expected_section_hash": _SECTION_HASH,
        "payload": {"heading_id": "verification", "content": "Verification completed."},
    },
    "MOVE_PAGE": {
        "operation_type": "MOVE_PAGE",
        "page_resource_id": _PAGE_ID,
        "expected_revision": _REVISION,
        "payload": {
            "to_path": "reference/moved-example.md",
            "to_nav_path": "Reference/Moved example",
            "create_redirect": True,
        },
    },
    "REPARENT_NAV": {
        "operation_type": "REPARENT_NAV",
        "page_resource_id": _PAGE_ID,
        "expected_revision": _REVISION,
        "payload": {"to_nav_path": "Operations/Examples/Example"},
    },
    "ARCHIVE_PAGE": {
        "operation_type": "ARCHIVE_PAGE",
        "page_resource_id": _PAGE_ID,
        "expected_revision": _REVISION,
        "payload": {},
    },
    "RESTORE_PAGE": {
        "operation_type": "RESTORE_PAGE",
        "page_resource_id": _PAGE_ID,
        "expected_revision": _REVISION,
        "payload": {},
    },
    "ADD_REDIRECT": {
        "operation_type": "ADD_REDIRECT",
        "payload": {"from_path": "reference/old-example.md", "to_path": "reference/example.md"},
    },
    "REMOVE_REDIRECT": {
        "operation_type": "REMOVE_REDIRECT",
        "payload": {"from_path": "reference/old-example.md"},
    },
    "REORDER_SECTIONS": {
        "operation_type": "REORDER_SECTIONS",
        "payload": {"order": ["Reference", "Operations", "Architecture"]},
    },
}


def _schema_name(operation_type: str) -> str:
    return "".join(part.title() for part in operation_type.lower().split("_")) + "Payload"


def _binding_contract(operation_type: str) -> dict[str, str]:
    if operation_type in _UNBOUND_OPERATIONS:
        return {
            "page_resource_id": "omit",
            "expected_revision": "omit",
            "expected_section_hash": "omit",
        }
    return {
        "page_resource_id": "required",
        "expected_revision": "required",
        "expected_section_hash": "required" if operation_type in _SECTION_OPERATIONS else "omit",
    }


def _contracts() -> dict[str, dict[str, Any]]:
    runtime = set(operation_types())
    documented = set(_PAYLOAD_SCHEMAS)
    examples = set(_REQUEST_EXAMPLES)
    if runtime != documented or runtime != examples:
        raise RuntimeError(
            "operation contract drift: "
            f"runtime_only={sorted(runtime - documented)} "
            f"schema_only={sorted(documented - runtime)} "
            f"missing_examples={sorted(runtime - examples)}"
        )
    return {
        operation_type: {
            "binding": _binding_contract(operation_type),
            "payload_schema": copy.deepcopy(_PAYLOAD_SCHEMAS[operation_type]),
            "request_example": copy.deepcopy(_REQUEST_EXAMPLES[operation_type]),
        }
        for operation_type in sorted(runtime)
    }


@router.get(CONTRACT_ENDPOINT)
def operation_contracts() -> dict[str, Any]:
    """Return the complete unauthenticated operation request contract."""
    return {
        "contract_version": CONTRACT_VERSION,
        "operation_endpoint": OPERATION_ENDPOINT,
        "required_headers": ["Authorization", "Idempotency-Key"],
        "rule": "Select the operation entry by operation_type; do not infer payload fields from another operation.",
        "operations": _contracts(),
    }


def install_operation_contract(app: Any) -> None:
    """Augment generated OpenAPI with payload schemas and full request examples."""
    upstream_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        schema = upstream_openapi()
        if schema.get("x-docplane-operation-contract-version") == CONTRACT_VERSION:
            return schema

        contracts = _contracts()
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        mapping: dict[str, dict[str, Any]] = {}
        payload_refs: list[dict[str, str]] = []
        for operation_type, contract in contracts.items():
            name = _schema_name(operation_type)
            payload_schema = copy.deepcopy(contract["payload_schema"])
            payload_schema["title"] = name
            components[name] = payload_schema
            ref = f"#/components/schemas/{name}"
            payload_refs.append({"$ref": ref})
            mapping[operation_type] = {
                "payload_schema": ref,
                "binding": contract["binding"],
            }

        request_model = components.get("ChangeOperationCreate")
        if isinstance(request_model, dict):
            properties = request_model.setdefault("properties", {})
            properties["payload"] = {
                "oneOf": payload_refs,
                "description": (
                    "Payload schema is selected by the sibling operation_type. "
                    f"The exact mapping and complete requests are also published at {CONTRACT_ENDPOINT}."
                ),
            }
            request_model["x-docplane-operation-contracts"] = mapping

        operation = schema.get("paths", {}).get(OPERATION_ENDPOINT, {}).get("post")
        if isinstance(operation, dict):
            operation["x-docplane-operation-contracts"] = mapping
            request_body = operation.get("requestBody", {})
            media = request_body.get("content", {}).get("application/json", {})
            media["examples"] = {
                operation_type: {
                    "summary": f"{operation_type} request",
                    "value": contract["request_example"],
                }
                for operation_type, contract in contracts.items()
            }
            description = operation.get("description", "").rstrip()
            addition = (
                f"Machine-readable payload schemas and complete examples for every operation_type "
                f"are available at {CONTRACT_ENDPOINT}."
            )
            operation["description"] = f"{description}\n\n{addition}" if description else addition

        schema["x-docplane-operation-contract-version"] = CONTRACT_VERSION
        schema["x-docplane-operation-contracts"] = {
            "endpoint": CONTRACT_ENDPOINT,
            "operation_endpoint": OPERATION_ENDPOINT,
            "mapping": mapping,
        }
        schema["x-docplane-caller-safety"] = {
            "credential_issuance": (
                "DocPlane private-fabric mode does not require server-side human approval, but a caller's own "
                "agent framework may classify token issuance as sensitive and require confirmation. Verify the "
                "routed endpoint, requested principal kind, role and expiry before approving that caller-side gate."
            )
        }
        return schema

    app.openapi = custom_openapi
