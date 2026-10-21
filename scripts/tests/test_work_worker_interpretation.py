"""Synthetic fixtures for the librarian interpretation contract (librarian-interp-2)."""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from work_worker import interpretation as interp

U = lambda n: "00000000-0000-4000-8000-%012d" % n
FP = lambda n: {"projection": "redacted-text-v1", "value": "%064x" % n}
CONF = {"basis": "RECORDED_CLAIM_ONLY", "read_at": None, "verifier_needed": None}


def part(pid, role="context", ob=None, conf=CONF):
    return {"part_id": pid, "locator_para": 0, "role": role, "interpretation": "synthetic statement " + pid, "epistemic": "explicit_statement",
            "temporal": {"reported_at": "2026-01-01", "observed_at": None, "valid_from": None, "valid_until": None},
            "responsibility": {"authenticated_writer": None, "reported_producer": None, "claimed_accountable": None, "acknowledgement": None},
            "confirmation": conf, "obligation": ob}


def cover(judgment="UNKNOWN", reason="UNKNOWN"):
    return {"judgment": judgment, "reason": reason}


def target(n=3, kind="WORK_ITEM", basis="STRUCTURAL", obj=True, disp="ACCEPTED", state="BACKLOG", cov=None, register=None, entry=None):
    return {"kind": kind, "record_id": U(n) if kind != "AUTHORITATIVE_REGISTER" and kind != "RECORD_ONLY" or kind == "RECORD_ONLY" and n else None,
            "register_id": register, "entry_ref": entry, "basis": basis, "objective_stated": obj, "disposition_stated": disp,
            "target_state": state, "coverage": cov or cover("ACTIVELY_COVERS", "NONE")}


def ob(lid, status="NOT_FOUND", targets=(), state="OPEN"):
    return {"local_id": lid, "maps_to": None, "state": state, "due": None, "buried": True, "tracking": {"status": status, "targets": list(targets)},
            "acceptance": "synthetic acceptance for " + lid, "source_refs": [], "adjudication_status": "NONE"}


def loc(n, p=None):
    return {"record_id": U(n), "part_id": p}


def base():
    return {"schema_version": "librarian-interp-2",
            "run": {"scope": {"scope_id": "s1", "initiative_ids": [U(9)], "capture_statuses": ["INBOX"], "collected_at": "2026-01-02T00:00:00Z",
                              "enumeration_limits": "synthetic", "authoritative_registers": [{"register_id": "prog-register", "description": "programme register kept in git"}]},
                    "budget": {"max_changed_records": 10, "max_candidates_per_record": 5, "max_extra_reads": 30}, "interpreter_version": "t1", "prompt_hash": "0" * 64},
            "records": [
                {"record_id": U(1), "record_kind": "capture", "source_fingerprint": FP(1), "depends_on": [{"record_id": U(2), "source_fingerprint": FP(2)}],
                 "parts": [part("a", "obligation", ob("o1")), part("b", "evidence")]},
                {"record_id": U(2), "record_kind": "capture", "source_fingerprint": FP(2), "depends_on": [], "parts": [part("c", "correction"), part("d", "decision")]}],
            "mappings": [], "relations": [], "unresolved_references": [],
            "briefing": {"workstream": "synthetic", "deliverable": "synthetic deliverable", "acceptance_as_stated": None, "status_lines": [], "open_obligations": [],
                         "retractions": [], "unresolved_contradictions": [], "ready_to_close": "no", "why": "synthetic"},
            "proposals": [], "incomplete": False,
            "retrieval": {"reads": [{"record_id": U(n), "how_found": "SEED", "read_ok": True} for n in (1, 2, 3, 9)], "not_retrieved": [], "budget_exhausted": False}}


def rel(rid, typ, basis="EXPLICIT_STATEMENT", via=None):
    return {"relation_id": rid, "type": typ, "from": loc(2, "c"), "to": loc(1, "b"), "basis": basis, "confidence": "high", "evidence": [loc(2, "c")], "via_reference": via}


def with_ob(o, doc=None):
    doc = doc or base()
    doc["records"][0]["parts"][0]["obligation"] = o
    return doc


class Interpretation(unittest.TestCase):
    def bad(self, doc, fragment):
        errors = interp.validate(doc)
        self.assertTrue(any(fragment in e for e in errors), errors)

    def test_baseline_and_rich_example_are_valid(self):
        self.assertEqual(interp.validate(base()), [])
        d = with_ob(ob("o1", "TRACKED", [target()]))
        d["relations"] = [rel("r1", "CORRECTS_RETRACTS"), dict(rel("r2", "CONTRADICTS_UNRESOLVED", "SEMANTIC"), to=loc(2, "d"))]
        d["briefing"]["retractions"], d["briefing"]["unresolved_contradictions"] = ["r1"], ["r2"]
        d["briefing"]["open_obligations"] = [{"local_id": "o1", "original_record": U(1), "text": "synthetic", "tracking_status": "TRACKED", "coverage": "ACTIVELY_COVERS"}]
        d["briefing"]["status_lines"] = [
            {"text": "state read", "confirmation": {"basis": "WORK_STATE_READ_NOW", "read_at": "2026-01-02T00:00:00Z"}, "evidence": [loc(3)]},
            {"text": "external run claimed", "confirmation": {"basis": "EXTERNAL_UNVERIFIED", "read_at": None, "verifier_needed": "check the code host"}, "evidence": [loc(1, "b")]}]
        self.assertEqual(interp.validate(d), [])

    def test_tracked_means_an_identifiable_item_records_it(self):
        for t in ([], [target(basis="SEMANTIC")], [target(obj=False)], [target(disp="NONE")], [target(kind="RECORD_ONLY")]):
            self.bad(with_ob(ob("o1", "TRACKED", t)), "TRACKED needs")
        self.assertEqual(interp.validate(with_ob(ob("o1", "CANDIDATE", [target(basis="SEMANTIC")]))), [])
        self.assertEqual(interp.validate(with_ob(ob("o1", "MENTIONED_ONLY", [target(kind="RECORD_ONLY")]))), [])

    def test_abandoned_and_completed_targets_stay_visible_and_are_a_separate_judgement(self):
        gone = target(state="ABANDONED", cov=cover("DOES_NOT_ACTIVELY_COVER", "ABANDONED"))
        d = with_ob(ob("o1", "TRACKED", [gone]))                      # still TRACKED: the relationship is not erased
        d["briefing"]["open_obligations"] = [{"local_id": "o1", "original_record": U(1), "text": "x", "tracking_status": "TRACKED", "coverage": "DOES_NOT_ACTIVELY_COVER"}]
        self.assertEqual(interp.validate(d), [])
        self.bad(with_ob(ob("o1", "TRACKED", [target(state="ABANDONED", cov=cover("ACTIVELY_COVERS", "NONE"))])), "abandoned target stays visible")
        self.bad(with_ob(ob("o1", "TRACKED", [target(state="ABANDONED", cov=cover("DOES_NOT_ACTIVELY_COVER", "STALE"))])), "abandoned target stays visible")
        self.bad(with_ob(ob("o1", "TRACKED", [target(state="COMPLETE")])), "completed item does not actively cover")
        done = target(state="COMPLETE", cov=cover("DOES_NOT_ACTIVELY_COVER", "COMPLETED_WITHOUT_THIS"))
        self.assertEqual(interp.validate(with_ob(ob("o1", "TRACKED", [done]))), [])

    def test_authoritative_register_entries_can_track_only_when_declared(self):
        reg = target(kind="AUTHORITATIVE_REGISTER", basis="REGISTER_ENTRY", state="NOT_APPLICABLE", register="prog-register", entry="item 7")
        self.assertEqual(interp.validate(with_ob(ob("o1", "TRACKED", [reg]))), [])
        undeclared = copy.deepcopy(reg); undeclared["register_id"] = "other-register"
        self.bad(with_ob(ob("o1", "TRACKED", [undeclared])), "declared authoritative register")
        no_entry = copy.deepcopy(reg); no_entry["entry_ref"] = None
        self.bad(with_ob(ob("o1", "TRACKED", [no_entry])), "declared authoritative register")

    def test_dependencies_are_recorded_and_must_have_been_read(self):
        d = base(); del d["records"][0]["depends_on"]
        self.assertTrue(interp.validate(d))
        d = base(); d["records"][0]["depends_on"][0]["record_id"] = U(77)
        self.bad(d, "was not read")

    def test_confirmation_is_qualified_by_source(self):
        d = base(); d["briefing"]["status_lines"] = [{"text": "x", "confirmation": {"basis": "EXTERNAL_UNVERIFIED", "read_at": None}, "evidence": [loc(1)]}]
        self.bad(d, "verifier_needed")
        d = base(); d["briefing"]["status_lines"] = [{"text": "x", "confirmation": {"basis": "WORK_STATE_READ_NOW", "read_at": None}, "evidence": [loc(1)]}]
        self.bad(d, "read_at")
        d = base(); d["briefing"]["status_lines"] = [{"text": "x", "confirmation": {"basis": "VERIFIED_NOW", "read_at": None}, "evidence": [loc(1)]}]
        self.assertTrue(interp.validate(d))

    def test_retraction_versus_contradiction(self):
        d = base(); d["relations"] = [rel("r1", "CORRECTS_RETRACTS", "SEMANTIC")]
        self.bad(d, "retraction needs an explicit statement")
        d = base(); d["relations"] = [dict(rel("r1", "CONTRADICTS_UNRESOLVED"), to=loc(1))]
        self.bad(d, "names the conflicting parts")
        d = base(); d["relations"] = [rel("r1", "CONTRADICTS_UNRESOLVED")]; d["briefing"]["retractions"] = ["r1"]
        self.bad(d, "not a CORRECTS_RETRACTS")

    def test_prefix_resolution_needs_unique_confirmed_in_scope(self):
        ref = lambda **kw: dict({"token": "5c840899", "cited_in": loc(1, "a"), "scope_id": "s1", "outcome": "UNIQUE_CONFIRMED", "candidate_count": 1,
                                 "resolved_record_id": U(3), "confirmed_by_full_read": True}, **kw)
        d = base(); d["unresolved_references"] = [ref()]; d["relations"] = [rel("r1", "FOLLOWS_UP", "CITED_ID", via=0)]
        self.assertEqual(interp.validate(d), [])
        for kw, frag in (({"candidate_count": 2}, "one candidate"), ({"confirmed_by_full_read": False}, "one candidate"),
                         ({"outcome": "AMBIGUOUS", "candidate_count": 2}, "only a unique"), ({"resolved_record_id": U(55)}, "one candidate")):
            d = base(); d["unresolved_references"] = [ref(**kw)]
            self.bad(d, frag)
        d = base(); d["unresolved_references"] = [ref(outcome="NO_MATCH_IN_SCOPE", candidate_count=0, resolved_record_id=None, confirmed_by_full_read=False)]
        d["relations"] = [rel("r1", "FOLLOWS_UP", "CITED_ID", via=0)]
        self.bad(d, "unresolved or ambiguous")

    def test_incomplete_runs_cannot_claim_closure(self):
        d = base(); d["retrieval"]["reads"][0]["read_ok"] = False
        self.bad(d, "incomplete must be true")
        d["incomplete"] = True; d["briefing"]["ready_to_close"] = "yes"
        self.assertTrue(any("ready_to_close" in e or "not read" in e for e in interp.validate(d)))

    def test_text_limits_unknown_fields_and_role_agreement(self):
        d = base(); d["records"][0]["parts"][0]["interpretation"] = 'says "quoted text"'
        self.assertTrue(interp.validate(d))
        d = base(); d["records"][0]["parts"][0]["interpretation"] = " ".join(["word"] * 40)
        self.assertTrue(interp.validate(d))
        d = base(); d["records"][0]["raw_body"] = "x"
        self.assertTrue(interp.validate(d))
        d = base(); d["records"][0]["parts"][1]["obligation"] = ob("o2")
        self.bad(d, "iff")
        d = base(); d["records"][1]["parts"][0]["role"] = "obligation"; d["records"][1]["parts"][0]["obligation"] = ob("o1")
        self.bad(d, "duplicate obligation")

    def test_split_only_independently_acceptable_actions(self):
        d = base(); d["records"][1]["parts"][0]["role"] = "obligation"; d["records"][1]["parts"][0]["obligation"] = dict(ob("o2"), acceptance="synthetic acceptance for o1")
        self.assertEqual(interp.validate(d), [])        # different records may share wording
        d = base(); d["records"][0]["parts"][1]["role"] = "obligation"; d["records"][0]["parts"][1]["obligation"] = dict(ob("o2"), acceptance="Synthetic   Acceptance for o1")
        self.bad(d, "share an acceptance condition")

    def test_duplicates_are_consolidated_keeping_every_source_reference(self):
        d = with_ob(dict(ob("o1"), source_refs=[loc(2, "d")]))
        self.assertEqual(interp.validate(d), [])
        d = with_ob(dict(ob("o1"), source_refs=[loc(77, "d")]))
        self.bad(d, "was not read")

    def test_suggestions_are_a_role_but_not_an_obligation(self):
        d = base(); d["records"][1]["parts"][1]["role"] = "suggestion"
        self.assertEqual(interp.validate(d), [])
        d["records"][1]["parts"][1]["obligation"] = ob("o9")
        self.bad(d, "iff")

    def test_uncertain_obligations_cannot_back_confident_filing(self):
        prop = lambda kind, rank=1: {"proposal_id": "p%d" % rank, "kind": kind, "target": "NEW", "change": "synthetic", "why": "synthetic", "alternatives": "none", "evidence": [loc(1, "a")],
                                     "confidence_reason": "synthetic", "authority_stage": "PROPOSE_ONLY", "capability": "CAPABILITY_GAP", "tool_name": None, "rank": rank, "confidence": "low"}
        for status in ("UNSETTLED", "UNARBITRATED"):
            for kind in ("FILE", "LINK", "METADATA", "CONSOLIDATE"):
                d = with_ob(dict(ob("o1"), adjudication_status=status)); d["proposals"] = [prop(kind)]
                self.bad(d, "cannot back a filing")
            d = with_ob(dict(ob("o1"), adjudication_status=status)); d["proposals"] = [prop("VERIFY")]
            self.assertEqual(interp.validate(d), [])
        d = with_ob(ob("o1")); d["proposals"] = [prop("FILE", 1), prop("LINK", 1)]
        self.bad(d, "ranks must be unique")

    def test_subset_validator_agrees_with_jsonschema_when_available(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        schema = interp.load_schema()
        jsonschema.Draft202012Validator.check_schema(schema)
        broken = []
        for build in (base, lambda: with_ob(ob("o1", "TRACKED", [target()])),
                      lambda: dict(base(), schema_version="librarian-interp-1"),
                      lambda: with_ob(ob("o1", "TRACKED", [dict(target(), kind="OTHER")])),
                      lambda: dict(base(), extra=1)):
            doc = build()
            ours = not interp.structural_errors(doc, schema, schema)
            theirs = jsonschema.Draft202012Validator(schema).is_valid(doc)
            if ours != theirs:
                broken.append(doc.get("schema_version"))
        self.assertEqual(broken, [])


if __name__ == "__main__":
    unittest.main()
