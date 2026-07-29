"""Extend the cold-start document with exact operation and caller-safety guidance."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.agent_contract_api import discovery as base_discovery
from app.operation_contract_api import CONTRACT_ENDPOINT, CONTRACT_VERSION, OPERATION_ENDPOINT

router = APIRouter(tags=["docplane-contract"])
DISCOVERY_PATH = "/.well-known/docplane.json"

_CALLER_POLICY_NOTE = (
    "DocPlane private-fabric mode requires no server-side bootstrap secret or human approval, "
    "but the caller's own agent framework may classify credential issuance as sensitive and "
    "require confirmation. Before approving that caller-side gate, verify the routed endpoint, "
    "AGENT/CONTRIBUTOR scope and token expiry shown in this document."
)


@router.get(DISCOVERY_PATH)
def discovery() -> dict[str, Any]:
    document = base_discovery()
    document["operation_contracts"] = {
        "contract_version": CONTRACT_VERSION,
        "endpoint": CONTRACT_ENDPOINT,
        "operation_endpoint": OPERATION_ENDPOINT,
        "contains": [
            "binding requirements",
            "per-operation payload JSON Schemas",
            "complete operation request examples",
        ],
    }
    document.setdefault("surfaces", {})["operation_contracts"] = CONTRACT_ENDPOINT

    authentication = document.setdefault("authentication", {})
    acquisition = authentication.setdefault("token_acquisition", {})
    acquisition["caller_policy_note"] = _CALLER_POLICY_NOTE

    quick_start = document.setdefault("quick_start", {})
    multi_operation = list(quick_start.get("multi_operation_change") or [])
    contract_step = f"GET {CONTRACT_ENDPOINT} and select the request contract by operation_type"
    if contract_step not in multi_operation:
        multi_operation.insert(0, contract_step)
    quick_start["multi_operation_change"] = multi_operation
    return document
