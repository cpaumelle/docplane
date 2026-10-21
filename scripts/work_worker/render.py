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


def build_ledger(runs, previous=None, applied=None):
    """Merge proposals across runs (and a previous ledger). Nothing is ever removed: absence from a later run changes only a flag."""
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
    applied = applied or {}
    for pid, e in entries.items():
        e["status"] = ("PARTLY_APPLIED" if applied[pid].get("partial") else "APPLIED") if pid in applied else "PENDING"
        e["not_restated_in_latest"] = pid not in seen_latest
        if pid in applied:
            e["applied"] = applied[pid]
    label_list = list((previous or {}).get("runs", [])) + [label for label, _ in runs]
    return {"runs": label_list, "count": len(entries), "pending": sum(1 for e in entries.values() if e["status"] != "APPLIED"), "entries": list(entries.values())}


def _words(text):
    return len(text.split())


def _trunc(text, n):
    parts = text.split()
    return " ".join(parts[:n]) + (" ..." if len(parts) > n else "")


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
    ranked = sorted(pending, key=lambda e: e["last_rank"])
    omitted = [_trunc(x["what"], 12) for x in b["omitted"]]
    writes = [o for o in (ops or []) if o.get("write") and o.get("ok")]

    def briefing(n_done, n_rem, n_next):
        lines = [f"# Briefing: {_trunc(b['workstream'], 12)}", "",
                 f"> **Scope: {b['completeness']}.** Not covered: " + ("; ".join(omitted[:4]) or "nothing recorded") + (f"; {len(omitted) - 4} more" if len(omitted) > 4 else "") + ". "
                 f"{know_line(config)} {len(rows)} remaining obligation(s) extracted; extraction recall is unmeasured, so this is not a complete list. "
                 f"{len(pending)} proposals pending in the ledger (top {min(n_next, len(ranked))} shown). Full backlog: [remaining](appendix.md#remaining), [proposals](appendix.md#proposals), [omitted](appendix.md#omitted).", "",
                 f"Snapshot cutoff {config['snapshot_cutoff']}; instance {config['instance']['label']}; records after the cutoff were not shown.", "",
                 "## Canonical facts (recorded claims; external facts unverified)"]
        lines += [f"- {_trunc(s['text'], 22)} ({', '.join(dict.fromkeys(_short(e['record_id']) for e in s['evidence'][:3]))})" for s in b["status_lines"][:n_done]]
        lines += ["", f"## Remaining obligations ({len(rows)})"]
        lines += [f"- {_trunc(r['text'], 15)} [{r['source']}] {r['tracking']} {r['home']}" for r in rows[:n_rem]]
        if len(rows) > n_rem:
            lines.append(f"- {len(rows) - n_rem} more in the appendix")
        lines += ["", "## Proposed organisation (nothing applied; each shows what it would do)"]
        lines += [f"{i}. [{e['effect']}{', PROVISIONAL' if e['provisional'] else ''}] {_trunc(e['change'], 24)}" for i, e in enumerate(ranked[:n_next], 1)]
        if writes:
            lines += ["", f"Applied outside this tool: {len(writes)} write(s) (appendix#operations)."]
        return "\n".join(lines) + "\n"

    n_done, n_rem, n_next = 4, 7, DISPLAY_TOP
    text = briefing(n_done, n_rem, n_next)
    while _words(text) > WORD_LIMIT:
        if n_rem > 2:
            n_rem -= 1
        elif n_done > 1:
            n_done -= 1
        elif n_next > 1:
            n_next -= 1
        else:
            raise ValueError("the banner alone does not fit in %d words" % WORD_LIMIT)
        text = briefing(n_done, n_rem, n_next)

    app = ["# Appendix (rebuildable from the interpretation, ledger and run config; not an authoritative list)", "",
           f"Run config {config['config_sha256'][:16]}; scope {config['scope_id']}; snapshot cutoff {config['snapshot_cutoff']}; {know_line(config)}", "",
           '<a id="remaining"></a>', f"## Remaining obligations ({len(rows)})", "", "| id | kind | obligation | source | tracking | home | coverage | currency | acceptance | review |", "|---|---|---|---|---|---|---|---|---|---|"]
    app += [f"| {r['id']} | {r['kind']} | {r['text']} | {r['source']} | {r['tracking']} | {r['home']} | {r['coverage']} | {r['currency']} | {r['acceptance']} | {r['review']} |" for r in rows]
    app += ["", "## Done and evidence"] + [f"- {s['text']} [{s['confirmation']['basis']}] ({', '.join(_short(e['record_id']) for e in s['evidence'])})" for s in b["status_lines"]]
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
                   f"{e['status']}{' (not restated in the latest run)' if e['not_restated_in_latest'] else ''} | {e['change']} |")
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
