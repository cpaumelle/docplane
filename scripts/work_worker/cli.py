"""Manual entry point: ``python3 -m work_worker run|decide`` (from scripts/).

Reads only. The ledger file is a private local file (0600), written atomically;
it is the only thing this command writes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

from . import ledger as ledger_mod
from .report import build, render
from .sources import McpReads, SnapshotReads, open_mcp_session
from .worker import run


def _load(path):
    if path and os.path.exists(path):
        with open(path) as handle:
            return json.load(handle)
    return {}


def _save(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)))
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="work_worker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="read WORK and print a proposal")
    r.add_argument("--manifest", required=True, help="reviewed obligations + scope (JSON)")
    r.add_argument("--ledger", required=True, help="private ledger state file (read, then rewritten)")
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--snapshot", help="preserved/synthetic snapshot (JSON); historical")
    src.add_argument("--mcp-url", help="consolidated MCP URL; bearer from --bearer-file")
    r.add_argument("--bearer-file", help="0600 file holding the MCP bearer (never argv)")
    r.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    d = sub.add_parser("decide", help="record a reviewer decision in the local ledger only")
    d.add_argument("--ledger", required=True)
    d.add_argument("--identity", required=True)
    d.add_argument("--status", choices=["RESOLVED", "REJECTED"], required=True)
    d.add_argument("--actor", required=True, help="reviewer principal UUID")
    d.add_argument("--evidence", required=True, nargs="+", help="evidence record UUIDs")
    a = ap.parse_args(argv)
    if a.cmd == "decide":
        _save(a.ledger, ledger_mod.decide(_load(a.ledger), a.identity, status=a.status,
                                          actor_id=a.actor, evidence=a.evidence))
        return 0
    with open(a.manifest) as handle:
        manifest = json.load(handle)
    if a.snapshot:
        with open(a.snapshot) as handle:
            reads = SnapshotReads(json.load(handle))
    else:
        if not a.bearer_file:
            ap.error("--mcp-url needs --bearer-file")
        reads = McpReads(open_mcp_session(a.mcp_url, a.bearer_file))
    state, view = run(reads, manifest, _load(a.ledger))
    _save(a.ledger, state)
    proposal = build(state, view)
    sys.stdout.write(json.dumps(proposal, indent=1) + "\n" if a.json else render(proposal))
    return 0
