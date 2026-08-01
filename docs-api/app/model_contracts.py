"""Per-kind card checklists and attribute safety for the model domain.

Card kinds are DATA, not schema: the entity table accepts any kind in its
vocabulary, and this module decides what a valid card of each kind looks
like. Checklists were harvested from a real deployment, not invented — the survey
method is docs/architecture/CARD_TYPE_HARVEST.md. A kind is `ratified` once
several real instances filled its required fields; unratified kinds accept
any attributes (still secret-scanned) until they earn a checklist.

Secret safety is two-layer and fail-closed, and this module REJECTS, never
rewrites:

1. Canonical shape detection — the single sanitisation transform
   (`migration.redaction.redact`, policy issue #60) runs over the serialised
   attributes; any change or refusal it would make means the payload carries
   a secret-shaped value and the write is refused. Detection is never
   re-implemented here.
2. Key-name guard — an attribute whose key names a credential (password,
   token, api_key, …) must carry a placeholder value, not a real one. The
   harvest records field NAMES; values live in the owning secret authority.

Consequence of layer 1: long bare hex digests (40+ chars) are treated as
secret-shaped. Fingerprints and config hashes belong in their dedicated
columns, not in card attributes.
"""
from __future__ import annotations

import json
import re
from typing import Any

from migration.redaction import DocumentRefusedError, MalformedMarkerError, redact

_SECRET_KEY_RE = re.compile(r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|credential|client[_-]?secret)", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"^\s*(\{\{[^{}]+\}\}|<[^<>]+>|\*{3,}|•{3,})?\s*$")

# field name -> JSON Schema fragment. Kept deliberately permissive on types:
# the checklist enforces presence and rough shape, not exhaustive typing.
_STR = {"type": "string", "minLength": 1}
_STR_OR_INT = {"type": ["string", "integer"]}
_STR_LIST = {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1}

CARD_CONTRACTS: dict[str, dict[str, Any]] = {
    "NODE": {
        "ratified": True,
        "required": {"cpu": _STR, "memory": _STR, "os": _STR},
        "optional": {
            "kernel": _STR, "chassis_model": _STR, "motherboard": _STR,
            "bios_version": _STR, "boot_mode": _STR, "service_tag": _STR,
            "tpm": _STR, "secureboot": _STR,
            "storage": {"type": "array", "items": {"type": "object"}},
        },
    },
    "VM": {
        "ratified": True,
        "required": {"vmid": _STR_OR_INT, "host_node": _STR, "ip": _STR, "hostname": _STR},
        "optional": {
            "os": _STR, "memory": _STR, "vcpu": _STR_OR_INT, "storage": _STR,
            "access": _STR, "passthrough": _STR, "mac": _STR, "lan": _STR,
            "onboot": {"type": ["boolean", "string"]},
        },
    },
    "SITE": {
        "ratified": True,
        # Only lan_subnets met the harvest bar across all nine sites; the rest
        # stay optional so the initial census is not blocked.
        "required": {"lan_subnets": _STR_LIST},
        "optional": {
            "wan_address": _STR, "public_ip": _STR, "gateway": _STR,
            "dhcp_server": _STR, "sdwan_tunnel": _STR, "wireguard_port": _STR_OR_INT,
            "vpn_fqdn": _STR, "nat": _STR, "edge_id": _STR,
        },
    },
    "SERVICE": {
        # Link-heavy by harvest finding: prose rarely states flat fields, the
        # on-fabric pass supplies them. Presence contract stays empty.
        "ratified": True,
        "required": {},
        "optional": {
            "image": _STR, "ports": _STR_LIST, "env_keys": _STR_LIST,
            "compose_file": _STR, "systemd_unit": _STR, "host": _STR,
            "lifecycle": _STR,
        },
    },
    "DATABASE": {
        "ratified": False,
        "required": {},
        "optional": {"engine": _STR, "engine_version": _STR, "port": _STR_OR_INT},
    },
    "SCHEMA": {
        "ratified": False,
        "required": {},
        "optional": {"database": _STR, "table_count": _STR_OR_INT},
    },
    "MONITOR_RULE": {
        # Sprint 6 meter list: one entity per Prometheus alerting/recording
        # rule, WATCHES-wired to the service it observes. Attributes mirror
        # the rule file, harvested by the importer — not typed by hand.
        "ratified": False,
        "required": {},
        "optional": {
            "rule_kind": _STR, "expr": _STR, "severity": _STR,
            "pending_for": _STR, "source_file": _STR, "group": _STR,
            "has_description": {"type": "boolean"},
            "runbook_url": _STR,
        },
    },
    "DEVICE_MODEL": {
        # Exactly at the three-instance threshold in the corpus harvest.
        "ratified": False,
        "required": {},
        "optional": {
            "model": _STR, "protocol": _STR, "device_class": _STR,
            "uplink_fport": _STR_OR_INT, "downlink_fport": _STR_OR_INT,
            "deveui_prefix": _STR, "joineui": _STR, "battery": _STR,
        },
    },
    "NETWORK": {
        "ratified": False,
        "required": {},
        "optional": {"cidr": _STR, "trust_class": _STR, "vlan": _STR_OR_INT, "site": _STR},
    },
    "SYSTEM": {"ratified": False, "required": {}, "optional": {}},
    "API": {"ratified": False, "required": {}, "optional": {}},
    "ROUTE": {"ratified": False, "required": {}, "optional": {"hostname": _STR, "upstream": _STR, "tls": _STR}},
    "INTERFACE": {"ratified": False, "required": {}, "optional": {}},
    "ARTIFACT": {"ratified": False, "required": {}, "optional": {}},
}

ENTITY_KINDS: tuple[str, ...] = tuple(sorted(CARD_CONTRACTS))

CONTRACT_VERSION = "model-contracts-v1"


def kind_schema(kind: str) -> dict[str, Any]:
    """The published JSON Schema for one kind's attributes."""
    contract = CARD_CONTRACTS[kind]
    return {
        "type": "object",
        "properties": {**contract["required"], **contract["optional"]},
        "required": sorted(contract["required"]),
        # Open cards: fields beyond the checklist are allowed, the checklist
        # enforces presence of what every real instance carries.
        "additionalProperties": True,
    }


def contracts_document() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "kinds": {
            kind: {
                "ratified": CARD_CONTRACTS[kind]["ratified"],
                "attributes_schema": kind_schema(kind),
            }
            for kind in ENTITY_KINDS
        },
        "provenance": "docs/architecture/CARD_TYPE_HARVEST.md",
    }


def checklist_errors(kind: str, attributes: dict[str, Any]) -> list[dict[str, Any]]:
    """Presence/shape errors for a ratified kind's checklist."""
    contract = CARD_CONTRACTS[kind]
    if not contract["ratified"]:
        return []
    errors: list[dict[str, Any]] = []
    for field, schema in contract["required"].items():
        if field not in attributes:
            errors.append({"field": field, "error": "REQUIRED_FIELD_MISSING"})
            continue
        errors.extend(_shape_errors(field, attributes[field], schema))
    for field, schema in contract["optional"].items():
        if field in attributes:
            errors.extend(_shape_errors(field, attributes[field], schema))
    return errors


def _shape_errors(field: str, value: Any, schema: dict[str, Any]) -> list[dict[str, Any]]:
    kinds = schema.get("type")
    kinds = [kinds] if isinstance(kinds, str) else list(kinds or [])
    checks = {
        "string": lambda v: isinstance(v, str) and (schema.get("minLength", 0) == 0 or len(v) >= schema["minLength"]),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "array": lambda v: isinstance(v, list) and len(v) >= schema.get("minItems", 0),
        "object": lambda v: isinstance(v, dict),
    }
    if kinds and not any(checks.get(kind, lambda v: True)(value) for kind in kinds):
        return [{"field": field, "error": "FIELD_SHAPE_INVALID", "expected": kinds}]
    if isinstance(value, list) and schema.get("items", {}).get("type") == "string":
        if not all(isinstance(item, str) and item for item in value):
            return [{"field": field, "error": "FIELD_SHAPE_INVALID", "expected": ["array of strings"]}]
    return []


def secret_findings(attributes: dict[str, Any]) -> list[dict[str, Any]]:
    """Fail-closed secret scan; returns findings, never values."""
    findings: list[dict[str, Any]] = []
    _scan_keys(attributes, "", findings)
    serialised = json.dumps(attributes, sort_keys=True, ensure_ascii=False, default=str)
    try:
        result = redact(serialised, label="MODEL-ATTRS")
    except MalformedMarkerError:
        findings.append({"path": "$", "code": "MALFORMED_REDACTION_MARKER"})
        return findings
    except DocumentRefusedError:
        findings.append({"path": "$", "code": "SECRET_SHAPED_VALUE", "classes": ["REFUSED"]})
        return findings
    if result.changed:
        findings.append({"path": "$", "code": "SECRET_SHAPED_VALUE", "classes": sorted(set(result.findings))})
    return findings


def _scan_keys(value: Any, path: str, findings: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else key
            if _SECRET_KEY_RE.search(str(key)) and isinstance(item, str) and not _PLACEHOLDER_RE.fullmatch(item):
                findings.append({"path": child, "code": "SECRET_NAMED_KEY_WITH_VALUE"})
            _scan_keys(item, child, findings)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_keys(item, f"{path}[{index}]", findings)
