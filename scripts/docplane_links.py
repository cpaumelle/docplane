#!/usr/bin/env python3
"""Corpus link tooling: plan and verify link repairs around page moves.

Operates on a corpus SNAPSHOT (a directory of Markdown, or a JSON mapping of
path -> content) and a MOVE MANIFEST. It deliberately does not talk to the Docs
API: publication, certification and receipts belong to the publishing path, and
keeping this side pure makes it usable from CI, from an operator shell, and from
a test without a live instance.

Subcommands
-----------
  plan    Show the link rewrites a set of moves implies. Read-only.
  apply   Write planned rewrites to a corpus directory. Requires --write.
  scan    Find references to moved-from paths, split into unintended vs preserved.
  routes  Print the compatibility hop each move should serve, derived from depth.

All mutating operations default to a dry run. Every subcommand can emit a
deterministic JSON receipt with --json, suitable for CI assertions and for
attaching to publication evidence.

Exit codes
  0  success, and for `scan` no unintended stale references
  1  a violation was found (unintended stale references, or plan/apply refusal)
  2  usage or input error

Manifest format (JSON)::

    {"moves": [{"from_path": "a/b.md", "to_path": "a/c/b.md"}, ...]}

Extra keys per move are ignored, so a richer cohort manifest can be passed
directly.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from migration.links import (  # noqa: E402
    AmbiguousIdentifierError,
    ParserUncertaintyError,
    expected_redirect_hop,
    plan_corpus,
    scan_stale_references,
)


def _fail(message: str, code: int = 2):
    print("error: %s" % message, file=sys.stderr)
    raise SystemExit(code)


def load_corpus(args) -> dict[str, str]:
    if args.corpus_json:
        try:
            data = json.loads(Path(args.corpus_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _fail("cannot read corpus JSON: %s" % exc)
        if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
            _fail("corpus JSON must be an object of path -> markdown string")
        return data
    root = Path(args.corpus)
    if not root.is_dir():
        _fail("corpus directory not found: %s" % root)
    corpus = {}
    for path in sorted(root.rglob("*.md")):
        corpus[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    if not corpus:
        _fail("no Markdown found under %s" % root)
    return corpus


def load_moves(path: str) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("cannot read manifest: %s" % exc)
    moves = data.get("moves") if isinstance(data, dict) else data
    if not isinstance(moves, list) or not moves:
        _fail("manifest must contain a non-empty 'moves' list")
    for move in moves:
        if not isinstance(move, dict) or "from_path" not in move or "to_path" not in move:
            _fail("each move needs 'from_path' and 'to_path'")
        if move["from_path"] == move["to_path"]:
            _fail("move %s is a no-op" % move["from_path"])
    seen = [m["from_path"] for m in moves]
    if len(set(seen)) != len(seen):
        _fail("manifest contains duplicate from_path entries")
    return moves


def mapping_of(moves) -> dict[str, str]:
    return {m["from_path"]: m["to_path"] for m in moves}


def emit(args, payload: dict) -> None:
    if args.json:
        stream = open(args.json, "w", encoding="utf-8") if args.json != "-" else sys.stdout
        json.dump(payload, stream, indent=1, sort_keys=True)
        stream.write("\n")
        if stream is not sys.stdout:
            stream.close()


# --------------------------------------------------------------------------

def cmd_plan(args) -> int:
    corpus, moves = load_corpus(args), load_moves(args.manifest)
    try:
        plans = plan_corpus(corpus, mapping_of(moves))
    except (ParserUncertaintyError, RuntimeError) as exc:
        _fail(str(exc), 1)
    rewrites = sum(len(p.rewrites) for p in plans)
    preserved = sum(len(p.preservations) for p in plans)
    changed = [p for p in plans if p.changed]
    print("%d rewrite(s) across %d page(s); %d preserved reference(s)"
          % (rewrites, len(changed), preserved))
    for plan in plans:
        if plan.changed:
            print("  %s" % plan.page)
            for r in plan.rewrites:
                print("      L%-5d %s -> %s" % (r.line, r.old_target, r.new_target))
    if preserved:
        print("\npreserved (not rewritten):")
        for plan in plans:
            for p in plan.preservations:
                print("  %s L%d %s [%s]" % (p.page, p.line, p.target, p.reason.value))
    if rewrites == 0:
        print("\nnothing to do: no page references any moved path")
    emit(args, {"moves": [{"from_path": m["from_path"], "to_path": m["to_path"]} for m in moves],
                "pages_changed": len(changed), "rewrites": rewrites, "preserved": preserved,
                "plans": [p.as_dict() for p in plans]})
    return 0


def cmd_apply(args) -> int:
    if args.corpus_json:
        _fail("apply writes files; use --corpus with a directory")
    corpus, moves = load_corpus(args), load_moves(args.manifest)
    try:
        plans = plan_corpus(corpus, mapping_of(moves))
    except (ParserUncertaintyError, RuntimeError) as exc:
        _fail(str(exc), 1)
    changed = [p for p in plans if p.changed]
    if not args.write:
        print("DRY RUN: would rewrite %d reference(s) across %d page(s). "
              "Re-run with --write to apply."
              % (sum(len(p.rewrites) for p in changed), len(changed)))
        emit(args, {"dry_run": True, "pages_changed": len(changed),
                    "plans": [p.as_dict() for p in plans]})
        return 0
    root = Path(args.corpus)
    for plan in changed:
        (root / plan.page).write_text(plan.content, encoding="utf-8")
    print("wrote %d page(s), %d rewrite(s)"
          % (len(changed), sum(len(p.rewrites) for p in changed)))
    emit(args, {"dry_run": False, "pages_changed": len(changed),
                "plans": [p.as_dict() for p in plans]})
    return 0


def cmd_scan(args) -> int:
    corpus, moves = load_corpus(args), load_moves(args.manifest)
    try:
        unintended, preserved = scan_stale_references(corpus, mapping_of(moves))
    except ParserUncertaintyError as exc:
        _fail(str(exc), 1)
    print("unintended stale references: %d" % len(unintended))
    for ref in unintended:
        print("  %s L%d -> %s" % (ref.page, ref.line, ref.target))
    print("preserved references: %d" % len(preserved))
    for p in preserved:
        print("  %s L%d -> %s [%s]" % (p.page, p.line, p.target, p.reason.value))

    reconciled = None
    if args.expect_preserved:
        try:
            declared = json.loads(Path(args.expect_preserved).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _fail("cannot read expected-preservation file: %s" % exc)
        want = {(d["page"], int(d["line"]), d["target"]) for d in declared.get("preserved", declared)}
        got = {p.key() for p in preserved}
        reconciled = want == got
        print("preservation reconciles with declaration: %s" % reconciled)
        if not reconciled:
            for item in sorted(want - got):
                print("  declared but not found: %s" % (item,))
            for item in sorted(got - want):
                print("  found but not declared: %s" % (item,))

    emit(args, {
        "unintended": [{"page": r.page, "line": r.line, "target": r.target,
                        "resolved": r.resolved} for r in unintended],
        "preserved": [{"page": p.page, "line": p.line, "target": p.target,
                       "resolved": p.resolved, "reason": p.reason.value} for p in preserved],
        "unintended_count": len(unintended), "preserved_count": len(preserved),
        "preservation_reconciles": reconciled,
    })
    if unintended or reconciled is False:
        return 1
    return 0


def cmd_routes(args) -> int:
    moves = load_moves(args.manifest)
    rows = []
    for move in moves:
        hop = expected_redirect_hop(move["from_path"], move["to_path"])
        rows.append({"from_path": move["from_path"], "to_path": move["to_path"],
                     "expected_hop": hop})
        print("  %-56s -> %-56s hop=%s" % (move["from_path"], move["to_path"], hop))
    emit(args, {"routes": rows})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docplane-links", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(sub, corpus=True):
        sub.add_argument("--manifest", required=True, help="JSON move manifest")
        if corpus:
            group = sub.add_mutually_exclusive_group(required=True)
            group.add_argument("--corpus", help="directory of Markdown")
            group.add_argument("--corpus-json", help="JSON object of path -> markdown")
        sub.add_argument("--json", metavar="PATH",
                         help="write a JSON receipt ('-' for stdout)")

    plan = subparsers.add_parser("plan", help="show implied link rewrites (read-only)")
    common(plan)
    plan.set_defaults(func=cmd_plan)

    apply_ = subparsers.add_parser("apply", help="write rewrites to a corpus directory")
    common(apply_)
    apply_.add_argument("--write", action="store_true",
                        help="actually write; omit for a dry run")
    apply_.set_defaults(func=cmd_apply)

    scan = subparsers.add_parser("scan", help="find stale references to moved paths")
    common(scan)
    scan.add_argument("--expect-preserved", metavar="PATH",
                      help="JSON of declared preservations to reconcile against")
    scan.set_defaults(func=cmd_scan)

    routes = subparsers.add_parser("routes", help="expected compatibility hop per move")
    common(routes, corpus=False)
    routes.set_defaults(func=cmd_routes)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AmbiguousIdentifierError as exc:
        _fail(str(exc), 1)


if __name__ == "__main__":
    raise SystemExit(main())
