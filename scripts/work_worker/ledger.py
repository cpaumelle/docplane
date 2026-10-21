"""Deterministic, names-only ledger for reviewed discrepancy candidates.

No text classification, credentials, network calls or WORK writes live here.
The collector/classifier must provide safe record identities and evidence
versions. A complete scan does not imply that an obligation is completed.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from uuid import UUID

KINDS = frozenset({
    "STATE_CONTRADICTS_EVIDENCE", "UNRECORDED_RESIDUAL",
    "UNACKNOWLEDGED_HANDOVER", "ACCOUNTABILITY_UNCLEAR",
    "EVIDENCE_EXPIRED", "SUPERSEDED_NOT_LINKED", "WRONG_OWNER_DOMAIN",
})


def record_id(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("expected canonical record UUID")
    return value


VERSION = re.compile(r"proj-v1:[0-9a-f]{64}")


def projection_version(**fields):
    """Reviewed evidence version: digest of an allowlisted scalar projection.

    The governed reads expose no revision for captures or single activities and
    only an integer ``version`` for initiatives. Callers pass just the fields
    whose change should reopen review; text bodies are never part of it.
    """
    if not fields or any(v is not None and not isinstance(v, (str, int, bool))
                         for v in fields.values()):
        raise ValueError("projection takes non-empty scalar fields only")
    blob = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return "proj-v1:" + sha256(blob.encode()).hexdigest()


def candidate(value):
    """Accept identities only; never persist arbitrary upstream metadata/text."""
    if set(value) != {"subject_id", "kind", "obligation_id", "evidence"}:
        raise ValueError("unexpected candidate fields")
    record_id(value["subject_id"])
    record_id(value["obligation_id"])
    if value["kind"] not in KINDS:
        raise ValueError("unknown discrepancy kind")
    evidence = value["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("versioned evidence required")
    result = []
    for item in evidence:
        if set(item) != {"record_id", "version"}:
            raise ValueError("versioned evidence identities required")
        if not isinstance(item["version"], str) or not VERSION.fullmatch(item["version"]):
            raise ValueError("evidence version must be a projection digest")
        result.append({"record_id": record_id(item["record_id"]), "version": item["version"]})
    if len({item["record_id"] for item in result}) != len(result):
        raise ValueError("duplicate evidence record")
    return {**value, "evidence": sorted(result, key=lambda x: x["record_id"])}


def identity(value):
    # Obligation identity is assigned once, independently of detector wording.
    return ":".join(value[k] for k in ("subject_id", "kind", "obligation_id"))


def fingerprint(value):
    return sha256(json.dumps(value["evidence"], sort_keys=True).encode()).hexdigest()


def reconcile(previous, candidates, *, evaluated_subjects):
    """Return a new private ledger. Omission never resolves a flag.

    evaluated_subjects contains only successfully, fully evaluated subjects.
    Failed, withheld, truncated or partially read subjects MUST be excluded.
    """
    evaluated = {record_id(x) for x in evaluated_subjects}
    checked = [candidate(x) for x in candidates]
    keys = [identity(x) for x in checked]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate discrepancy identity")
    if any(x["subject_id"] not in evaluated for x in checked):
        raise ValueError("candidate subject was not fully evaluated")
    result = deepcopy(previous)
    for key, row in result.items():
        if row["status"] not in {"OPEN", "REJECTED", "RESOLVED"}:
            raise ValueError("invalid prior status")
        row["detection"] = ("NOT_DETECTED" if row["subject_id"] in evaluated
                            else "NOT_REEVALUATED")
    for value in checked:
        key = identity(value)
        digest = fingerprint(value)
        old = result.get(key)
        status = old["status"] if old else "OPEN"
        if old and old["fingerprint"] != digest:
            status = "OPEN"  # genuinely changed evidence warrants review
        result[key] = {**value, "fingerprint": digest, "status": status,
                       "detection": "DETECTED", "decisions": old["decisions"] if old else []}
    return result


def decide(ledger, key, *, status, actor_id, evidence):
    """Record a review decision by reference; does not change underlying WORK."""
    if status not in {"RESOLVED", "REJECTED"}:
        raise ValueError("review decision must resolve or reject")
    actor_id = record_id(actor_id)
    refs = [record_id(x) for x in evidence]
    if not refs:
        raise ValueError("decision evidence required")
    result = deepcopy(ledger)
    row = result[key]
    row["status"] = status
    row["decisions"].append({"status": status, "actor_id": actor_id,
                             "evidence_ids": refs, "fingerprint": row["fingerprint"]})
    return result
