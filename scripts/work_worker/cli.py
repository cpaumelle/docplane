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
from .sources import McpHttpClient, McpReads, RecordingReads, SnapshotReads, load_api_key
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
    src.add_argument("--mcp-url", help="consolidated MCP URL; key from --api-key-file")
    r.add_argument("--api-key-file", help="0600 env-style file defining CHARLIEHUB_MCP_API_KEY (parsed, never argv)")
    r.add_argument("--cafile", help="CA bundle for the MCP host")
    r.add_argument("--record-snapshot", help="write a text-free snapshot of the reads (0600) for offline replay")
    r.add_argument("--report-requests", action="store_true", help="print the MCP method/verb names used, to stderr")
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
        if not a.api_key_file:
            ap.error("--mcp-url needs --api-key-file")
        client = McpHttpClient(a.mcp_url, load_api_key(a.api_key_file), cafile=a.cafile)
        reads = McpReads(client)
    if a.record_snapshot:
        reads = RecordingReads(reads)
    state, view = run(reads, manifest, _load(a.ledger))
    if a.record_snapshot:
        import datetime
        reads.snapshot["as_of"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _save(a.record_snapshot, reads.snapshot)
    if a.report_requests and not a.snapshot:
        sys.stderr.write("requests: " + json.dumps(sorted(set(client.requests))) + " count=%d\n" % len(client.requests))
    _save(a.ledger, state)
    proposal = build(state, view)
    sys.stdout.write(json.dumps(proposal, indent=1) + "\n" if a.json else render(proposal))
    return 0
