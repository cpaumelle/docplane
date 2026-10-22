"""Per-input evaluations checked against the actual read log (stdlib only).

A run's read log is bookkeeping truth about what was opened. An evaluation's `search_scope` must be derived from it,
never copied across inputs: it always contains the input itself, only records that were really opened, and an explicit
KNOW statement. Proposals to create work or dispose of a duplicate stay provisional until relevant captures (not only
initiatives) were inspected.

Log lines are JSON objects with `tool`, `args`, `ok`, `ids_returned` (as written by a read tool that logs every call).
"""
from __future__ import annotations

import json

GENERIC_DISPOSITIONS_NEEDING_CAPTURE_SEARCH = frozenset({"WARRANTS_NEW_DELIVERABLE", "DUPLICATES", "ADDS_OBLIGATION"})
KNOW_WORDS = ("not searched", "searched")


def load_log(path):
    with open(path) as handle:
        return [json.loads(line) for line in handle if line.strip()]


def summarise(log):
    """What the run really opened."""
    captures, histories, listed, register = set(), {}, set(), 0
    for row in log:
        if not row.get("ok") and row.get("tool") != "register":
            continue
        tool, args = row.get("tool"), row.get("args") or {}
        if tool == "docplane_work_capture_get" and args.get("capture_id"):
            captures.add(args["capture_id"])
        elif tool == "docplane_work_get" and args.get("initiative_id"):
            histories.setdefault(args["initiative_id"], []).append(args.get("activity_offset", 0))
        elif tool == "docplane_work_initiatives":
            listed.update(row.get("ids_returned") or [])
        elif tool == "register":
            register += 1
    return {"captures_opened": captures, "histories_opened": histories, "initiatives_listed": listed, "register_queries": register}


def derive_search_scope(log, capture_id, cited_ids):
    """Search scope for one input: the input itself plus the cited records that the log shows were opened."""
    seen = summarise(log)
    cited = set(cited_ids)
    captures = [capture_id] + sorted(c for c in cited if c in seen["captures_opened"] and c != capture_id)
    initiatives = sorted(i for i in cited if i in seen["histories_opened"])
    ranges = {i: sorted(set(seen["histories_opened"][i])) for i in initiatives}
    return {"captures_read": captures, "initiatives_read": initiatives,
            "activity_offsets_opened": ranges,
            "run_level": {"initiatives_listed": len(seen["initiatives_listed"]), "histories_opened": len(seen["histories_opened"]),
                          "pinned_register_queries": seen["register_queries"]},
            "know": "not searched: no docs credential on this access path"}


def validate_evaluations(evaluations, log):
    seen = summarise(log)
    errors = []
    scopes = {}
    for e in evaluations:
        n, cid, s = e.get("input"), e.get("capture_id"), e.get("search_scope") or {}
        read = list(s.get("captures_read") or [])
        if cid not in read:
            errors.append(f"input {n}: the search scope must include the input itself")
        failed = e.get("read_status") in ("FAILED", "AFTER_SNAPSHOT")      # the input's own read did not succeed: that must be visible, not an error
        if failed and not any(r.get("tool") == "docplane_work_capture_get" and (r.get("args") or {}).get("capture_id") == cid for r in log):
            errors.append(f"input {n}: marked {e['read_status']} but the log shows no attempt to read it")
        for c in read:
            if c not in seen["captures_opened"] and not (failed and c == cid):
                errors.append(f"input {n}: claims capture {c} was read but the log shows it was not")
        for i in s.get("initiatives_read") or []:
            if i not in seen["histories_opened"]:
                errors.append(f"input {n}: claims initiative {i} was read but the log shows no history opened")
        know = (s.get("know") or "").strip().lower()
        if know.startswith("not searched"):
            if len(know.partition(":")[2].strip()) < 5:
                errors.append(f"input {n}: KNOW was not searched and no reason is given (write 'not searched: <reason>')")
        elif not know.startswith("searched"):
            errors.append(f"input {n}: KNOW access must be stated explicitly (searched, or not searched and why)")
        if e.get("read_status", "READ") != "READ":
            if e.get("proposed_disposition") != "UNCERTAIN" or e.get("confidence") != "low":
                errors.append(f"input {n}: an unread or withheld record ({e['read_status']}) gets a low-confidence UNCERTAIN disposition, not a judgement")
            if e.get("purpose", "UNDETERMINED") != "UNDETERMINED":
                errors.append(f"input {n}: an unread or withheld record has an UNDETERMINED purpose")
        scopes.setdefault(tuple(sorted(read)), []).append((n, cid))
        others = [c for c in read if c != cid]
        needs = e.get("proposed_disposition") in GENERIC_DISPOSITIONS_NEEDING_CAPTURE_SEARCH or (e.get("relation_to_existing") or {}).get("class") == "NEW"
        if needs and not others and not e.get("provisional"):
            errors.append(f"input {n}: proposing new work or duplicate disposal without inspecting any other capture must be provisional")
        if bool(e.get("provisional")) != bool(e.get("provisional_reason")):
            errors.append(f"input {n}: a provisional evaluation states why, and only a provisional one does")
    for scope, who in scopes.items():
        distinct = {c for _, c in who}
        if len(distinct) > 1:
            errors.append(f"inputs {sorted(n for n, _ in who)} share an identical capture scope: a generic scope copied across inputs")
    return errors
