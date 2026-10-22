"""Briefing (at most 400 words), detailed appendix and the full proposal ledger. Pure rendering of validated input.

Rules: the incomplete-scope banner and KNOW access come first; canonical facts and proposed organisation are separate; a display cap
limits only the briefing, never the ledger, and rendering fails if any pending proposal is missing from the appendix.
"""
from __future__ import annotations

import json
import os

DISPLAY_TOP = 3
WORD_LIMIT = 400
def know_line(config):
    if config["know_access"] == "SEARCHED":
        return "KNOW was searched."
    return f"KNOW was not searched ({config['know_access'].replace('NOT_SEARCHED_', '').lower().replace('_', ' ')}: {config['know_reason']}): no conclusion that depends on KNOW can be drawn."


def _hold(pid, h):
    ok = isinstance(h, dict) and all(isinstance(h.get(k), str) for k in ("reason", "source")) and len(h["reason"].strip()) >= 5 and len(h["source"].strip()) >= 3
    ok = ok and isinstance(h.get("subjects", []), list) and all(isinstance(x, str) and x for x in h.get("subjects", []))
    if not ok:
        raise ValueError(f"hold for {pid} needs a string reason and a string source (who or what decided it) and a list of string subjects")
    return {"reason": h["reason"].strip(), "source": h["source"].strip(), "subjects": sorted(h.get("subjects", []))}


def _subject_status(pid, who, value):
    if value is True or value == "APPLIED":
        return "APPLIED"
    if value == "PENDING":
        return "PENDING"
    raise ValueError(f"application record for {pid}: subject {who} has status {value!r}; only APPLIED or PENDING are recorded here (holds go in the hold record)")


def _merge_applied(pid, old, new):
    """Merge a newer application record into the earlier one. Earlier subjects and receipts are never dropped; anything that would rewrite history is refused."""
    if not isinstance(new, dict):
        raise ValueError(f"application record for {pid} must be an object")
    if old is None:
        old = {}
    out = {k: v for k, v in old.items() if k not in ("subjects", "receipts", "partial")}
    for k, v in new.items():
        if k not in ("subjects", "receipts", "partial") and k in out and out[k] != v:
            raise ValueError(f"application record for {pid}: conflicting {k!r} ({out[k]!r} then {v!r})")
        if k not in ("subjects", "receipts", "partial"):
            out[k] = v
    subs = {k: _subject_status(pid, k, v) for k, v in (old.get("subjects") or {}).items()}
    for k, v in (new.get("subjects") or {}).items():
        v = _subject_status(pid, k, v)
        if subs.get(k) == "APPLIED" and v != "APPLIED":
            raise ValueError(f"application record for {pid}: subject {k} was APPLIED and cannot be recorded as {v}")
        subs[k] = v
    receipts = [dict(r) for r in (old.get("receipts") or [])]
    for r in (new.get("receipts") or []):
        same = [x for x in receipts if x.get("capture_id") == r.get("capture_id")]
        if r.get("capture_id") and same:
            if any(x != r for x in same):
                raise ValueError(f"application record for {pid}: a different receipt already exists for {r.get('capture_id')}")
            continue
        receipts.append(dict(r))
    if subs:
        out["subjects"] = subs
    if receipts:
        out["receipts"] = receipts
    if "partial" in new:                                                    # only an explicit new claim is checked; an inherited flag is recomputed from the subjects
        now = new["partial"]
        if now is True and ((old.get("partial") is False and not subs) or (subs and all(v == "APPLIED" for v in subs.values()))):
            raise ValueError(f"application record for {pid}: a fully applied proposal cannot be recorded as partial")
        if now is False and subs and any(v != "APPLIED" for v in subs.values()):
            raise ValueError(f"application record for {pid}: cannot be recorded complete while subjects are not applied")
        out["partial"] = now
    elif "partial" in old:
        out["partial"] = old["partial"]
    return out


def _subjects(applied, hold):
    """Per-subject status from the applied record (subjects: {id: APPLIED}) and the hold (subjects held). None when nothing is tracked per subject."""
    out = {k: _subject_status("ledger", k, v) for k, v in ((applied or {}).get("subjects") or {}).items()}
    for k in (hold or {}).get("subjects", []):
        out.setdefault(k, "HELD")
    return out or None


def build_ledger(runs, previous=None, applied=None, holds=None):
    """Merge proposals across runs (and a previous ledger). Nothing is ever removed: absence from a later run changes only a flag.
    Applied receipts and operator holds are carried from the previous ledger and only replaced by a newer record for the same proposal, so a later
    run that does not restate them cannot flatten them. A hold is a decision with a source and reason; it is not a dependency (held_behind is untouched)."""
    entries = {e["proposal_id"]: dict(e) for e in (previous or {}).get("entries", [])}
    seen_latest = set()
    for label, doc in runs:
        seen_latest = set()
        for p in doc["proposals"]:
            e = entries.setdefault(p["proposal_id"], {"proposal_id": p["proposal_id"], "first_seen": label, "history": []})
            e.update({"kind": p["kind"], "target": p["target"], "change": p["change"], "why": p["why"], "confidence": p["confidence"], "authority_stage": p["authority_stage"],
                      "capability": p["capability"], "tool_name": p["tool_name"], "effect": p["effect"], "provisional": p["provisional"], "held_behind": p["held_behind"],
                      "last_rank": p["rank"], "last_seen": label, "evidence": p["evidence"]})
            e["history"] = list(e.get("history", [])) + [{"run": label, "rank": p["rank"]}]
            seen_latest.add(p["proposal_id"])
    for pid in list(applied or {}) + list(holds or {}):
        if pid not in entries:
            raise ValueError(f"applied/hold record names a proposal that is in no ledger or run: {pid}")
    for pid, e in entries.items():
        app = _merge_applied(pid, e.get("applied"), applied[pid]) if pid in (applied or {}) else e.get("applied")   # carried; a newer record is merged, never replaces
        hold = e.get("hold")
        if pid in (holds or {}):
            newer = _hold(pid, holds[pid])
            if hold and hold != newer:
                e["hold_history"] = list(e.get("hold_history", [])) + [hold]       # a changed hold decision keeps the earlier one
            hold = newer
        subj = _subjects(app, hold)
        if app is not None and subj:
            partial = any(v != "APPLIED" for v in subj.values())
        else:
            partial = bool(app and app.get("partial"))
        if app is not None and not partial:
            e["status"] = "APPLIED"
        elif app is not None:
            e["status"] = "PARTLY_APPLIED"
        elif hold:
            e["status"] = "HELD"
        else:
            e["status"] = "PENDING"
        for key, val in (("applied", app), ("hold", hold), ("subjects", subj)):
            if val is None: e.pop(key, None)
            else: e[key] = val
        e["not_restated_in_latest"] = pid not in seen_latest
    label_list = list((previous or {}).get("runs", [])) + [label for label, _ in runs]
    return {"runs": label_list, "count": len(entries), "pending": sum(1 for e in entries.values() if e["status"] != "APPLIED"),
            "held": sum(1 for e in entries.values() if e["status"] == "HELD" or e.get("hold")), "entries": list(entries.values())}


def _words(text):
    return len(text.split())


def _short(i):
    return (i or "")[:8]


def _rows(doc):
    obs = {o["local_id"]: o for o in doc["briefing"]["open_obligations"]}
    rows = []
    for rec in doc["records"]:
        for part in rec["parts"]:
            o = part["obligation"]
            if o and o["local_id"] in obs:
                homes = ", ".join(((t["register_id"] + ":" + (t["entry_ref"] or "")) if t["kind"] == "AUTHORITATIVE_REGISTER" else _short(t["record_id"]) + ":" + t["target_state"]) for t in o["tracking"]["targets"]) or "none found"
                rows.append({"id": o["local_id"], "text": part["interpretation"], "source": _short(rec["record_id"]), "tracking": o["tracking"]["status"], "home": homes,
                             "coverage": obs[o["local_id"]]["coverage"], "acceptance": o["acceptance"], "review": o["adjudication_status"], "currency": o["currency"], "kind": o["work_kind"]})
    return rows


def render(config, doc, ledger, ops=None):
    """Return {'briefing.md', 'appendix.md', 'ledger.json'} as text. Raises if the briefing cannot fit or a pending proposal would be dropped."""
    b = doc["briefing"]
    rows = _rows(doc)
    pending = [e for e in ledger["entries"] if e["status"] != "APPLIED"]
    held = [e for e in pending if e.get("hold")]                              # any explicit hold, including a hold on one subject of a partly applied proposal
    ranked = sorted((e for e in pending if not e.get("hold")), key=lambda e: e["last_rank"])   # any explicit hold (also on a partly applied proposal) keeps it out of the next-action ranking
    omitted = [x["what"] for x in b["omitted"]]
    writes = [o for o in (ops or []) if o.get("write") and o.get("ok")]

    def briefing(n_done, n_rem, n_next, n_om):
        lines = [f"# Briefing: {b['workstream']}", "",
                 f"> **Scope: {b['completeness']}.** Not covered: " + ("; ".join(omitted[:n_om]) or "nothing recorded") + (f"; {len(omitted) - n_om} more (appendix#omitted)" if len(omitted) > n_om else "") + ". "
                 f"{know_line(config)} {len(rows)} remaining obligation(s) extracted; extraction recall is unmeasured, so this is not a complete list. "
                 f"{len(pending)} proposals pending in the ledger" + (f", {len(held)} of them on explicit hold" if held else "") + f" (top {min(n_next, len(ranked))} shown). Full backlog: [remaining](appendix.md#remaining), [proposals](appendix.md#proposals), [omitted](appendix.md#omitted).", "",
                 f"Snapshot cutoff {config['snapshot_cutoff']}; instance {config['instance']['label']}; records after the cutoff were not shown.", "",
                 "## Canonical facts (recorded claims; external facts unverified)"]
        lines += [f"- {s['text']} ({', '.join(dict.fromkeys(_short(e['record_id']) for e in s['evidence'][:3]))})" for s in b["status_lines"][:n_done]]
        if len(b["status_lines"]) > n_done:
            lines.append(f"- {len(b['status_lines']) - n_done} more in the [appendix](appendix.md#done)")
        lines += ["", f"## Remaining obligations ({len(rows)})"]
        lines += [f"- {r['text']} [{r['source']}] {r['tracking']} {r['home']}" for r in rows[:n_rem]]
        if len(rows) > n_rem:
            lines.append(f"- {len(rows) - n_rem} more in the [appendix](appendix.md#remaining)")
        lines += ["", "## Proposed organisation (nothing applied; each shows what it would do)"]
        lines += [f"{i}. [{e['effect']}{', PROVISIONAL' if e['provisional'] else ''}] {e['change']}" for i, e in enumerate(ranked[:n_next], 1)]
        if writes:
            lines += ["", f"Applied outside this tool: {len(writes)} write(s) (appendix#operations)."]
        return "\n".join(lines) + "\n"

    # Actionable text is never cut: when the briefing is over the limit, fewer COMPLETE items are selected and the rest is linked to the appendix.
    n_done, n_rem, n_next, n_om = 4, 7, DISPLAY_TOP, 4
    text = briefing(n_done, n_rem, n_next, n_om)
    while _words(text) > WORD_LIMIT:
        if n_om > 1 and n_om > len(omitted) // 2:
            n_om -= 1
        elif n_rem > 2:
            n_rem -= 1
        elif n_done > 1:
            n_done -= 1
        elif n_next > 1:
            n_next -= 1
        elif n_rem > 1:
            n_rem -= 1
        elif n_om > 1:
            n_om -= 1
        else:
            raise ValueError("the briefing cannot fit in %d words with one complete item of each kind; shorten the workstream or the item texts" % WORD_LIMIT)
        text = briefing(n_done, n_rem, n_next, n_om)

    app = ["# Appendix (rebuildable from the interpretation, ledger and run config; not an authoritative list)", "",
           f"Run config {config['config_sha256'][:16]}; scope {config['scope_id']}; snapshot cutoff {config['snapshot_cutoff']}; {know_line(config)}", "",
           '<a id="remaining"></a>', f"## Remaining obligations ({len(rows)})", "", "| id | kind | obligation | source | tracking | home | coverage | currency | acceptance | review |", "|---|---|---|---|---|---|---|---|---|---|"]
    app += [f"| {r['id']} | {r['kind']} | {r['text']} | {r['source']} | {r['tracking']} | {r['home']} | {r['coverage']} | {r['currency']} | {r['acceptance']} | {r['review']} |" for r in rows]
    app += ["", '<a id="done"></a>', "## Done and evidence"] + [f"- {s['text']} [{s['confirmation']['basis']}] ({', '.join(_short(e['record_id']) for e in s['evidence'])})" for s in b["status_lines"]]
    app += ["", "## Intake evaluation of incoming records", "", "| record | lifecycle | purpose | processing | disposition | placement | targets | search |", "|---|---|---|---|---|---|---|---|"]
    for e in doc["intake"]:
        app.append(f"| {_short(e['record_id'])} | {e['lifecycle_seen']} | {e['purpose']} | {e['processing_status']} | {e['disposition']} | {e['placement']} | "
                   f"{', '.join((t['register_id'] + ':' + (t['entry_ref'] or '')) if t['kind'] == 'REGISTER_ROW' else _short(t['record_id']) + ':' + t['relation'] for t in e['targets']) or '-'} | "
                   f"captures {e['search']['captures_inspected']}, initiatives {e['search']['initiatives_inspected']}, beyond {e['search']['beyond_workstream']}, completed {e['search']['included_completed']}, KNOW {e['search']['know_access']} |")
    app += ["", f"Coverage: {doc['coverage']}", "", "## Explicit concerns (no action or acceptance is invented)"] + [f"- {c['concern_id']}: {c['text']} (from {_short(c['record_id'])})" for c in doc["concerns"]]
    app += ["", "## Relations"] + [f"- {r['type']}: {_short(r['from']['record_id'])} -> {_short(r['to']['record_id'])} ({r['basis'].lower()}, {r['confidence']})" for r in doc["relations"]]
    app += ["", '<a id="omitted"></a>', "## Unresolved scope and omitted history"] + [f"- {x['what']}: {x['why']}" for x in b["omitted"]]
    app += ["", '<a id="proposals"></a>', f"## Proposal ledger ({ledger['count']} retained, {len(pending)} pending; the briefing shows a display subset only)", "",
            "| rank | id | kind | effect | confidence | provisional | held behind | tool | status | proposal |", "|---|---|---|---|---|---|---|---|---|---|"]
    for e in sorted(ledger["entries"], key=lambda e: e["last_rank"]):
        app.append(f"| {e['last_rank']} | {e['proposal_id']} | {e['kind']} | {e['effect']} | {e['confidence']} | {e['provisional']} | {e['held_behind'] or '-'} | {e['tool_name'] or e['capability']} | "
                   f"{e['status']}{' (' + e['hold']['reason'] + '; source: ' + e['hold']['source'] + ')' if e.get('hold') and e['status'] == 'HELD' else ''}{' (not restated in the latest run)' if e['not_restated_in_latest'] else ''} | {e['change']} |")
    tracked = [e for e in ledger["entries"] if e.get("hold") or e.get("subjects")]
    if tracked:
        app += ["", "## Holds and per-subject status (operator decisions, not dependencies)"]
        for e in sorted(tracked, key=lambda e: e["last_rank"]):
            if e.get("hold"):
                app.append(f"- {e['proposal_id']}: HOLD ({e['hold']['source']}): {e['hold']['reason']}" + (f" Subjects held: {', '.join(e['hold']['subjects'])}." if e["hold"]["subjects"] else ""))
            if e.get("subjects"):
                app.append(f"- {e['proposal_id']} per subject: " + "; ".join(f"{k} {v}" for k, v in sorted(e["subjects"].items())))
    app += ["", '<a id="operations"></a>', "## Operations recorded by the operator (this tool applies none)"] + [f"- {o['ts'][11:19]} {o['tool']} -> {o.get('result')}" for o in writes]
    app += ["", "## Limits", "- not retrieved: " + ("; ".join(n["what"] for n in doc["retrieval"]["not_retrieved"]) or "nothing recorded"), f"- run incomplete: {doc['incomplete']}"]
    appendix = "\n".join(app) + "\n"
    for e in pending:                                   # rendering must never drop a pending proposal
        if f"| {e['proposal_id']} |" not in appendix:
            raise ValueError(f"pending proposal {e['proposal_id']} is missing from the appendix")
    return {"briefing.md": text, "appendix.md": appendix, "ledger.json": json.dumps(ledger, indent=1)}


def write_all(out_dir, files):
    for name, text in files.items():
        path = os.path.join(out_dir, name)
        with open(path, "x") as handle:                  # 'x': never overwrite
            handle.write(text)
        os.chmod(path, 0o444)
