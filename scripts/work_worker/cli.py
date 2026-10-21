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
from . import readtool, render as render_mod, runcheck, runconfig
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
    r.add_argument("--allow-loopback-http", action="store_true", help="permit plain http to a loopback test endpoint (never for a remote host)")
    r.add_argument("--record-snapshot", help="write a text-free snapshot of the reads (0600) for offline replay")
    r.add_argument("--report-requests", action="store_true", help="print the MCP method/verb names used, to stderr")
    r.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    d = sub.add_parser("decide", help="record a reviewer decision in the local ledger only")
    d.add_argument("--ledger", required=True)
    d.add_argument("--identity", required=True)
    d.add_argument("--status", choices=["RESOLVED", "REJECTED"], required=True)
    d.add_argument("--actor", required=True, help="reviewer principal UUID")
    d.add_argument("--evidence", required=True, nargs="+", help="evidence record UUIDs")
    c = sub.add_parser("config", help="create or verify the pinned run configuration")
    c.add_argument("action", choices=["create", "verify"])
    c.add_argument("path", help="spec file (create) or config file (verify)")
    c.add_argument("--out", help="new config file to write (create)")
    rd = sub.add_parser("read", help="logging, snapshot-enforcing, read-only query tool")
    rd.add_argument("--config", required=True); rd.add_argument("--log", required=True)
    rd.add_argument("--key-file", help="0600 file with CHARLIEHUB_MCP_API_KEY (never argv)"); rd.add_argument("--cafile"); rd.add_argument("--allow-loopback-http", action="store_true", help="permit plain http to a loopback test endpoint (never for a remote host)")
    rd.add_argument("verb", help="a WORK read verb, or MARK, or REGISTER"); rd.add_argument("args", nargs="?", default="{}")
    ck = sub.add_parser("check", help="validate a run: contract, config, read log, evaluations, continuity")
    ck.add_argument("--config", required=True); ck.add_argument("--interpretation", required=True); ck.add_argument("--log", required=True)
    ck.add_argument("--evaluations"); ck.add_argument("--previous"); ck.add_argument("--registry"); ck.add_argument("--report", help="new report file")
    mt = sub.add_parser("mint", help="issue obligation ids once; existing ids are immutable")
    mt.add_argument("--interpretation", required=True); mt.add_argument("--registry-in"); mt.add_argument("--out", required=True)
    rn = sub.add_parser("render", help="briefing (<= 400 words), appendix and full proposal ledger into a NEW directory")
    rn.add_argument("--config", required=True); rn.add_argument("--interpretation", required=True); rn.add_argument("--run-label", required=True)
    rn.add_argument("--ledger-in"); rn.add_argument("--applied"); rn.add_argument("--ops-log"); rn.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd in ("config", "read", "check", "mint", "render"):
        return _harness(a)
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
        client = McpHttpClient(a.mcp_url, load_api_key(a.api_key_file), cafile=a.cafile, allow_loopback_http=a.allow_loopback_http)
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


def _json(path):
    with open(path) as handle:
        return json.load(handle)


def _write_new(path, data):
    with open(path, "x") as handle:                       # never overwrite an existing file
        json.dump(data, handle, indent=1)
    os.chmod(path, 0o444)


def _harness(a):
    if a.cmd == "config":
        if a.action == "verify":
            errors = runconfig.verify(_json(a.path))
            print("\n".join(errors) or "config intact")
            return 1 if errors else 0
        _write_new(a.out, runconfig.create(_json(a.path)))
        print("config written (read-only):", a.out)
        return 0
    if a.cmd == "mint":
        registry = runcheck.mint(_json(a.interpretation), _json(a.registry_in) if a.registry_in else None)
        _write_new(a.out, registry)
        print(f"{len(registry)} obligation ids issued: {a.out}")
        return 0
    config = _json(a.config)
    if a.cmd == "read":
        if a.verb in ("MARK", "REGISTER"):
            call = None
        else:
            if not a.key_file:
                print("--key-file is required for reads", file=sys.stderr)
                return 2
            from .sources import McpHttpClient, load_api_key
            client = McpHttpClient(config["instance"]["url"], load_api_key(a.key_file), cafile=a.cafile, allow_loopback_http=a.allow_loopback_http)
            client.open()
            def call(verb, args):
                res = client._rpc("tools/call", {"name": verb, "arguments": args})
                if res.get("structuredContent") is not None:
                    return res["structuredContent"]
                return json.loads(res["content"][0]["text"])
        tool = readtool.ReadTool(config, a.log, call)
        args = json.loads(a.args)
        if a.verb == "MARK":
            tool.mark(args.get("label", ""))
        elif a.verb == "REGISTER":
            print(tool.register(args["label"], grep=args.get("grep"), lines=args.get("lines"), maximum=int(args.get("max", 60))))
        else:
            print(json.dumps(tool.call(a.verb, args), indent=1))
        return 0
    doc = _json(a.interpretation)
    if a.cmd == "check":
        fps_path = os.path.join(os.path.dirname(os.path.abspath(a.log)), "fingerprints.json")
        errors = runcheck.check_run(config, doc, runcheck.load_log(a.log), _json(fps_path) if os.path.exists(fps_path) else {},
                                    evaluation_list=_json(a.evaluations) if a.evaluations else None,
                                    previous=_json(a.previous) if a.previous else None, registry=_json(a.registry) if a.registry else None)
        if a.report:
            _write_new(a.report, {"ok": not errors, "errors": errors})
        print("\n".join(errors) or "run check passed")
        return 1 if errors else 0
    out = runcheck.fresh_output_dir(a.out, a.config, a.interpretation, *(p for p in (a.ledger_in, a.applied, a.ops_log) if p))
    errors = runconfig.verify(config) + runconfig.header_errors(config, doc)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    ledger = render_mod.build_ledger([(a.run_label, doc)], _json(a.ledger_in) if a.ledger_in else None, _json(a.applied) if a.applied else None)
    ops = [json.loads(line) for line in open(a.ops_log)] if a.ops_log else None
    render_mod.write_all(out, render_mod.render(config, doc, ledger, ops))
    print(f"rendered into {out}: {ledger['count']} proposals retained, {ledger['pending']} pending")
    return 0
