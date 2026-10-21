"""Harness tests on synthetic fixtures only: pinned config, logging read tool, run check, id minting, rendering."""
import contextlib
import copy
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from work_worker import cli, readtool, render, runcheck, runconfig
import test_work_worker_interpretation as T

U = T.U
CUTOFF = "2026-01-02T00:00:00Z"


def world():
    base = {"created_at": "2026-01-01T00:00:00Z", "kind": "FINDING", "status": "INBOX", "author_principal_id": U(7), "body_withheld": False, "origin_text": "{}", "origin_text_withheld": False}
    caps = {U(n): dict(base, capture_id=U(n), body="synthetic body %d" % n) for n in (1, 2, 3)}
    caps[U(4)] = dict(base, capture_id=U(4), body="synthetic later body", created_at="2026-02-01T00:00:00Z")
    act = lambda n, ts: {"activity_id": U(100 + n), "activity_type": "NOTE", "created_at": ts, "author_principal_id": U(7), "body": "synthetic activity %d" % n, "references": [], "source_capture_id": None}
    ini = {"initiative_id": U(9), "initiative_key": "synthetic", "work_state": "BACKLOG", "version": 1, "activity_count": 2, "activity_offset": 0, "activities_truncated": False,
           "owner": {"principal_id": U(8), "principal_kind": "AGENT"}, "founding_capture_ids": [], "activities": [act(1, "2026-01-01T00:00:00Z"), act(2, "2026-02-01T00:00:00Z")]}

    def call(verb, args):
        if verb == "docplane_work_capture_get":
            return caps.get(args["capture_id"], {"error": "x", "code": "NOT_FOUND"})
        if verb == "docplane_work_get":
            return copy.deepcopy(ini)
        if verb == "docplane_work_captures":
            return {"captures": [{"capture_id": c["capture_id"], "created_at": c["created_at"]} for c in caps.values()], "count": 4, "total": 4}
        if verb == "docplane_work_initiatives":
            return {"initiatives": [{"initiative_id": U(9), "initiative_key": "synthetic", "work_state": "BACKLOG"}], "count": 1}
        if verb == "read_doc":
            return {"path": "p", "content_markdown": "x", "last_updated": "2026-02-01T00:00:00Z"}
        raise AssertionError("unexpected verb " + verb)
    return call


class Workdir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / "register.md").write_text("K8 | stage 2 | accepted\nK17 | off-host | accepted\n")
        (self.d / "prompt.md").write_text("synthetic pinned prompt")

    def spec(self, **over):
        s = {"scope_id": "s1", "snapshot_cutoff": CUTOFF, "instance": {"label": "synthetic instance", "url": "http://127.0.0.1:1/mcp", "expected_identity": {"dump_sha256": "synthetic"}},
             "know_access": "SEARCHED", "know_reason": "no docs credential on this path", "incoming_record_ids": [U(1)], "initiative_ids": [U(9)], "pinned_files": [{"label": "register", "path": str(self.d / "register.md")}],
             "prompt_files": [str(self.d / "prompt.md")], "model_configuration": "synthetic agent", "read_budget": 12,
             "authoritative_registers": [{"register_id": "prog-register", "description": "programme register kept in git", "pinned_file_label": "register"}]}
        s.update(over)
        return s

    def config(self, **over):
        return runconfig.create(self.spec(**over))

    def tool(self, config=None, name="read-log.jsonl"):
        return readtool.ReadTool(config or self.config(), str(self.d / name), world())

    def log(self, name="read-log.jsonl"):
        return [json.loads(x) for x in (self.d / name).read_text().splitlines()]


class Config(Workdir):
    def test_create_and_verify_and_tamper(self):
        c = self.config()
        self.assertEqual(runconfig.verify(c), [])
        edited = dict(c, snapshot_cutoff="2026-03-01T00:00:00Z")
        self.assertTrue(any("self hash differs" in e for e in runconfig.verify(edited)))
        (self.d / "register.md").write_text("changed")
        self.assertTrue(any("pinned file changed" in e for e in runconfig.verify(c)))

    def test_review_unavailable_know_access_needs_a_reason(self):
        with self.assertRaises(ValueError):
            runconfig.create(self.spec(know_access="NOT_SEARCHED_OTHER", know_reason=""))
        self.assertEqual(runconfig.create(self.spec(know_access="NOT_SEARCHED_OTHER", know_reason="the docs service refused reads"))["know_reason"], "the docs service refused reads")

    def test_bad_specs_are_refused(self):
        for over in ({"snapshot_cutoff": "yesterday"}, {"know_access": "MAYBE"}, {"incoming_record_ids": []}, {"read_budget": 5, "extra": 1},
                     {"authoritative_registers": [{"register_id": "r", "description": "d", "pinned_file_label": "nope"}]}):
            with self.assertRaises(ValueError):
                runconfig.create(self.spec(**over))

    def test_config_carries_no_credential_or_content(self):
        text = json.dumps(self.config())
        self.assertNotIn("synthetic body", text)
        self.assertNotIn("api_key", text.lower())


class Documentation(unittest.TestCase):
    def test_the_documented_spec_names_every_required_key_and_the_procedure_covers_every_command(self):
        text = (Path(__file__).resolve().parents[1] / "work_worker" / "RUN-PROCEDURE.md").read_text()
        block = text.split("Write `spec.json`")[1].split("```")[1]
        for key in runconfig.SPEC_KEYS:
            self.assertIn('"%s"' % key, block, key)
        for command in ("config create", "config verify", " read ", "check ", "mint ", "render "):
            self.assertIn(command, text)
        self.assertIn("NOT_SEARCHED_NO_CREDENTIAL", text)
        self.assertNotIn("/home/", text)


class ReadTool(Workdir):
    def test_snapshot_cut_logging_and_fingerprints(self):
        t = self.tool()
        self.assertEqual(t.call("docplane_work_capture_get", {"capture_id": U(1)})["capture_id"], U(1))
        self.assertEqual(t.call("docplane_work_capture_get", {"capture_id": U(4)})["code"], "AFTER_SNAPSHOT")      # created after the one cutoff
        listing = t.call("docplane_work_captures", {"status": "ALL"})
        self.assertEqual((listing["count"], listing["filtered_after_snapshot"]), (3, 1))
        got = t.call("docplane_work_get", {"initiative_id": U(9)})
        self.assertEqual((len(got["activities"]), got["filtered_after_snapshot"]), (1, 1))
        rows = self.log()
        self.assertEqual(rows[0]["tool"], "CONFIG")
        self.assertTrue(all(r["cfg"] == self.config()["config_sha256"][:16] for r in rows))
        self.assertEqual(sum(r.get("filtered_after_snapshot", 0) or 0 for r in rows), 3)
        fps = json.loads((self.d / "fingerprints.json").read_text())
        self.assertIn(U(1), fps)
        self.assertNotIn(U(4), fps)                                                  # a record the cut removed gets no fingerprint
        self.assertNotIn("synthetic body", (self.d / "read-log.jsonl").read_text())   # the log holds ids and outcomes, never bodies

    def fingerprints_with(self, name, mutate):
        sub = self.d / name
        sub.mkdir()
        base = world()

        def call(verb, args):
            res = copy.deepcopy(base(verb, args))
            mutate(verb, res)
            return res
        t = readtool.ReadTool(self.config(), str(sub / "read-log.jsonl"), call)
        t.call("docplane_work_capture_get", {"capture_id": U(1)})
        t.call("docplane_work_get", {"initiative_id": U(9)})
        return json.loads((sub / "fingerprints.json").read_text())

    def test_review_fingerprints_cover_the_typed_references_not_only_the_body(self):
        def refs(cap=None, act=None, founding=None):
            def mutate(verb, res):
                if verb == "docplane_work_capture_get" and cap is not None:
                    res["linked_capture_ids"] = cap
                if verb == "docplane_work_get":
                    if act is not None:
                        res["activities"][0]["references"] = act
                        res["activities"][0]["source_capture_id"] = act[0]["id"] if act else None
                    if founding is not None:
                        res["founding_capture_ids"] = founding
            return mutate
        base = self.fingerprints_with("a", refs())
        none = self.fingerprints_with("b", lambda v, r: None)
        self.assertEqual(base, none)                                                  # no references: the version a record had before is unchanged
        ref_a = [{"type": "ACTIVITY", "id": U(50)}]
        ref_b = [{"type": "ACTIVITY", "id": U(51)}]
        with_a = self.fingerprints_with("c", refs(cap=[U(5)], act=ref_a, founding=[U(5)]))
        with_b = self.fingerprints_with("d", refs(cap=[U(6)], act=ref_a, founding=[U(5)]))
        self.assertNotEqual(with_a[U(1)], base[U(1)])                                 # same body, a reference now exists
        self.assertNotEqual(with_a[U(1)], with_b[U(1)])                               # same body, a different referenced capture
        self.assertNotEqual(self.fingerprints_with("e", refs(act=ref_a))[U(101)], self.fingerprints_with("f", refs(act=ref_b))[U(101)])
        self.assertNotEqual(self.fingerprints_with("g", refs(founding=[U(5)]))[U(9)], self.fingerprints_with("h", refs(founding=[U(6)]))[U(9)])
        order1 = self.fingerprints_with("i", refs(cap=[U(5), U(6)]))
        order2 = self.fingerprints_with("j", refs(cap=[U(6), U(5)]))
        self.assertEqual(order1, order2)                                              # reordering is not a change
        for fps in (with_a, with_b):
            self.assertEqual(fps[U(1)]["projection"], "sem-v1")

    def test_no_write_verb_and_know_only_when_configured(self):
        t = self.tool()
        for verb in ("docplane_work_note", "docplane_work_capture", "docplane_work_triage_discard", "write_doc"):
            with self.assertRaises(PermissionError):
                t.call(verb, {})
        self.assertEqual(sum(1 for r in self.log() if r.get("refused")), 4)
        off = self.tool(self.config(know_access="NOT_SEARCHED_NO_CREDENTIAL"), name="know-off.jsonl")
        with self.assertRaises(PermissionError) as caught:
            off.call("read_doc", {"path": "p"})
        self.assertIn("KNOW is not available", str(caught.exception))
        self.assertEqual(t.call("read_doc", {"path": "p"})["code"], "AFTER_SNAPSHOT")       # allowed when configured; a post-cutoff revision is ignored

    def test_budget_and_conflicting_config_and_pinned_file(self):
        t = self.tool(self.config(read_budget=2), name="b.jsonl")
        t.call("docplane_work_capture_get", {"capture_id": U(1)}); t.call("docplane_work_capture_get", {"capture_id": U(2)})
        with self.assertRaises(PermissionError):
            t.call("docplane_work_capture_get", {"capture_id": U(3)})
        other = self.config(snapshot_cutoff="2026-01-05T00:00:00Z")
        with self.assertRaises(readtool.ConfigConflict):
            readtool.ReadTool(other, str(self.d / "b.jsonl"), world())                  # a log written under another config is rejected
        c = self.config()
        tool = self.tool(c, name="r.jsonl")
        self.assertIn("K8", tool.register("register", grep="K8"))
        (self.d / "register.md").write_text("tampered")
        with self.assertRaises(readtool.ConfigConflict):
            tool.register("register", grep="K8")
        with self.assertRaises(readtool.ConfigConflict):
            readtool.ReadTool(c, str(self.d / "new.jsonl"), world())                    # the config itself no longer verifies


class RunCheck(Workdir):
    def make_run(self, know="SEARCHED"):
        config = self.config(know_access=know)
        t = readtool.ReadTool(config, str(self.d / "read-log.jsonl"), world())
        for n in (1, 2, 3):
            t.call("docplane_work_capture_get", {"capture_id": U(n)})
        t.call("docplane_work_get", {"initiative_id": U(9)})
        fps = json.loads((self.d / "fingerprints.json").read_text())
        doc = T.Interpretation("test_baseline_and_rich_example_are_valid").incoming(T.base(), T.intake(1, "CONTRIBUTES_EVIDENCE", targets=[T.itarget(3)]))
        doc["run"]["prompt_hash"] = config["prompt_bundle_sha256"]
        for rec in doc["records"]:
            rec["source_fingerprint"] = fps[rec["record_id"]]
            for dep in rec["depends_on"]:
                dep["source_fingerprint"] = fps[dep["record_id"]]
        for e in doc["intake"]:
            e["search"]["know_access"] = know
        if know != "SEARCHED":
            doc["briefing"]["completeness"] = "INCOMPLETE"; doc["briefing"]["omitted"] = [{"what": "KNOW pages were not searched", "why": "no credential"}]
        return config, doc, fps

    def run_check(self, config, doc, fps, **kw):
        return runcheck.check_run(config, doc, runcheck.load_log(str(self.d / "read-log.jsonl")), fps, **kw)

    def test_a_consistent_run_passes(self):
        config, doc, fps = self.make_run()
        self.assertEqual(self.run_check(config, doc, fps), [])

    def test_conflicting_run_configuration_is_rejected(self):
        config, doc, fps = self.make_run()
        for mutate, fragment in ((lambda d: d["run"]["scope"].update(collected_at="2026-03-01T00:00:00Z"), "single snapshot cutoff"),
                                 (lambda d: d["run"]["scope"].update(scope_id="other"), "scope_id conflicts"),
                                 (lambda d: d["run"].update(prompt_hash="1" * 64), "prompt_hash conflicts"),
                                 (lambda d: d["intake"][0]["search"].update(know_access="NOT_SEARCHED_OTHER"), "know_access conflicts")):
            d = copy.deepcopy(doc); mutate(d)
            self.assertTrue(any(fragment in e for e in self.run_check(config, d, fps)), fragment)

    def test_know_limits_are_enforced_when_access_is_unavailable(self):
        config, doc, fps = self.make_run("NOT_SEARCHED_NO_CREDENTIAL")
        self.assertEqual(self.run_check(config, doc, fps), [])
        doc["briefing"]["omitted"] = []; doc["briefing"]["completeness"] = "COMPLETE"
        self.assertTrue(self.run_check(config, doc, fps))                          # claiming completeness without listing the missing KNOW search

    def test_only_what_this_run_read_can_be_cited(self):
        config, doc, fps = self.make_run()
        fake = copy.deepcopy(doc); fake["records"][0]["source_fingerprint"] = {"projection": "sem-v1", "value": "f" * 64}
        self.assertTrue(any("not the one the read tool issued" in e for e in self.run_check(config, fake, fps)))
        self.assertTrue(any("was not read in this run" in e for e in self.run_check(config, doc, {k: v for k, v in fps.items() if k != U(2)})))
        mixed = self.d / "read-log.jsonl"
        with open(mixed, "a") as f:
            f.write(json.dumps({"tool": "docplane_work_capture_get", "cfg": "0" * 16, "ok": True, "ids_returned": []}) + "\n")
        self.assertTrue(any("mixes run configurations" in e for e in self.run_check(config, doc, fps)))

    def test_review_a_labelled_derived_log_header_still_checks_and_keeps_original_rows(self):
        config, doc, fps = self.make_run()
        log = runcheck.load_log(str(self.d / "read-log.jsonl"))
        derived = [dict(log[0], derived=True, derived_from_sha256="0" * 64, derived_at="2026-10-22T00:00:00Z", original_collected_from=log[1]["ts"], original_collected_to=log[-1]["ts"])] + log[1:]
        self.assertEqual(runcheck.check_run(config, doc, derived, fps), [])
        self.assertEqual(derived[1:], log[1:])                                    # original rows and timestamps are untouched
        wrong = [dict(derived[0], config_sha256="1" * 64)] + derived[1:]
        self.assertTrue(runcheck.check_run(config, doc, wrong, fps))

    def test_review_claimed_inspections_must_agree_with_logged_reads(self):
        config, doc, fps = self.make_run()
        doc["intake"][0]["search"]["captures_inspected"] = 1             # the document records one other capture read (2); the log shows two opened (2 and 3)
        self.assertEqual([e for e in self.run_check(config, doc, fps) if "captures inspected" in e], [])
        doc["intake"][0]["search"]["captures_inspected"] = 3             # claims one more than the log returned
        self.assertTrue(any("captures inspected but the log shows 2" in e for e in self.run_check(config, doc, fps)))
        doc["retrieval"]["reads"].append({"record_id": U(50), "how_found": "SEARCH", "read_ok": True})        # the document says more was read than the log returned
        self.assertTrue(any("never returned it" in e for e in self.run_check(config, doc, fps)))
        two = copy.deepcopy(doc); two["retrieval"]["reads"] = two["retrieval"]["reads"][:-1]
        two["intake"][0]["search"]["captures_inspected"] = 1
        self.assertEqual([e for e in self.run_check(config, two, fps) if "inspected" in e], [])
        two["intake"][0]["search"]["initiatives_inspected"] = 2             # only one initiative history was opened
        self.assertTrue(any("initiatives inspected but the log shows 1 histories opened" in e for e in self.run_check(config, two, fps)))

    def test_mint_ids_are_immutable_and_continuity_uses_them(self):
        config, doc, fps = self.make_run()
        doc["records"][0]["parts"][0]["obligation"] = T.ob("o1")
        registry = runcheck.mint(doc)
        self.assertEqual(runcheck.mint(doc, registry), registry)
        with self.assertRaises(ValueError):
            runcheck.mint(doc, {"o1": U(999)})
        later = copy.deepcopy(doc); later["records"][0]["parts"][0]["obligation"]["maps_to"] = registry["o1"]
        self.assertEqual(self.run_check(config, later, fps, previous=doc, registry=registry), [])
        lost = copy.deepcopy(doc); lost["records"][0]["parts"][0]["obligation"] = None; lost["records"][0]["parts"][0]["role"] = "context"
        self.assertTrue(any("continuity" in e for e in self.run_check(config, lost, fps, previous=doc, registry=registry)))

    def test_outputs_never_overwrite_or_enter_an_input_directory(self):
        (self.d / "input.json").write_text("{}")
        with self.assertRaises(FileExistsError):
            runcheck.fresh_output_dir(str(self.d), str(self.d / "input.json"))
        sub = self.d / "outputs"; sub.mkdir(); (sub / "x").write_text("keep")
        with self.assertRaises(FileExistsError):
            runcheck.fresh_output_dir(str(sub), str(self.d / "input.json"))
        new = runcheck.fresh_output_dir(str(self.d / "fresh"), str(self.d / "input.json"))
        self.assertTrue(os.path.isdir(new))


class Rendering(Workdir):
    def synthetic(self, n=14):
        d = T.Interpretation("test_baseline_and_rich_example_are_valid").incoming(T.base(), T.intake(1, "CONTRIBUTES_EVIDENCE", targets=[T.itarget(3)]))
        d["briefing"]["completeness"] = "INCOMPLETE"; d["briefing"]["omitted"] = [{"what": "older history was not read", "why": "outside the window"}]
        d["proposals"] = [{"proposal_id": "p%02d" % i, "kind": "NEXT_ACTION", "target": "NEW", "change": "synthetic proposal %d to do something specific" % i, "why": "synthetic", "alternatives": "none",
                           "evidence": [T.loc(1, "a")], "confidence_reason": "synthetic", "authority_stage": "PROPOSE_ONLY", "capability": "CAPABILITY_GAP", "tool_name": None, "rank": i + 1,
                           "confidence": "medium", "effect": "CLARIFY", "provisional": False, "held_behind": None, "provisional_reason": None} for i in range(n)]
        return d

    def test_ledger_never_drops_a_pending_proposal_and_the_cap_shows_only_a_subset(self):
        config, doc = self.config(), self.synthetic(14)
        ledger = render.build_ledger([("r1", doc)])
        out = render.render(config, doc, ledger)
        self.assertEqual(ledger["count"], 14)
        for i in range(14):
            self.assertIn("| p%02d |" % i, out["appendix.md"])                       # every pending proposal is in the appendix
        self.assertLessEqual(len(out["briefing.md"].split()), 400)
        self.assertEqual(out["briefing.md"].count("\n1. ") + out["briefing.md"].count("\n2. ") + out["briefing.md"].count("\n3. "), 3)   # display cap = 3
        self.assertIn("14 proposals pending", out["briefing.md"])

    def test_a_later_run_without_a_proposal_keeps_it_pending_and_applied_ones_are_marked(self):
        config, first = self.config(), self.synthetic(5)
        ledger = render.build_ledger([("r1", first)])
        second = copy.deepcopy(first); second["proposals"] = second["proposals"][:3]
        merged = render.build_ledger([("r2", second)], previous=ledger, applied={"p00": {"partial": False}, "p01": {"partial": True}})
        by = {e["proposal_id"]: e for e in merged["entries"]}
        self.assertEqual(merged["count"], 5)
        self.assertEqual((by["p04"]["status"], by["p04"]["not_restated_in_latest"]), ("PENDING", True))
        self.assertEqual((by["p00"]["status"], by["p01"]["status"]), ("APPLIED", "PARTLY_APPLIED"))
        out = render.render(config, second, merged)
        self.assertIn("| p04 |", out["appendix.md"])
        self.assertIn("not restated in the latest run", out["appendix.md"])
        self.assertEqual(merged["pending"], 4)                                         # p00 applied; p01 partly applied still counts as pending

    def test_banner_comes_first_and_states_scope_and_know_limits(self):
        config = self.config(know_access="NOT_SEARCHED_NO_CREDENTIAL")
        doc = self.synthetic(3)
        for e in doc["intake"]:
            e["search"]["know_access"] = "NOT_SEARCHED_NO_CREDENTIAL"
        text = render.render(config, doc, render.build_ledger([("r1", doc)]))["briefing.md"]
        banner = text.split("\n")[2]
        self.assertTrue(banner.startswith("> **Scope: INCOMPLETE.**"))
        self.assertIn("no conclusion that depends on KNOW can be drawn", banner)
        self.assertLess(text.index("Scope: INCOMPLETE"), text.index("Canonical facts"))
        self.assertLess(text.index("Canonical facts"), text.index("Proposed organisation"))


class EndToEnd(Workdir):
    """config -> read (through a local fake server) -> check -> render, via the CLI, as the run procedure documents."""
    def serve(self, call):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if "id" not in body:
                    self.send_response(202); self.end_headers(); return
                out, extra = ({"protocolVersion": "2025-03-26"}, {"Mcp-Session-Id": "s"}) if body["method"] == "initialize" else ({"structuredContent": call(body["params"]["name"], body["params"]["arguments"])}, {})
                data = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": out}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers(); self.wfile.write(data)
        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d/mcp" % server.server_port

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_documented_procedure(self):
        url = self.serve(world())
        spec = self.spec(); spec["instance"]["url"] = url
        (self.d / "spec.json").write_text(json.dumps(spec)); key = self.d / "key.env"; key.write_text("CHARLIEHUB_MCP_API_KEY=synthetic-key\n"); key.chmod(0o600)
        self.assertEqual(self.cli("config", "create", str(self.d / "spec.json"), "--out", str(self.d / "config.json"))[0], 0)
        self.assertEqual(self.cli("config", "verify", str(self.d / "config.json"))[0], 0)
        with self.assertRaises(FileExistsError):
            self.cli("config", "create", str(self.d / "spec.json"), "--out", str(self.d / "config.json"))       # never overwrite
        base = ["read", "--config", str(self.d / "config.json"), "--log", str(self.d / "log.jsonl"), "--key-file", str(key), "--allow-loopback-http"]
        for n in (1, 2, 3):
            self.assertEqual(self.cli(*base, "docplane_work_capture_get", json.dumps({"capture_id": U(n)}))[0], 0)
        self.assertEqual(self.cli(*base, "docplane_work_get", json.dumps({"initiative_id": U(9)}))[0], 0)
        code, text = self.cli(*base, "REGISTER", json.dumps({"label": "register", "grep": "K17"}))
        self.assertIn("K17", text)
        with self.assertRaises(PermissionError):
            self.cli(*base, "docplane_work_note", "{}")
        with open(self.d / "config.json") as handle:
            config = json.load(handle)
        doc = T.Interpretation("test_baseline_and_rich_example_are_valid").incoming(T.base(), T.intake(1, "CONTRIBUTES_EVIDENCE", targets=[T.itarget(3)]))
        with open(self.d / "fingerprints.json") as handle:
            fps = json.load(handle)
        doc["run"]["prompt_hash"] = config["prompt_bundle_sha256"]
        for rec in doc["records"]:
            rec["source_fingerprint"] = fps[rec["record_id"]]
            for dep in rec["depends_on"]:
                dep["source_fingerprint"] = fps[dep["record_id"]]
        doc["briefing"]["completeness"] = "INCOMPLETE"; doc["briefing"]["omitted"] = [{"what": "older history was not read", "why": "outside the window"}]
        (self.d / "interpretation.json").write_text(json.dumps(doc))
        code, text = self.cli("check", "--config", str(self.d / "config.json"), "--interpretation", str(self.d / "interpretation.json"), "--log", str(self.d / "log.jsonl"), "--report", str(self.d / "check.json"))
        self.assertEqual((code, text.strip()), (0, "run check passed"))
        code, text = self.cli("render", "--config", str(self.d / "config.json"), "--interpretation", str(self.d / "interpretation.json"), "--run-label", "r1", "--out", str(self.d / "rendered"))
        self.assertEqual(code, 0)
        self.assertTrue((self.d / "rendered" / "briefing.md").exists())
        with self.assertRaises(FileExistsError):
            self.cli("render", "--config", str(self.d / "config.json"), "--interpretation", str(self.d / "interpretation.json"), "--run-label", "r2", "--out", str(self.d / "rendered"))   # never overwrite
        self.assertEqual(self.cli("mint", "--interpretation", str(self.d / "interpretation.json"), "--out", str(self.d / "registry.json"))[0], 0)
        (self.d / "register.md").write_text("tampered")                                        # a changed pinned file stops everything
        self.assertEqual(self.cli("check", "--config", str(self.d / "config.json"), "--interpretation", str(self.d / "interpretation.json"), "--log", str(self.d / "log.jsonl"))[0], 1)


if __name__ == "__main__":
    unittest.main()
