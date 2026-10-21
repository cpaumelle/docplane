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
    uncertain = {(rid, p["part_id"]) for (rid, _), p in parts.items() if p["obligation"] and p["obligation"]["adjudication_status"] != "NONE"}
    for (rid, _), p in parts.items():
        o = p["obligation"]
        if o:
            for ref in o["source_refs"]:
                read(ref["record_id"], "source_refs")
    seen_accept = {}
    for (rid, pid), p in parts.items():
        o = p["obligation"]
        if o:
            key = (rid, " ".join(o["acceptance"].lower().split()))
            if key in seen_accept:
                err(f"obligations {seen_accept[key]} and {o['local_id']} share an acceptance condition: split only independently acceptable actions, otherwise consolidate")
            seen_accept[key] = o["local_id"]
    ranks = [pr["rank"] for pr in doc["proposals"]]
    if len(ranks) != len(set(ranks)):
        err("proposal ranks must be unique")
    for pr in doc["proposals"]:
        if pr["kind"] in ("FILE", "LINK", "METADATA", "CONSOLIDATE") and any((e["record_id"], e["part_id"]) in uncertain for e in pr["evidence"]):
            err(f"proposal {pr['proposal_id']}: an unsettled or unarbitrated obligation cannot back a filing, linking, metadata or consolidation proposal")
        for e in pr["evidence"]:
            read(e["record_id"], "proposal evidence")
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
    _check_intake(doc, obligations, reads, rel, err, parts)
    _check_organisation(doc, reads, err)
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
    for ref in o["later_evidence"]:
        if "register_id" in ref:
            if ref["register_id"] not in registers:
                err(f"obligation {lid}: later evidence cites a register that is not declared authoritative ({ref['register_id']})")
        else:
            read(ref["record_id"], "later evidence")
    if o["later_evidence"] and o["currency"] == "AS_OF_RECORD":
        err(f"obligation {lid}: later evidence was retrieved, so its status cannot be presented as plainly as-of-record")
    if o["currency"] == "UNCERTAIN_CURRENT" and t["status"] == "TRACKED":
        err(f"obligation {lid}: while the current status is uncertain a home is only a candidate, not TRACKED")
    srcs = {record_id} | {x["record_id"] for x in o["source_refs"]}
    comp = {c["source_record_id"] for c in o["scope_components"]}
    if o["source_refs"] and not srcs <= comp:
        err(f"obligation {lid}: a consolidated obligation must keep each source's own scope (components missing for {len(srcs - comp)} source(s))")
    if not comp <= srcs:
        err(f"obligation {lid}: a scope component names a record that is not a source of this obligation")
    completed = [tg for tg in identified if tg["disposition_stated"] == "DONE" or tg["target_state"] == "COMPLETE"]
    if t["status"] == "TRACKED" and o["state"] in ("OPEN", "UNCLEAR") and identified and len(completed) == len(identified):
        explained = all(tg["coverage"]["judgment"] == "DOES_NOT_ACTIVELY_COVER" and tg["coverage"]["reason"] in ("PARTIAL_SCOPE", "COMPLETED_WITHOUT_THIS") for tg in completed) and bool(o["scope_components"])
        if not explained:
            err(f"obligation {lid}: every tracker states it is done or complete, so it cannot back an open TRACKED claim unless it explains the mismatch "
                "(coverage DOES_NOT_ACTIVELY_COVER with reason PARTIAL_SCOPE or COMPLETED_WITHOUT_THIS, and the part it does not cover stated in scope_components); otherwise the obligation is done or the home is only a candidate")
    if t["status"] == "TRACKED" and not identified:
        err(f"obligation {lid}: TRACKED needs an identifiable work item or register entry that states an objective and disposition")
    if t["status"] in ("TRACKED", "CANDIDATE", "MENTIONED_ONLY") and not t["targets"]:
        err(f"obligation {lid}: {t['status']} needs a target")


def _check_intake(doc, obligations, reads, rel, err, parts):
    """Backlog guardianship: every incoming record is evaluated, visibly, against existing work."""
    incoming = doc["run"]["scope"]["incoming_record_ids"]
    concerns = {}
    for c in doc["concerns"]:
        if c["concern_id"] in concerns:
            err(f"concern {c['concern_id']} defined twice")
        concerns[c["concern_id"]] = c
        if (c["record_id"], c["part_id"]) not in parts:
            err(f"concern {c['concern_id']}: its source evidence part does not exist")
        elif parts[(c["record_id"], c["part_id"])]["obligation"] is not None:
            err(f"concern {c['concern_id']}: a concern points at evidence, not at an extracted obligation")
        if not reads.get(c["record_id"]):
            err(f"concern {c['concern_id']}: source record was not read")
    entries = {}
    for e in doc["intake"]:
        if e["record_id"] in entries:
            err(f"intake: record {e['record_id']} evaluated twice")
        entries[e["record_id"]] = e
    for rid in incoming:
        if rid not in entries:
            err(f"intake: incoming record {rid} has no evaluation")
    for rid, e in entries.items():
        if rid not in incoming and e["processing_status"] != "REEVALUATION_DUE":
            err(f"intake: {rid} is not incoming; only a re-evaluation may be added")
        disp, status = e["disposition"], e["processing_status"]
        if reads.get(rid) is False and status != "READ_FAILED":
            err(f"intake: {rid} failed to read and must say so")
        if status == "READ_FAILED" and (disp != "UNCERTAIN" or reads.get(rid) is not False):
            err(f"intake: {rid} READ_FAILED needs a failed read and an UNCERTAIN disposition")
        if disp == "UNCERTAIN" and e["placement"] != "UNDECIDED":
            err(f"intake: {rid} an uncertain disposition leaves the placement undecided")
        if (disp == "UNCERTAIN") != (status in ("READ_FAILED", "UNCERTAIN_PLACEMENT")):
            err(f"intake: {rid} an uncertain disposition and an uncertain or failed status go together")
        if disp == "UNCERTAIN" and not e["uncertain_reason"]:
            err(f"intake: {rid} uncertain placement must say why")
        if e["reevaluation"]["due"] != (status == "REEVALUATION_DUE" or e["reevaluation"]["trigger"] != "NONE"):
            err(f"intake: {rid} re-evaluation flag and trigger disagree")
        registers = {r["register_id"] for r in doc["run"]["scope"]["authoritative_registers"]}
        for tg in e["targets"]:
            if tg["kind"] == "WORK_RECORD":
                if not tg["record_id"] or tg["register_id"] or tg["entry_ref"]:
                    err(f"intake: {rid} a work-record target names a record and no register row")
                elif not reads.get(tg["record_id"]):
                    err(f"intake: target {tg['record_id']} was not read")
            elif tg["record_id"] or tg["register_id"] not in registers or not tg["entry_ref"] or tg["work_state_seen"] != "NOT_A_WORK_ITEM":
                err(f"intake: {rid} a register-row target needs a declared authoritative register and an entry, and is not a work item")
        purpose = e["purpose"]
        if disp == "ADDS_OBLIGATION" and purpose != "OBLIGATION":
            err(f"intake: {rid} adding an obligation means the record states an action (purpose OBLIGATION)")
        if disp == "CONTRIBUTES_EVIDENCE" and purpose in ("OBLIGATION", "CORRECTION"):
            err(f"intake: {rid} evidence cannot have purpose {purpose}")
        if purpose == "COMPLETED_EVIDENCE":
            live = [l for l in e["obligation_local_ids"] if l in obligations and obligations[l][1]["state"] in ("OPEN", "UNCLEAR")]
            if live:
                err(f"intake: {rid} completed evidence must not manufacture an open obligation ({', '.join(live)})")
        if reads.get(rid) is False and purpose != "UNDETERMINED":
            err(f"intake: {rid} an unread or withheld record has an UNDETERMINED purpose")
        if purpose == "UNDETERMINED" and disp != "UNCERTAIN":
            err(f"intake: {rid} an undetermined purpose goes with an UNCERTAIN disposition")
        s = e["search"]
        others = len({r_ for r_, ok_ in reads.items() if ok_ and r_ != rid})
        if s["captures_inspected"] > others or s["initiatives_inspected"] > others:
            err(f"intake: {rid} claims {s['captures_inspected']} captures and {s['initiatives_inspected']} initiatives inspected but only {others} other records were read in this run")
        know_entries = [x for x in doc["briefing"]["omitted"] if "know" in x["what"].lower()]
        if s["know_access"] != "SEARCHED" and not know_entries:
            err(f"intake: {rid} KNOW was not searched ({s['know_access']}): the briefing must list it as omitted")
        if s["know_access"] != "SEARCHED" and know_entries and not any(len(x["why"].strip()) >= 5 for x in know_entries):
            err(f"intake: {rid} unavailable KNOW access needs a reason in the omitted entry")
        for lid in e["obligation_local_ids"]:
            if lid not in obligations:
                err(f"intake: unknown obligation {lid}")
        same = [tg for tg in e["targets"] if tg["relation"] == "SAME_OBLIGATION"]
        if disp == "DUPLICATES":
            consolidated = [lid for lid in e["obligation_local_ids"] if any(r["record_id"] == rid for r in obligations[lid][1]["source_refs"])] if all(l in obligations for l in e["obligation_local_ids"]) else []
            if not same or not consolidated:
                err(f"intake: {rid} a duplicate needs a same-obligation target and a consolidated obligation that keeps this record as a source")
            for lid in consolidated:
                home = obligations[lid][0]
                if home in incoming and home != rid and not any(r["type"] == "DUPLICATES" and {r["from"]["record_id"], r["to"]["record_id"]} == {rid, home} for r in rel.values()):
                    err(f"intake: {rid} consolidated into an obligation of another incoming record {home}: a DUPLICATES relation between them is required")
        elif [tg for tg in same if not (disp == "CONTRIBUTES_EVIDENCE" and tg["kind"] == "REGISTER_ROW") and disp != "ADDS_OBLIGATION"]:
            err(f"intake: {rid} names a same-obligation target but is not a duplicate")
        if disp == "ADDS_OBLIGATION":
            ids = [l for l in e["obligation_local_ids"] if l in obligations]
            own = [l for l in ids if obligations[l][0] == rid]
            merged = [l for l in ids if obligations[l][0] != rid and any(x["record_id"] == rid for x in obligations[l][1]["source_refs"])]
            if not own or len(own) + len(merged) != len(ids) or not any(tg["relation"] == "RELATED" for tg in e["targets"]):
                err(f"intake: {rid} adding an obligation needs its own new obligation and a related existing work item (other obligations here must be consolidated ones that keep this record as a source)")
            homes = {obligations[l][0] for l in merged}
            for tg in same:
                if tg["record_id"] not in homes:
                    err(f"intake: {rid} names a same-obligation target that is not the home of an obligation consolidated here")
        if disp == "CONTRIBUTES_EVIDENCE" and not e["targets"] and not (purpose == "COMPLETED_EVIDENCE" and e["placement"] == "NO_HOME"):
            err(f"intake: {rid} evidence must name what it contributes to (a work record or a register row), unless it is completed evidence with no home")
        for cid in e["concern_ids"]:
            c = concerns.get(cid)
            if not c or c["record_id"] != rid:
                err(f"intake: {rid} names concern {cid} that is not an explicit concern raised by this record")
        if disp == "WARRANTS_NEW_DELIVERABLE":
            if not (e["obligation_local_ids"] or e["concern_ids"]):
                err(f"intake: {rid} a new deliverable must reference an extracted obligation or an explicit unresolved concern with source evidence")
            if e["placement"] != "NO_HOME":
                err(f"intake: {rid} a new deliverable means no existing home (placement NO_HOME)")
            s = e["search"]
            if not (s["terms"] >= 1 and s["beyond_workstream"] and s["included_completed"]):
                err(f"intake: {rid} a new deliverable needs a search beyond the workstream that included completed work")
        if disp in ("CORRECTS", "SUPERSEDES"):
            want = "CORRECTS_RETRACTS" if disp == "CORRECTS" else "SUPERSEDES"
            if not any(r["type"] == want and r["from"]["record_id"] == rid for r in rel.values()):
                err(f"intake: {rid} {disp.lower()} needs a matching relation from this record")
    c = doc["coverage"]
    counts = {"incoming_total": len(incoming),
              "evaluated": sum(1 for r in incoming if entries.get(r, {}).get("processing_status") == "EVALUATED"),
              "uncertain_placement": sum(1 for r in incoming if entries.get(r, {}).get("processing_status") == "UNCERTAIN_PLACEMENT"),
              "read_failed": sum(1 for r in incoming if entries.get(r, {}).get("processing_status") == "READ_FAILED"),
              "reevaluation_due": sum(1 for r in incoming if entries.get(r, {}).get("processing_status") == "REEVALUATION_DUE")}
    if c != counts:
        err(f"coverage summary {c} does not match the intake entries {counts}")


def continuity_errors(previous, new, registry):
    """Between two interpretations: no obligation lost or duplicated, no source dropped.

    ``registry`` maps each previous local_id to the id minted once for it.
    """
    def obs(doc):
        return {p["obligation"]["local_id"]: (r["record_id"], p["obligation"]) for r in doc["records"] for p in r["parts"] if p["obligation"]}
    before, after = obs(previous), obs(new)
    errors = []
    for lid, (rid, o) in before.items():
        if lid not in after:
            errors.append(f"obligation {lid} was lost")
            continue
        if after[lid][1]["maps_to"] != registry[lid]:
            errors.append(f"obligation {lid} lost its minted id")
        missing = {(x["record_id"], x["part_id"]) for x in o["source_refs"]} - {(x["record_id"], x["part_id"]) for x in after[lid][1]["source_refs"]}
        if missing:
            errors.append(f"obligation {lid} dropped sources")
    for lid, (rid, o) in before.items():
        if lid in after and o["work_kind"] == "RECOVERY" and o["state"] in ("OPEN", "UNCLEAR") and after[lid][1]["state"] in ("DONE_IN_RECORD", "SUPERSEDED", "WITHDRAWN"):
            for lid2, (rid2, o2) in before.items():
                if rid2 == rid and o2["work_kind"] == "PREVENTION" and lid2 in after and after[lid2][1]["state"] != o2["state"] and not after[lid2][1]["later_evidence"]:
                    errors.append(f"prevention obligation {lid2} changed state because recovery {lid} finished; prevention needs its own evidence")
    seen = {}
    for lid, (rid, o) in after.items():
        key = " ".join(o["acceptance"].lower().split())
        if key in seen:
            errors.append(f"obligations {seen[key]} and {lid} share an acceptance condition (duplicate)")
        seen[key] = lid
    return errors


def _check_organisation(doc, reads, err):
    """Proposed organisation stays separate from canonical facts, and a cross-reference is not a rehoming."""
    mine = set(doc["run"]["scope"]["librarian_authored_record_ids"])
    for rid in mine:
        if not reads.get(rid):
            err(f"librarian-authored record {rid} was not read")
    for s in doc["briefing"]["status_lines"]:
        if all(e["record_id"] in mine for e in s["evidence"]):
            err("status line rests only on records the librarian wrote: proposed organisation cannot establish a canonical fact")
    for r in doc["relations"]:
        if all(e["record_id"] in mine for e in r["evidence"]):
            err(f"relation {r['relation_id']} rests only on records the librarian wrote")
    placement_of = {e["record_id"]: e["placement"] for e in doc["intake"]}
    entries = {e["record_id"]: e for e in doc["intake"]}
    new_work_tools = {"docplane_work_capture", "docplane_work_triage_promote", "docplane_work_triage_discard"}
    for pr in doc["proposals"]:
        if pr["provisional"] != (pr["provisional_reason"] is not None):
            err(f"proposal {pr['proposal_id']}: a provisional proposal states why, and only a provisional one does")
        for sid in pr["subjects"]:
            if not reads.get(sid):
                err(f"proposal {pr['proposal_id']}: subject {sid} was not read in this run")
        creates = (pr["kind"] == "FILE" and pr["effect"] == "RECORD_NEW") or pr["kind"] == "CONSOLIDATE" or pr["tool_name"] in new_work_tools
        if (creates or pr["kind"] in ("FILE", "LINK", "CONSOLIDATE")) and not pr["subjects"]:
            err(f"proposal {pr['proposal_id']}: filing, linking, consolidating or creating work names its subject records")
        used = list(dict.fromkeys(list(pr["subjects"]) + [e["record_id"] for e in pr["evidence"]]))
        if creates and not pr["provisional"]:
            backing = [entries[i] for i in used if i in entries] or list(entries.values())
            if not backing or any(x["search"]["captures_inspected"] < 1 or x["search"]["initiatives_inspected"] < 1 for x in backing):
                err(f"proposal {pr['proposal_id']}: new work or duplicate disposal needs relevant captures and initiatives inspected first; otherwise it is provisional")
    pids = {pr["proposal_id"]: pr for pr in doc["proposals"]}
    shaky_parts = {(r["record_id"], p["part_id"]) for r in doc["records"] for p in r["parts"] if p["obligation"] and p["obligation"]["currency"] == "UNCERTAIN_CURRENT"}
    shaky_records = {rid for rid, _ in shaky_parts}
    unsure = {e["record_id"] for e in doc["intake"] if e["disposition"] == "UNCERTAIN"}
    for pr in doc["proposals"]:
        hb = pr["held_behind"]
        if hb is not None and (hb == pr["proposal_id"] or pids.get(hb, {}).get("effect") != "VERIFY"):
            err(f"proposal {pr['proposal_id']}: it can only be held behind a verification proposal that exists")
        files = pr["kind"] in ("FILE", "LINK", "CONSOLIDATE") or pr["tool_name"] in new_work_tools
        subj = set(pr["subjects"]) | {e["record_id"] for e in pr["evidence"]}
        rests = any((e["record_id"], e["part_id"]) in shaky_parts for e in pr["evidence"]) or bool(set(pr["subjects"]) & (shaky_records | unsure)) or bool(subj & unsure)
        if files and rests and not (hb is not None and pr["provisional"]):
            err(f"proposal {pr['proposal_id']}: it rests on an uncertain current status, so it must be provisional and held behind a verification proposal")
    for pr in doc["proposals"]:
        eff, kind = pr["effect"], pr["kind"]
        if kind == "FILE" and eff not in ("REHOME", "RECORD_NEW"):
            err(f"proposal {pr['proposal_id']}: FILE means rehoming or a new record")
        if kind == "VERIFY" and eff != "VERIFY":
            err(f"proposal {pr['proposal_id']}: a verification proposal has effect VERIFY")
        if eff == "REHOME":
            homes = [placement_of[i] for i in pr["subjects"] if i in placement_of]
            if not homes or any(h != "HOME_FOUND" for h in homes):
                err(f"proposal {pr['proposal_id']}: rehoming is not supported where the disposition is only a cross-reference or has no found home")
        if eff == "CROSS_REFERENCE_ONLY" and kind in ("FILE", "CONSOLIDATE"):
            err(f"proposal {pr['proposal_id']}: a cross-reference is not an attachment")
    b = doc["briefing"]
    incomplete = doc["incomplete"] or bool(doc["retrieval"]["not_retrieved"]) or doc["retrieval"]["budget_exhausted"]
    if incomplete and (b["completeness"] != "INCOMPLETE" or not b["omitted"]):
        err("the briefing must say INCOMPLETE and list what was omitted whenever the run is incomplete or anything was not retrieved")
    if b["completeness"] == "COMPLETE" and (incomplete or b["omitted"]):
        err("a briefing cannot be COMPLETE while anything is omitted")
