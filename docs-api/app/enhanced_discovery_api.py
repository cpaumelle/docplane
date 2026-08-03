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
    document["contract_version"] = "docplane-agent-discovery-v4"
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
    document["surfaces"].update({
        "single_page_patch": "/api/v1/pages/{resource_id}/patch",
        "page_outline": "/api/v1/pages/{resource_id}?view=outline",
        "page_section": "/api/v1/pages/{resource_id}/sections/{heading_id}",
        "page_history": "/api/v1/pages/{resource_id}/history",
        "page_trust": "/api/v1/pages/{resource_id}/trust",
        "corpus_structure": "/api/v1/dashboard/structure",
        "observatory_export": "/api/v1/dashboard/observatory/export",
    })

    authentication = document.setdefault("authentication", {})
    acquisition = authentication.setdefault("token_acquisition", {})
    acquisition["caller_policy_note"] = _CALLER_POLICY_NOTE

    quick_start = document.setdefault("quick_start", {})
    quick_start["bounded_read"] = [
        "Resolve the page, then GET /api/v1/pages/{resource_id}?view=outline.",
        "GET /api/v1/pages/{resource_id}/sections/{heading_id} for only the exact section needed.",
        "Retain the returned revision for the subsequent mutation.",
    ]
    quick_start["small_exact_patch"] = [
        "Read the current outline, section or page and retain its revision.",
        "POST /api/v1/pages/{resource_id}/patch with expected_revision, purpose and exact old_text/new_text edits.",
        "Each old_text must occur exactly expected_occurrences times; ambiguity or staleness fails closed.",
        "Treat HTTP 409 PAGE_REVISION_STALE as a required rebase, never as success.",
    ]
    multi_operation = list(quick_start.get("multi_operation_change") or [])
    contract_step = f"GET {CONTRACT_ENDPOINT} and select the request contract by operation_type"
    if contract_step not in multi_operation:
        multi_operation.insert(0, contract_step)
    quick_start["multi_operation_change"] = multi_operation
    document["document_metadata"] = {
        "contract_version": "docplane-document-metadata-v1",
        "identity_and_location": {
            "resource_id": "Stable UUID identity; use it across path and section moves.",
            "uri": "Stable docplane:// URI derived from resource_id.",
            "path": "Canonical Markdown path and public URL input.",
            "title": "Human-readable document title.",
            "nav_path": "Ordered navigation hierarchy used for browsing and categorisation.",
        },
        "corpus_and_category": {
            "workspace_id": "Stable workspace classification UUID.",
            "workspace_key": "Human-readable workspace classification key.",
            "knowledge_class": {
                "description": "Document intent/category.",
                "values": ["ARCHITECTURE", "OPERATION", "REFERENCE", "POLICY", "DECISION", "EVIDENCE", "DESIGN", "WORK_NOTE"],
            },
            "criticality": {
                "description": "Operational importance and review sensitivity.",
                "values": ["NORMAL", "IMPORTANT", "OPERATIONAL_CRITICAL", "POLICY_REQUIRED"],
            },
        },
        "content_discovery": {
            "summary": "Bounded first-content summary for search and triage.",
            "outline": "Heading IDs, levels, line bounds, explicit-ID flags and section content hashes.",
            "content": "Full authoritative Markdown, available only from full/edit_context reads.",
        },
        "revision_and_history": {
            "revision": "Immutable content revision used for optimistic concurrency.",
            "version": "Monotonic page version.",
            "updated_at": "Last authoritative mutation timestamp.",
            "updated_by": "Named principal responsible for the current revision.",
            "history": "/api/v1/pages/{resource_id}/history",
        },
        "lifecycle_and_publication": {
            "status": {"values": ["active", "archived"]},
            "publication_state": {"values": ["DRAFT", "PUBLISHED", "ARCHIVED"]},
            "provenance": {"description": "Whether content is authored or generated.", "values": ["AUTHORED", "GENERATED"]},
        },
        "trust_and_maintenance": {
            "verification_state": {"values": ["UNVERIFIED", "VERIFIED", "OUTDATED", "EXPIRED"]},
            "owner_principal_id": "Accountable owner principal, when assigned.",
            "review_due_at": "Next expected review time, when assigned.",
            "metadata_review_required": "Signals incomplete or stale classification metadata.",
            "metadata_version": "Version of the metadata classification contract applied.",
            "detail": "/api/v1/pages/{resource_id}/trust",
        },
    }
    document["discoverability_and_categorisation"] = {
        "search": {
            "endpoint": "/api/v1/search?q=<term>",
            "indexed_fields": ["title", "path", "nav_path", "content"],
            "result_context": ["matched_in", "snippet", "summary", "workspace_key", "revision"],
        },
        "resolve": {"endpoint": "/api/v1/resolve", "keys": ["path", "title"]},
        "list_filters": {"endpoint": "/api/v1/pages", "fields": ["status", "path", "workspace_key"]},
        "hierarchy": {"endpoint": "/api/v1/dashboard/structure", "fields": ["workspace_key", "nav_path", "path"]},
        "facets": {
            "endpoint": "/api/v1/dashboard/observatory/export",
            "fields": ["workspace_key", "knowledge_class", "criticality", "verification_state", "publication_state", "status", "provenance"],
        },
        "guidance": "Use stable resource_id for identity, nav_path/workspace for placement, knowledge_class for intent, criticality for impact, and trust metadata for freshness.",
    }
    document["agent_tools"] = {
        "patch_doc": "Preferred for small exact edits; revision-bound and ambiguity-rejecting.",
        "read_doc_outline": "Returns structure and hashes without transferring the full document.",
        "read_doc_section": "Returns one heading-bounded section and the page revision.",
        "write_doc": "Creates a page or replaces the complete page; existing-page replacement requires a caller-supplied expected_revision.",
    }
    return document
