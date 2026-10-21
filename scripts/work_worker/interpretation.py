"""Validate the librarian's interpretation output (librarian-interp-2). Stdlib only.

Structural checks use a small JSON Schema subset (the keywords the schema file uses);
semantic checks enforce the contract rules a schema cannot: what TRACKED means, that
active coverage is a separate judgement, retraction versus contradiction, id-prefix
resolution, and that nothing is built on a record that was not read.
"""
from __future__ import annotations

import json
import os
import re

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "interpretation.schema.json")
READ_NOW = {"WORK_STATE_READ_NOW", "DOCPLANE_PAGE_READ_NOW", "MODEL_READ_NOW"}
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def load_schema():
    with open(SCHEMA_PATH) as handle:
        return json.load(handle)


def _is(value, name):
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name]) and not (name != "boolean" and isinstance(value, bool))


def structural_errors(value, schema, root, path="$"):
    """Errors for the schema keywords in use; unknown keywords are ignored (descriptions)."""
    if "$ref" in schema:
        node = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        return structural_errors(value, node, root, path)
    if "anyOf" in schema:
        if all(structural_errors(value, sub, root, path) for sub in schema["anyOf"]):
            return [f"{path}: matches none of the allowed shapes"]
        return []
    errors = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: not an allowed value")
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is(value, n) for n in names):
            return errors + [f"{path}: wrong type"]
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: too long")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match the allowed pattern")
    if isinstance(value, int) and not isinstance(value, bool) and "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{path}: below minimum")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key}")
        for key, sub in value.items():
            if key in props:
                errors += structural_errors(sub, props[key], root, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected field {key}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: too few items")
        if schema.get("uniqueItems") and len({json.dumps(x, sort_keys=True) for x in value}) != len(value):
            errors.append(f"{path}: items must be unique")
        if "items" in schema:
            for i, sub in enumerate(value):
                errors += structural_errors(sub, schema["items"], root, f"{path}[{i}]")
    return errors


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def _best_coverage(targets):
    judgments = [t["coverage"]["judgment"] for t in targets]
    for name in ("ACTIVELY_COVERS", "UNKNOWN", "DOES_NOT_ACTIVELY_COVER"):
        if name in judgments:
            return name
    return "NO_TARGET"


def validate(doc):
    schema = load_schema()
    errors = structural_errors(doc, schema, schema)
    if errors:
        return errors
    err = errors.append
    if any(len(s.split()) > 30 for s in _strings(doc)):
        err("text over 30 words")
    reads = {r["record_id"]: r["read_ok"] for r in doc["retrieval"]["reads"]}
    registers = {r["register_id"] for r in doc["run"]["scope"]["authoritative_registers"]}

    def read(rid, where):
        if not reads.get(rid):
            err(f"{where}: record {rid} was not read successfully this run")

    parts, obligations = {}, {}
    for rec in doc["records"]:
        read(rec["record_id"], "record")
        for dep in rec["depends_on"]:
            read(dep["record_id"], "depends_on")
        for p in rec["parts"]:
            parts[(rec["record_id"], p["part_id"])] = p
            if (p["role"] == "obligation") != (p["obligation"] is not None):
                err(f"part {p['part_id']}: role obligation iff obligation present")
            c = p["confirmation"]
            if c["basis"] in READ_NOW and not c["read_at"]:
                err(f"part {p['part_id']}: a read-now basis needs read_at")
            if c["basis"] == "EXTERNAL_UNVERIFIED" and not c.get("verifier_needed"):
                err(f"part {p['part_id']}: external claim needs verifier_needed")
            o = p["obligation"]
            if o:
                _check_obligation(o, rec["record_id"], obligations, registers, read, err)
    for m in doc["mappings"]:
        for n in m["new_local_ids"]:
            if n not in obligations:
                err(f"mapping names unknown obligation {n}")
    refs = doc["unresolved_references"]
    for i, r in enumerate(refs):
        unique = r["outcome"] == "UNIQUE_CONFIRMED"
        if unique and not (r["candidate_count"] == 1 and r["resolved_record_id"] and r["confirmed_by_full_read"] and reads.get(r["resolved_record_id"])):
            err(f"reference {i}: unique resolution needs one candidate, a full-id read and confirmation")
        if not unique and r["resolved_record_id"]:
            err(f"reference {i}: only a unique confirmed prefix may resolve")
    rel = {}
    for r in doc["relations"]:
        rel[r["relation_id"]] = r
        for side in ("from", "to"):
            read(r[side]["record_id"], "relation")
            if r[side]["part_id"] and (r[side]["record_id"], r[side]["part_id"]) not in parts:
                err(f"relation {r['relation_id']}: unknown part")
        if r["type"] == "CORRECTS_RETRACTS" and (r["basis"] not in ("EXPLICIT_STATEMENT", "STRUCTURAL") or not (r["from"]["part_id"] and r["to"]["part_id"])):
            err(f"relation {r['relation_id']}: a retraction needs an explicit statement and named parts on both sides")
        if r["type"] == "CONTRADICTS_UNRESOLVED" and not (r["from"]["part_id"] and r["to"]["part_id"]):
            err(f"relation {r['relation_id']}: a contradiction names the conflicting parts")
        via = r["via_reference"]
        if via is not None and (via >= len(refs) or refs[via]["outcome"] != "UNIQUE_CONFIRMED"):
            err(f"relation {r['relation_id']}: built on an unresolved or ambiguous id prefix")
        if r["basis"] == "CITED_ID" and via is None and not (reads.get(r["to"]["record_id"]) and reads.get(r["from"]["record_id"])):
            err(f"relation {r['relation_id']}: cited id never confirmed by a full-id read")
    b = doc["briefing"]
    for rid in b["retractions"]:
        if rel.get(rid, {}).get("type") != "CORRECTS_RETRACTS":
            err(f"briefing retraction {rid} is not a CORRECTS_RETRACTS relation")
    for rid in b["unresolved_contradictions"]:
        if rel.get(rid, {}).get("type") != "CONTRADICTS_UNRESOLVED":
            err(f"briefing contradiction {rid} is not a CONTRADICTS_UNRESOLVED relation")
    for ob in b["open_obligations"]:
        got = obligations.get(ob["local_id"])
        if not got or got[0] != ob["original_record"]:
            err(f"briefing obligation {ob['local_id']} does not match an extracted obligation of that record")
        elif (got[1]["tracking"]["status"] != ob["tracking_status"] or got[1]["state"] not in ("OPEN", "UNCLEAR")
              or _best_coverage(got[1]["tracking"]["targets"]) != ob["coverage"]):
            err(f"briefing obligation {ob['local_id']} disagrees with its extraction")
    for s in b["status_lines"]:
        c = s["confirmation"]
        if c["basis"] in READ_NOW and not c["read_at"]:
            err("status line: a read-now basis needs read_at")
        if c["basis"] == "EXTERNAL_UNVERIFIED" and not c.get("verifier_needed"):
            err("status line: external claim needs verifier_needed")
    if (doc["retrieval"]["budget_exhausted"] or not all(reads.values())) and not doc["incomplete"]:
        err("incomplete must be true when a read failed or the budget ran out")
    if doc["incomplete"] and b["ready_to_close"] == "yes":
        err("an incomplete run cannot say ready_to_close yes")
    return errors


def _check_obligation(o, record_id, obligations, registers, read, err):
    lid = o["local_id"]
    if lid in obligations:
        err(f"duplicate obligation local_id {lid}")
    obligations[lid] = (record_id, o)
    t = o["tracking"]
    identified = []
    for tg in t["targets"]:
        kind = tg["kind"]
        if kind == "WORK_ITEM":
            if not tg["record_id"]:
                err(f"obligation {lid}: a work-item target needs record_id")
            else:
                read(tg["record_id"], "tracking target")
        elif kind == "AUTHORITATIVE_REGISTER":
            if tg["register_id"] not in registers or not tg["entry_ref"]:
                err(f"obligation {lid}: register target must name a declared authoritative register and an entry")
        elif not tg["record_id"] or tg["register_id"]:
            err(f"obligation {lid}: a record-only target names the restating record and no register")
        else:
            read(tg["record_id"], "restating record")
        cov = tg["coverage"]
        if tg["target_state"] == "ABANDONED" and (cov["judgment"] == "ACTIVELY_COVERS" or cov["reason"] != "ABANDONED"):
            err(f"obligation {lid}: an abandoned target stays visible and does not actively cover")
        if tg["target_state"] == "COMPLETE" and cov["judgment"] == "ACTIVELY_COVERS" and o["state"] in ("OPEN", "UNCLEAR"):
            err(f"obligation {lid}: a completed item does not actively cover an obligation still open")
        if kind != "RECORD_ONLY" and tg["basis"] != "SEMANTIC" and tg["objective_stated"] and tg["disposition_stated"] != "NONE":
            identified.append(tg)
    if t["status"] == "TRACKED" and not identified:
        err(f"obligation {lid}: TRACKED needs an identifiable work item or register entry that states an objective and disposition")
    if t["status"] in ("TRACKED", "CANDIDATE", "MENTIONED_ONLY") and not t["targets"]:
        err(f"obligation {lid}: {t['status']} needs a target")
