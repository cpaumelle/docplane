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
            "acceptance": "synthetic acceptance for " + lid, "source_refs": [], "adjudication_status": "NONE",
            "work_kind": "DELIVERY", "currency": "AS_OF_RECORD", "later_evidence": [], "scope_components": []}


def itarget(n, relation="RELATED", state="BACKLOG"):
    return {"record_id": U(n), "kind": "WORK_RECORD", "register_id": None, "entry_ref": None, "relation": relation, "work_state_seen": state, "found_by": "STRUCTURAL"}


def rowtarget(entry="K8", relation="SAME_OBLIGATION", register="prog-register"):
    return {"record_id": None, "kind": "REGISTER_ROW", "register_id": register, "entry_ref": entry, "relation": relation, "work_state_seen": "NOT_A_WORK_ITEM", "found_by": "CITED_ID"}


def comps(*ns):
    return [{"text": "synthetic scope of source %d" % n, "source_record_id": U(n)} for n in ns]


def consolidated(lid, home, *refs, **kw):
    """An obligation of record `home` that also keeps sources `refs` (record numbers), with each source's scope."""
    return dict(ob(lid), source_refs=[loc(r, "d") for r in refs], scope_components=comps(home, *refs), **kw)


def intake(n, disposition, status="EVALUATED", targets=(), obligations=(), search=None, reason=None, concerns=()):
    placement = {"WARRANTS_NEW_DELIVERABLE": "NO_HOME", "UNCERTAIN": "UNDECIDED", "CORRECTS": "CROSS_REFERENCE_ONLY", "SUPERSEDES": "CROSS_REFERENCE_ONLY"}.get(disposition, "HOME_FOUND")
    purpose = {"ADDS_OBLIGATION": "OBLIGATION", "DUPLICATES": "OBLIGATION", "CONTRIBUTES_EVIDENCE": "PROGRESS_EVIDENCE", "CORRECTS": "CORRECTION", "SUPERSEDES": "CORRECTION"}.get(disposition, "BACKGROUND")
    if status == "READ_FAILED":
        purpose = "UNDETERMINED"
    return {"record_id": U(n), "lifecycle_seen": "INBOX", "processing_status": status, "disposition": disposition, "targets": list(targets), "concern_ids": list(concerns), "placement": placement, "purpose": purpose,
            "obligation_local_ids": list(obligations),
            "search": search or {"terms": 1, "beyond_workstream": False, "included_completed": False, "limits": "synthetic", "captures_inspected": 1, "initiatives_inspected": 1, "know_access": "SEARCHED"},
            "reevaluation": {"due": status == "REEVALUATION_DUE", "trigger": "NONE"}, "uncertain_reason": reason}


def loc(n, p=None):
    return {"record_id": U(n), "part_id": p}


def base():
    return {"schema_version": "librarian-interp-2",
            "run": {"scope": {"scope_id": "s1", "initiative_ids": [U(9)], "capture_statuses": ["INBOX"], "collected_at": "2026-01-02T00:00:00Z",
                              "enumeration_limits": "synthetic", "incoming_record_ids": [], "librarian_authored_record_ids": [], "authoritative_registers": [{"register_id": "prog-register", "description": "programme register kept in git"}]},
                    "budget": {"max_changed_records": 10, "max_candidates_per_record": 5, "max_extra_reads": 30}, "interpreter_version": "t1", "prompt_hash": "0" * 64},
            "records": [
                {"record_id": U(1), "record_kind": "capture", "source_fingerprint": FP(1), "depends_on": [{"record_id": U(2), "source_fingerprint": FP(2)}],
                 "parts": [part("a", "obligation", ob("o1")), part("b", "evidence")]},
                {"record_id": U(2), "record_kind": "capture", "source_fingerprint": FP(2), "depends_on": [], "parts": [part("c", "correction"), part("d", "decision")]}],
            "mappings": [], "relations": [], "unresolved_references": [],
            "briefing": {"workstream": "synthetic", "deliverable": "synthetic deliverable", "acceptance_as_stated": None, "status_lines": [], "open_obligations": [],
                         "retractions": [], "unresolved_contradictions": [], "ready_to_close": "no", "why": "synthetic", "completeness": "COMPLETE", "omitted": []},
            "proposals": [], "incomplete": False, "intake": [], "concerns": [],
            "coverage": {"incoming_total": 0, "evaluated": 0, "uncertain_placement": 0, "read_failed": 0, "reevaluation_due": 0},
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
        self.bad(with_ob(ob("o1", "TRACKED", [done])), "cannot back an open TRACKED claim unless it explains the mismatch")   # B1: needs the uncovered part stated
        self.assertEqual(interp.validate(with_ob(dict(ob("o1", "TRACKED", [done]), scope_components=comps(1)))), [])

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
        ref = lambda **kw: dict({"token": "ab12cd34", "cited_in": loc(1, "a"), "scope_id": "s1", "outcome": "UNIQUE_CONFIRMED", "candidate_count": 1,
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
        d = with_ob(consolidated("o1", 1, 2))
        self.assertEqual(interp.validate(d), [])
        d = with_ob(dict(ob("o1"), source_refs=[loc(77, "d")], scope_components=comps(1, 77)))
        self.bad(d, "was not read")

    def test_suggestions_are_a_role_but_not_an_obligation(self):
        d = base(); d["records"][1]["parts"][1]["role"] = "suggestion"
        self.assertEqual(interp.validate(d), [])
        d["records"][1]["parts"][1]["obligation"] = ob("o9")
        self.bad(d, "iff")

    def test_uncertain_obligations_cannot_back_confident_filing(self):
        prop = lambda kind, rank=1: {"proposal_id": "p%d" % rank, "kind": kind, "target": "NEW", "change": "synthetic", "why": "synthetic", "alternatives": "none", "evidence": [loc(1, "a")],
                                     "confidence_reason": "synthetic", "authority_stage": "PROPOSE_ONLY", "capability": "CAPABILITY_GAP", "tool_name": None, "rank": rank, "confidence": "low", "provisional": False, "provisional_reason": None, "held_behind": None, "subjects": [U(1)],
                                     "effect": {"FILE": "RECORD_NEW", "LINK": "CROSS_REFERENCE_ONLY", "METADATA": "CLARIFY", "CONSOLIDATE": "CLARIFY", "VERIFY": "VERIFY"}.get(kind, "CLARIFY")}
        for status in ("UNSETTLED", "UNARBITRATED"):
            for kind in ("FILE", "LINK", "METADATA", "CONSOLIDATE"):
                d = with_ob(dict(ob("o1"), adjudication_status=status)); d["proposals"] = [prop(kind)]
                self.bad(d, "cannot back a filing")
            d = with_ob(dict(ob("o1"), adjudication_status=status)); d["proposals"] = [prop("VERIFY")]
            self.assertEqual(interp.validate(d), [])
        d = with_ob(ob("o1")); d["proposals"] = [prop("FILE", 1), prop("LINK", 1)]
        self.bad(d, "ranks must be unique")

    def incoming(self, doc, *entries):
        doc["run"]["scope"]["incoming_record_ids"] = [e["record_id"] for e in entries]
        doc["intake"] = list(entries)
        n = lambda status: sum(1 for e in entries if e["processing_status"] == status)
        doc["coverage"] = {"incoming_total": len(entries), "evaluated": n("EVALUATED"), "uncertain_placement": n("UNCERTAIN_PLACEMENT"),
                           "read_failed": n("READ_FAILED"), "reevaluation_due": n("REEVALUATION_DUE")}
        return doc

    def test_every_incoming_record_gets_an_evaluation_visibly(self):
        d = self.incoming(base(), intake(1, "CONTRIBUTES_EVIDENCE", targets=[itarget(3)]))
        self.assertEqual(interp.validate(d), [])
        d["intake"] = []
        self.bad(d, "has no evaluation")
        d = self.incoming(base(), intake(1, "CONTRIBUTES_EVIDENCE", targets=[itarget(3)]))
        d["intake"].append(copy.deepcopy(d["intake"][0]))
        self.bad(d, "evaluated twice")
        d = self.incoming(base(), intake(1, "CONTRIBUTES_EVIDENCE", targets=[itarget(3)]))
        d["coverage"]["evaluated"] = 0
        self.bad(d, "coverage summary")

    def test_failed_reads_and_uncertain_placements_stay_visible(self):
        d = base(); d["retrieval"]["reads"][0]["read_ok"] = False; d["incomplete"] = True
        self.bad(self.incoming(d, intake(1, "CONTRIBUTES_EVIDENCE", targets=[itarget(3)])), "failed to read and must say so")
        d = base(); d["retrieval"]["reads"][0]["read_ok"] = False; d["incomplete"] = True
        failed = intake(1, "UNCERTAIN", status="READ_FAILED", reason="read refused")
        self.assertEqual([e for e in interp.validate(self.incoming(d, failed)) if "intake" in e], [])
        self.bad(self.incoming(base(), intake(1, "UNCERTAIN", status="EVALUATED", reason="unclear")), "go together")
        self.bad(self.incoming(base(), intake(1, "UNCERTAIN", status="UNCERTAIN_PLACEMENT", reason=None)), "must say why")
        self.assertEqual(interp.validate(self.incoming(base(), intake(1, "UNCERTAIN", status="UNCERTAIN_PLACEMENT", reason="two plausible homes"))), [])

    def test_lifecycle_is_informational_and_never_replaces_processing(self):
        for life in ("INBOX", "DISCARDED", "ATTACHED", "PROMOTED"):
            d = self.incoming(base(), dict(intake(1, "CONTRIBUTES_EVIDENCE", targets=[itarget(3)]), lifecycle_seen=life))
            self.assertEqual(interp.validate(d), [])
        d = base(); d["run"]["scope"]["incoming_record_ids"] = [U(1)]       # an INBOX or even DISCARDED capture still needs its evaluation
        d["coverage"]["incoming_total"] = 1
        self.assertTrue(interp.validate(d))

    def test_duplicate_means_same_obligation_consolidated_with_this_record_as_a_source(self):
        dup = consolidated("o1", 1, 2)
        d = with_ob(dup); d["records"][1]["parts"][1]["role"] = "evidence"
        good = intake(2, "DUPLICATES", targets=[itarget(1, "SAME_OBLIGATION")], obligations=["o1"])
        self.assertEqual(interp.validate(self.incoming(d, good)), [])
        d = with_ob(ob("o1"))                                           # no source_ref kept: the duplicate would be dropped
        self.bad(self.incoming(d, good), "a duplicate needs")
        d = with_ob(dup)
        self.bad(self.incoming(d, intake(2, "DUPLICATES", targets=[itarget(1)], obligations=["o1"])), "a duplicate needs")
        self.bad(self.incoming(with_ob(dup), intake(2, "CONTRIBUTES_EVIDENCE", targets=[itarget(1, "SAME_OBLIGATION")])), "not a duplicate")

    def test_related_new_action_is_not_the_same_obligation(self):
        d = base(); d["records"][1]["parts"][0]["role"] = "obligation"; d["records"][1]["parts"][0]["obligation"] = ob("o2")
        ok = intake(2, "ADDS_OBLIGATION", targets=[itarget(3, "RELATED")], obligations=["o2"])
        self.assertEqual(interp.validate(self.incoming(d, ok)), [])
        self.bad(self.incoming(d, intake(2, "ADDS_OBLIGATION", targets=[itarget(3, "RELATED")], obligations=[])), "adding an obligation needs")
        self.bad(self.incoming(d, intake(2, "ADDS_OBLIGATION", targets=[], obligations=["o2"])), "adding an obligation needs")
        d2 = base(); d2["records"][0]["parts"][0]["obligation"] = ob("o1")
        self.bad(self.incoming(d2, intake(2, "ADDS_OBLIGATION", targets=[itarget(3, "RELATED")], obligations=["o1"])), "its own new obligation")

    def test_new_deliverable_requires_a_search_beyond_the_workstream_including_completed_work(self):
        wide = {"terms": 3, "beyond_workstream": True, "included_completed": True, "limits": "literal key and title terms only; capture bodies not searched", "captures_inspected": 1, "initiatives_inspected": 1, "know_access": "SEARCHED"}
        def doc(search):
            d = base(); d["concerns"] = [{"concern_id": "c1", "record_id": U(1), "part_id": "b", "text": "synthetic raised risk", "status": "UNRESOLVED"}]
            return self.incoming(d, intake(1, "WARRANTS_NEW_DELIVERABLE", search=search, concerns=["c1"]))
        self.assertEqual(interp.validate(doc(wide)), [])
        for bad in (dict(wide, beyond_workstream=False), dict(wide, included_completed=False), dict(wide, terms=0)):
            self.bad(doc(bad), "a new deliverable needs")

    def test_correction_and_supersession_need_their_relation(self):
        d = base(); d["relations"] = [rel("r1", "CORRECTS_RETRACTS")]
        c = intake(2, "CORRECTS", targets=[itarget(1)])
        self.assertEqual(interp.validate(self.incoming(d, c)), [])
        self.bad(self.incoming(base(), c), "needs a matching relation")
        self.bad(self.incoming(d, intake(2, "SUPERSEDES", targets=[itarget(1)])), "needs a matching relation")

    def test_reevaluation_when_later_evidence_changes_meaning(self):
        again = dict(intake(1, "CONTRIBUTES_EVIDENCE", targets=[itarget(3)], status="REEVALUATION_DUE"), reevaluation={"due": True, "trigger": "LATER_EVIDENCE_CHANGES_MEANING"})
        d = base(); d["intake"] = [again]
        self.assertEqual(interp.validate(d), [])               # earlier record, not incoming, may be re-evaluated
        d["intake"] = [dict(again, processing_status="EVALUATED")]
        self.bad(d, "is not incoming")

    def test_continuity_no_loss_no_duplicate_no_dropped_sources(self):
        reg = {"o1": U(500)}
        before = with_ob(consolidated("o1", 1, 2))
        after = with_ob(dict(consolidated("o1", 1, 2, 3), maps_to=U(500)))
        self.assertEqual(interp.continuity_errors(before, after, reg), [])
        self.assertTrue(any("lost" in e for e in interp.continuity_errors(before, base(), reg)))
        self.assertTrue(any("minted id" in e for e in interp.continuity_errors(before, with_ob(consolidated("o1", 1, 2)), reg)))
        self.assertTrue(any("dropped sources" in e for e in interp.continuity_errors(before, with_ob(dict(ob("o1"), maps_to=U(500), scope_components=comps(1, 2))), reg)))
        dup = copy.deepcopy(after); dup["records"][1]["parts"][0]["role"] = "obligation"
        dup["records"][1]["parts"][0]["obligation"] = dict(ob("o2"), acceptance="synthetic acceptance for o1")
        self.assertTrue(any("duplicate" in e for e in interp.continuity_errors(before, dup, reg)))

    def test_new_deliverable_must_reference_an_obligation_or_an_explicit_concern_with_evidence(self):
        wide = {"terms": 3, "beyond_workstream": True, "included_completed": True, "limits": "key and title only", "captures_inspected": 1, "initiatives_inspected": 1, "know_access": "SEARCHED"}
        d = base(); d["concerns"] = [{"concern_id": "c1", "record_id": U(1), "part_id": "b", "text": "synthetic raised risk", "status": "UNRESOLVED"}]
        self.assertEqual(interp.validate(self.incoming(d, intake(1, "WARRANTS_NEW_DELIVERABLE", search=wide, concerns=["c1"]))), [])
        self.bad(self.incoming(base(), intake(1, "WARRANTS_NEW_DELIVERABLE", search=wide)), "must reference an extracted obligation or an explicit unresolved concern")
        d = with_ob(ob("o1"))                                      # an extracted obligation is also acceptable
        self.assertEqual(interp.validate(self.incoming(d, dict(intake(1, "WARRANTS_NEW_DELIVERABLE", search=wide, obligations=["o1"]), purpose="OBLIGATION"))), [])
        d = base(); d["concerns"] = [{"concern_id": "c1", "record_id": U(2), "part_id": "c", "text": "synthetic", "status": "UNRESOLVED"}]
        self.bad(self.incoming(d, intake(1, "WARRANTS_NEW_DELIVERABLE", search=wide, concerns=["c1"])), "not an explicit concern raised by this record")
        d = base(); d["concerns"] = [{"concern_id": "c1", "record_id": U(1), "part_id": "zz", "text": "synthetic", "status": "UNRESOLVED"}]
        self.bad(self.incoming(d, intake(1, "WARRANTS_NEW_DELIVERABLE", search=wide, concerns=["c1"])), "source evidence part does not exist")
        d = with_ob(ob("o1")); d["concerns"] = [{"concern_id": "c1", "record_id": U(1), "part_id": "a", "text": "synthetic", "status": "UNRESOLVED"}]
        self.bad(self.incoming(d, intake(1, "WARRANTS_NEW_DELIVERABLE", search=wide, concerns=["c1"])), "points at evidence")
        d = base(); d["concerns"] = [{"concern_id": "c1", "record_id": U(1), "part_id": "b", "text": "synthetic", "status": "UNRESOLVED"}]
        e = dict(intake(1, "WARRANTS_NEW_DELIVERABLE", search=wide, concerns=["c1"]), placement="HOME_FOUND")
        self.bad(self.incoming(d, e), "no existing home")

    def test_no_action_or_acceptance_can_be_attached_to_a_concern(self):
        d = base(); d["concerns"] = [{"concern_id": "c1", "record_id": U(1), "part_id": "b", "text": "synthetic", "status": "UNRESOLVED", "acceptance": "invented"}]
        self.assertTrue(interp.validate(d))

    def test_the_librarians_own_notes_cannot_establish_canonical_facts(self):
        d = base(); d["run"]["scope"]["librarian_authored_record_ids"] = [U(2)]
        line = lambda *ids: {"text": "scope claim", "confirmation": CONF, "evidence": [loc(i) for i in ids]}
        d["briefing"]["status_lines"] = [line(2)]
        self.bad(d, "rests only on records the librarian wrote")
        d["briefing"]["status_lines"] = [line(2, 3)]
        self.assertEqual(interp.validate(d), [])
        d["relations"] = [dict(rel("r1", "SUPERSEDES", "SEMANTIC"), evidence=[loc(2, "c")])]
        self.bad(d, "relation r1 rests only on records the librarian wrote")
        d = base(); d["run"]["scope"]["librarian_authored_record_ids"] = [U(77)]
        self.bad(d, "was not read")

    def test_a_cross_reference_is_not_a_rehoming(self):
        prop = lambda kind, effect, ev: {"proposal_id": "p1", "kind": kind, "target": "NEW", "change": "synthetic", "why": "synthetic", "alternatives": "none", "evidence": [loc(ev)],
            "confidence_reason": "synthetic", "authority_stage": "PROPOSE_ONLY", "capability": "CAPABILITY_GAP", "tool_name": None, "rank": 1, "confidence": "medium", "effect": effect, "provisional": False, "provisional_reason": None, "held_behind": None, "subjects": [U(ev)]}
        cross = intake(1, "CONTRIBUTES_EVIDENCE", targets=[itarget(3)]); cross["placement"] = "CROSS_REFERENCE_ONLY"
        d = self.incoming(base(), cross); d["proposals"] = [prop("LINK", "REHOME", 1)]
        self.bad(d, "rehoming is not supported")
        d["proposals"] = [prop("FILE", "REHOME", 1)]
        self.bad(d, "rehoming is not supported")
        d["proposals"] = [prop("FILE", "CROSS_REFERENCE_ONLY", 1)]
        self.bad(d, "FILE means rehoming or a new record")
        d["proposals"] = [prop("LINK", "CROSS_REFERENCE_ONLY", 1)]
        self.assertEqual(interp.validate(d), [])
        home = self.incoming(base(), intake(1, "CONTRIBUTES_EVIDENCE", targets=[itarget(3)]))
        home["proposals"] = [prop("FILE", "REHOME", 1)]
        self.assertEqual(interp.validate(home), [])
        d["proposals"] = [prop("VERIFY", "CLARIFY", 1)]
        self.bad(d, "has effect VERIFY")

    def test_a_shorter_briefing_cannot_imply_completeness(self):
        d = base(); d["retrieval"]["not_retrieved"] = [{"what": "older history", "why": "outside the window"}]
        self.bad(d, "must say INCOMPLETE")
        d["briefing"]["completeness"] = "INCOMPLETE"
        self.bad(d, "must say INCOMPLETE")                       # still needs the omitted list
        d["briefing"]["omitted"] = [{"what": "older history", "why": "outside the window"}]
        self.assertEqual(interp.validate(d), [])
        d2 = base(); d2["briefing"]["omitted"] = [{"what": "x", "why": "y"}]
        self.bad(d2, "cannot be COMPLETE")

    # ---- regression tests from the held-out scoring (synthetic fixtures; regression evidence, not held-out validation)

    def test_regression_completed_evidence_keeps_its_purpose_without_an_initiative_home(self):
        reg_evidence = dict(intake(1, "CONTRIBUTES_EVIDENCE", targets=[rowtarget("K8")]), purpose="COMPLETED_EVIDENCE")
        self.assertEqual(interp.validate(self.incoming(base(), reg_evidence)), [])                    # a pinned register row is an evidence target
        no_home = dict(intake(1, "CONTRIBUTES_EVIDENCE"), purpose="COMPLETED_EVIDENCE", placement="NO_HOME")
        self.assertEqual(interp.validate(self.incoming(base(), no_home)), [])                         # completed evidence needs no home
        self.bad(self.incoming(base(), dict(no_home, placement="HOME_FOUND")), "evidence must name what it contributes to")
        self.assertEqual(interp.validate(self.incoming(base(), dict(intake(1, "UNCERTAIN", status="UNCERTAIN_PLACEMENT", reason="completion claimed, not yet verified"), purpose="COMPLETED_EVIDENCE"))), [])   # P3: genuinely unverified stays uncertain
        d = with_ob(ob("o1"))                                                                        # missing placement must not manufacture an open obligation
        self.bad(self.incoming(d, dict(intake(1, "CONTRIBUTES_EVIDENCE", obligations=["o1"], targets=[rowtarget("K8")]), purpose="COMPLETED_EVIDENCE")), "completed evidence must not manufacture an open obligation")
        bad_row = dict(rowtarget("K8"), register_id="undeclared-register")
        self.bad(self.incoming(base(), dict(intake(1, "CONTRIBUTES_EVIDENCE", targets=[bad_row]), purpose="COMPLETED_EVIDENCE")), "declared authoritative register")
        self.assertEqual(interp.validate(self.incoming(with_ob(ob("o1", state="DONE_IN_RECORD")), dict(intake(1, "CONTRIBUTES_EVIDENCE", obligations=["o1"], targets=[rowtarget("K8")]), purpose="COMPLETED_EVIDENCE"))), [])

    def test_regression_a_historical_action_is_reconciled_against_later_evidence_already_retrieved(self):
        stale = dict(ob("o1"), later_evidence=[loc(3, "x")])                                        # later evidence retrieved, still presented as plainly open
        self.bad(with_ob(stale), "cannot be presented as plainly as-of-record")
        uncertain = dict(ob("o1"), later_evidence=[loc(3, "x")], currency="UNCERTAIN_CURRENT", tracking={"status": "CANDIDATE", "targets": [target(3, basis="SEMANTIC", state="BACKLOG")]})
        self.assertEqual(interp.validate(with_ob(uncertain)), [])                                    # status stays uncertain; the home is a candidate
        claimed_home = dict(uncertain, tracking={"status": "TRACKED", "targets": [target(3)]})
        self.bad(with_ob(claimed_home), "only a candidate, not TRACKED")
        self.bad(with_ob(dict(ob("o1"), later_evidence=[loc(77, "x")], currency="UNCERTAIN_CURRENT")), "was not read")

    def test_regression_prevention_work_stays_independent_of_recovery(self):
        def two(recovery_state, prevention_state, prevention_later=()):
            d = base()
            d["records"][0]["parts"][1]["role"] = "obligation"
            d["records"][0]["parts"][0]["obligation"] = dict(ob("rec", state=recovery_state), work_kind="RECOVERY")
            d["records"][0]["parts"][1]["obligation"] = dict(ob("prev", state=prevention_state), work_kind="PREVENTION", acceptance="synthetic prevention acceptance", later_evidence=list(prevention_later),
                                                              currency="UNCERTAIN_CURRENT" if prevention_later else "AS_OF_RECORD")
            return d
        before = two("OPEN", "OPEN"); reg = {"rec": U(501), "prev": U(502)}
        after = two("DONE_IN_RECORD", "OPEN")
        for d in (before, after):
            for p in d["records"][0]["parts"]: p["obligation"]["maps_to"] = reg[p["obligation"]["local_id"]]
        self.assertEqual(interp.continuity_errors(before, after, reg), [])                           # recovery done, prevention untouched
        wrongly = two("DONE_IN_RECORD", "SUPERSEDED")
        for p in wrongly["records"][0]["parts"]: p["obligation"]["maps_to"] = reg[p["obligation"]["local_id"]]
        self.assertTrue(any("prevention obligation prev" in e for e in interp.continuity_errors(before, wrongly, reg)))
        own = two("DONE_IN_RECORD", "SUPERSEDED", prevention_later=[loc(3, "x")])
        for p in own["records"][0]["parts"]: p["obligation"]["maps_to"] = reg[p["obligation"]["local_id"]]
        self.assertEqual(interp.continuity_errors(before, own, reg), [])                             # prevention may change with its own evidence

    def test_regression_two_captures_named_one_preventive_obligation_keep_both_sources_and_the_cross_repo_scope(self):
        d = base()
        d["records"][1]["parts"][0]["role"] = "obligation"
        d["records"][1]["parts"][0]["obligation"] = dict(ob("prev"), work_kind="PREVENTION", source_refs=[loc(1, "b")], scope_components=comps(2, 1))
        both = [intake(2, "ADDS_OBLIGATION", targets=[itarget(3)], obligations=["prev"]), intake(1, "DUPLICATES", targets=[itarget(2, "SAME_OBLIGATION")], obligations=["prev"])]
        d = self.incoming(d, *both)
        self.bad(d, "a DUPLICATES relation between them is required")
        d["relations"] = [dict(rel("r1", "DUPLICATES", "STRUCTURAL"), **{"from": loc(1), "to": loc(2), "evidence": [loc(3)]})]
        self.assertEqual(interp.validate(d), [])
        lost = copy.deepcopy(d); lost["records"][1]["parts"][0]["obligation"]["scope_components"] = comps(2)
        self.bad(lost, "must keep each source's own scope")
        self.bad(self.incoming(base(), intake(1, "DUPLICATES", targets=[itarget(2, "SAME_OBLIGATION")])), "a duplicate needs")

    def test_regression_new_work_or_duplicate_disposal_needs_captures_inspected_or_is_provisional(self):
        def doc(captures, provisional=False, kind="CAPABILITY_GAP"):
            search = {"terms": 2, "beyond_workstream": True, "included_completed": True, "limits": "synthetic", "captures_inspected": captures, "initiatives_inspected": 3, "know_access": "SEARCHED"}
            d = with_ob(ob("o1")); d["records"][0]["parts"][0]["obligation"]["tracking"] = {"status": "NOT_FOUND", "targets": []}
            e = dict(intake(1, "ADDS_OBLIGATION", targets=[itarget(3)], obligations=["o1"], search=search))
            d = self.incoming(d, e)
            d["proposals"] = [{"proposal_id": "p1", "kind": "FILE", "target": "NEW", "change": "synthetic", "why": "synthetic", "alternatives": "none", "evidence": [loc(1, "a")],
                               "confidence_reason": "synthetic", "authority_stage": "PROPOSE_ONLY", "capability": "EXISTING_TOOL", "tool_name": "docplane_work_capture", "rank": 1, "confidence": "medium",
                               "effect": "RECORD_NEW", "provisional": provisional, "provisional_reason": "captures were not searched" if provisional else None, "held_behind": None, "subjects": [U(1)]}]
            return d
        self.assertEqual(interp.validate(doc(2)), [])
        self.bad(doc(0), "needs relevant captures and initiatives inspected first")
        self.assertEqual(interp.validate(doc(0, provisional=True)), [])
        d = doc(2); d["proposals"][0]["provisional_reason"] = "stated without being provisional"
        self.bad(d, "states why, and only a provisional one does")

    def test_regression_missing_know_access_stays_explicit(self):
        search = lambda know: {"terms": 1, "beyond_workstream": False, "included_completed": False, "limits": "synthetic", "captures_inspected": 1, "initiatives_inspected": 1, "know_access": know}
        d = self.incoming(base(), intake(1, "CONTRIBUTES_EVIDENCE", targets=[itarget(3)], search=search("NOT_SEARCHED_NO_CREDENTIAL")))
        self.bad(d, "KNOW was not searched")
        d["briefing"]["completeness"] = "INCOMPLETE"; d["briefing"]["omitted"] = [{"what": "KNOW pages were not searched", "why": "no docs credential on this path"}]
        self.assertEqual([e for e in interp.validate(d) if "KNOW" in e], [])
        bad = self.incoming(base(), intake(1, "CONTRIBUTES_EVIDENCE", targets=[itarget(3)], search=search("MAYBE")))
        self.assertTrue(interp.validate(bad))

    def test_regression_scoring_c3_a_record_may_carry_one_consolidated_action_and_one_distinct_action(self):
        # record 2 = the earlier preventive item (its home); record 1 (incoming) restates its first action and adds a distinct second action
        d = base()
        d["records"][1]["parts"][0]["role"] = "obligation"
        d["records"][1]["parts"][0]["obligation"] = dict(ob("prev-a"), work_kind="PREVENTION", source_refs=[loc(1, "a")], scope_components=comps(2, 1))
        d["records"][0]["parts"][0]["obligation"] = dict(ob("prev-b"), work_kind="PREVENTION", acceptance="synthetic distinct second action")
        e = intake(1, "ADDS_OBLIGATION", targets=[itarget(2, "SAME_OBLIGATION"), itarget(3, "RELATED")], obligations=["prev-a", "prev-b"])
        self.assertEqual(interp.validate(self.incoming(d, e)), [])
        self.bad(self.incoming(d, intake(1, "ADDS_OBLIGATION", targets=[itarget(3, "SAME_OBLIGATION"), itarget(3, "RELATED")], obligations=["prev-a", "prev-b"])), "not the home of an obligation consolidated here")
        only_merged = intake(1, "ADDS_OBLIGATION", targets=[itarget(2, "SAME_OBLIGATION"), itarget(3, "RELATED")], obligations=["prev-a"])
        self.bad(self.incoming(d, only_merged), "needs its own new obligation")
        stray = copy.deepcopy(d); stray["records"][1]["parts"][0]["obligation"]["source_refs"] = []; stray["records"][1]["parts"][0]["obligation"]["scope_components"] = []
        self.bad(self.incoming(stray, e), "other obligations here must be consolidated ones")

    def test_regression_scoring_c2_filing_on_an_uncertain_current_status_waits_behind_verification(self):
        uncertain = dict(ob("o1"), currency="UNCERTAIN_CURRENT", later_evidence=[loc(3, "x")], tracking={"status": "CANDIDATE", "targets": [target(3, basis="SEMANTIC", state="BACKLOG")]})
        def doc(held=None, provisional=True, verify=True):
            d = with_ob(uncertain)
            d = self.incoming(d, dict(intake(1, "UNCERTAIN", status="UNCERTAIN_PLACEMENT", reason="later cleanups may have changed the state", obligations=["o1"]), purpose="OBLIGATION"))
            mk = lambda pid, kind, effect, rank, **kw: dict({"proposal_id": pid, "kind": kind, "target": "NEW", "change": "synthetic", "why": "synthetic", "alternatives": "none", "evidence": [loc(1, "a")],
                "confidence_reason": "synthetic", "authority_stage": "PROPOSE_ONLY", "capability": "CAPABILITY_GAP", "tool_name": None, "rank": rank, "confidence": "low", "effect": effect,
                "provisional": False, "provisional_reason": None, "held_behind": None, "subjects": [U(1)]}, **kw)
            filing = mk("filing", "FILE", "REHOME", 2, provisional=provisional, provisional_reason="status unverified" if provisional else None, held_behind=held)
            d["proposals"] = ([mk("verify", "VERIFY", "VERIFY", 1)] if verify else []) + [filing]
            return d
        # a rehoming proposal also needs HOME_FOUND; this record is UNDECIDED, so the held filing is refused for that reason too: use a clarifying next action instead
        d = doc(held="verify"); d["proposals"][1]["kind"] = "NEXT_ACTION"; d["proposals"][1]["effect"] = "CLARIFY"
        self.assertEqual(interp.validate(d), [])
        d = doc(); d["proposals"][1]["kind"] = "LINK"; d["proposals"][1]["effect"] = "CROSS_REFERENCE_ONLY"
        self.bad(d, "must be provisional and held behind a verification proposal")
        d = doc(held="verify", provisional=False); d["proposals"][1]["kind"] = "LINK"; d["proposals"][1]["effect"] = "CROSS_REFERENCE_ONLY"
        self.bad(d, "must be provisional and held behind a verification proposal")
        d = doc(held="nowhere"); d["proposals"][1]["kind"] = "LINK"; d["proposals"][1]["effect"] = "CROSS_REFERENCE_ONLY"
        self.bad(d, "held behind a verification proposal that exists")
        d = doc(held="filing"); d["proposals"][1]["kind"] = "LINK"; d["proposals"][1]["effect"] = "CROSS_REFERENCE_ONLY"
        self.bad(d, "held behind a verification proposal that exists")

    def test_regression_scoring_c1_done_in_the_register_is_completion_evidence_not_an_open_obligation(self):
        done = with_ob(dict(ob("o1", state="DONE_IN_RECORD"), currency="RECONCILED_CURRENT", later_evidence=[loc(3, "x")]))
        e = dict(intake(1, "CONTRIBUTES_EVIDENCE", obligations=["o1"], targets=[rowtarget("K8")]), purpose="COMPLETED_EVIDENCE")
        self.assertEqual(interp.validate(self.incoming(done, e)), [])
        listed_open = copy.deepcopy(done); listed_open["briefing"]["open_obligations"] = [{"local_id": "o1", "original_record": U(1), "text": "x", "tracking_status": "NOT_FOUND", "coverage": "NO_TARGET"}]
        self.bad(self.incoming(listed_open, e), "disagrees with its extraction")           # the briefing cannot list a done obligation as open

    # ---- regression tests from the bounded review of bc6a3d26 (synthetic; regression evidence, not independent validation)

    def test_review_b1_a_completed_tracker_cannot_back_a_confident_open_tracked_claim(self):
        reg_done = target(kind="AUTHORITATIVE_REGISTER", register="prog-register", entry="K8", disp="DONE", state="NOT_APPLICABLE", cov=cover())
        o = ob("o1", status="TRACKED", targets=[reg_done])
        self.bad(self.incoming(with_ob(o), dict(intake(1, "UNCERTAIN", status="UNCERTAIN_PLACEMENT", reason="no work target", obligations=["o1"]), purpose="OBLIGATION")), "cannot back an open TRACKED claim")
        self.bad(self.incoming(with_ob(o), intake(1, "ADDS_OBLIGATION", targets=[itarget(3)], obligations=["o1"])), "cannot back an open TRACKED claim")
        self.bad(with_ob(o), "cannot back an open TRACKED claim")                                        # independent of the intake purpose label
        # legitimate added scope is preserved: the tracker covers part, and the part it does not cover is stated
        partial = target(kind="AUTHORITATIVE_REGISTER", register="prog-register", entry="K8", disp="DONE", state="NOT_APPLICABLE", cov=cover("DOES_NOT_ACTIVELY_COVER", "PARTIAL_SCOPE"))
        added = dict(ob("o1", status="TRACKED", targets=[partial]), scope_components=comps(1))
        self.assertEqual(interp.validate(with_ob(added)), [])
        self.bad(with_ob(dict(added, scope_components=[])), "cannot back an open TRACKED claim")           # the added scope must be stated
        # the other honest ways to record the same facts
        self.assertEqual(interp.validate(with_ob(dict(ob("o1", status="TRACKED", targets=[reg_done], state="DONE_IN_RECORD"), currency="RECONCILED_CURRENT", later_evidence=[loc(3, "x")]))), [])
        cand = dict(ob("o1", status="CANDIDATE", targets=[target(3, basis="SEMANTIC")]), currency="UNCERTAIN_CURRENT", later_evidence=[loc(3, "x")])
        self.assertEqual(interp.validate(with_ob(cand)), [])
        mixed = dict(ob("o1", status="TRACKED", targets=[reg_done, target(3)]))                          # one live tracker remains: not every tracker is done
        self.assertEqual(interp.validate(with_ob(mixed)), [])

    def test_review_b2_missing_placement_does_not_reject_a_genuine_decision_or_uncertain_concern(self):
        q = with_ob(dict(ob("o1"), work_kind="DECISION"))
        for purpose in ("OBLIGATION", "DECISION", "BACKGROUND", "HANDOVER"):
            d = self.incoming(copy.deepcopy(q), dict(intake(1, "UNCERTAIN", status="UNCERTAIN_PLACEMENT", reason="register or WORK is an owner call", obligations=["o1"]), purpose=purpose))
            self.assertEqual(interp.validate(d), [], purpose)
        wide = {"terms": 3, "beyond_workstream": True, "included_completed": True, "limits": "synthetic", "captures_inspected": 1, "initiatives_inspected": 1, "know_access": "SEARCHED"}
        new = self.incoming(copy.deepcopy(q), dict(intake(1, "WARRANTS_NEW_DELIVERABLE", obligations=["o1"], search=wide), purpose="DECISION"))
        self.assertEqual(interp.validate(new), [])
        # but completed evidence never carries an open obligation
        self.bad(self.incoming(copy.deepcopy(q), dict(intake(1, "UNCERTAIN", status="UNCERTAIN_PLACEMENT", reason="x", obligations=["o1"]), purpose="COMPLETED_EVIDENCE")), "completed evidence must not manufacture")

    def test_review_b3_filing_a_still_uncertain_record_waits_behind_verification_even_if_the_evidence_list_omits_it(self):
        u = dict(ob("o1"), currency="UNCERTAIN_CURRENT", later_evidence=[loc(3, "x")], tracking={"status": "CANDIDATE", "targets": [target(3, basis="SEMANTIC")]})
        def doc(subjects, held, provisional, with_verify=True):
            d = self.incoming(with_ob(u), dict(intake(1, "UNCERTAIN", status="UNCERTAIN_PLACEMENT", reason="the source may be retired", obligations=["o1"]), purpose="OBLIGATION"))
            mk = lambda pid, kind, effect, rank, **kw: dict({"proposal_id": pid, "kind": kind, "target": "NEW", "change": "synthetic", "why": "synthetic", "alternatives": "none", "evidence": [loc(2, "c")],
                "confidence_reason": "synthetic", "authority_stage": "PROPOSE_ONLY", "capability": "CAPABILITY_GAP", "tool_name": None, "rank": rank, "confidence": "low", "effect": effect,
                "provisional": False, "provisional_reason": None, "held_behind": None, "subjects": []}, **kw)
            filing = mk("pr5", "LINK", "CROSS_REFERENCE_ONLY", 2, subjects=subjects, held_behind=held, provisional=provisional, provisional_reason="status unverified" if provisional else None)
            d["proposals"] = ([mk("pr8", "VERIFY", "VERIFY", 1)] if with_verify else []) + [filing]
            return d
        self.bad(doc([U(1)], None, False), "must be provisional and held behind a verification proposal")      # evidence cites record 2, the subject is the uncertain record 1
        self.bad(doc([U(1)], "pr8", False), "must be provisional and held behind")
        self.assertEqual(interp.validate(doc([U(1)], "pr8", True)), [])
        self.bad(doc([], "pr8", True), "names its subject records")                                                # a subject is required
        self.bad(doc([U(77)], "pr8", True), "was not read in this run")
        self.bad(doc([U(1)], "pr8", True, with_verify=False), "held behind a verification proposal that exists")
        d = doc([U(1)], "pr8", True); d["proposals"][0]["kind"] = "NEXT_ACTION"              # a next action whose effect is VERIFY is a verification proposal
        self.assertEqual(interp.validate(d), [])
        d["proposals"][0]["effect"] = "CLARIFY"; d["proposals"][0]["kind"] = "VERIFY"
        self.assertTrue(interp.validate(d))

    def test_review_unread_or_withheld_records_are_undetermined_and_uncertain(self):
        def unread(purpose, disp="UNCERTAIN"):
            d = base(); d["retrieval"]["reads"][0]["read_ok"] = False; d["incomplete"] = True
            return self.incoming(d, dict(intake(1, disp, status="READ_FAILED", reason="withheld"), purpose=purpose))
        self.assertEqual([e for e in interp.validate(unread("UNDETERMINED")) if "intake" in e], [])
        self.bad(unread("BACKGROUND"), "an unread or withheld record has an UNDETERMINED purpose")
        ok = self.incoming(base(), dict(intake(1, "UNCERTAIN", status="UNCERTAIN_PLACEMENT", reason="ambiguous"), purpose="UNDETERMINED"))       # readable but unclassifiable: allowed
        self.assertEqual(interp.validate(ok), [])
        self.bad(self.incoming(base(), dict(intake(1, "CONTRIBUTES_EVIDENCE", targets=[itarget(3)]), purpose="UNDETERMINED")), "goes with an UNCERTAIN disposition")

    def test_review_inspection_counts_cannot_exceed_what_this_run_read(self):
        search = lambda c, i: {"terms": 2, "beyond_workstream": True, "included_completed": True, "limits": "x", "captures_inspected": c, "initiatives_inspected": i, "know_access": "SEARCHED"}
        d = with_ob(ob("o1")); d["records"][0]["parts"][0]["obligation"]["tracking"] = {"status": "NOT_FOUND", "targets": []}
        d["retrieval"]["reads"] = [r for r in d["retrieval"]["reads"] if r["record_id"] in (U(1), U(2))]
        self.bad(self.incoming(copy.deepcopy(d), intake(1, "ADDS_OBLIGATION", targets=[itarget(2)], obligations=["o1"], search=search(5, 1))), "but only 1 other records were read")
        self.bad(self.incoming(copy.deepcopy(d), intake(1, "ADDS_OBLIGATION", targets=[itarget(2)], obligations=["o1"], search=search(1, 9))), "but only 1 other records were read")
        self.assertEqual(interp.validate(self.incoming(copy.deepcopy(d), intake(1, "ADDS_OBLIGATION", targets=[itarget(2)], obligations=["o1"], search=search(1, 1)))), [])

    def test_review_later_evidence_may_cite_a_pinned_authoritative_register_row(self):
        row = lambda reg="prog-register": {"register_id": reg, "entry_ref": "K8"}
        done = dict(ob("o1", state="DONE_IN_RECORD"), currency="RECONCILED_CURRENT", later_evidence=[row()])
        self.assertEqual(interp.validate(with_ob(done)), [])
        self.bad(with_ob(dict(done, later_evidence=[row("undeclared")])), "not declared authoritative")
        self.bad(with_ob(dict(ob("o1"), later_evidence=[row()])), "cannot be presented as plainly as-of-record")      # with later evidence the status is not plainly as-of-record

    def test_review_unavailable_know_access_needs_a_reason(self):
        search = {"terms": 1, "beyond_workstream": False, "included_completed": False, "limits": "x", "captures_inspected": 1, "initiatives_inspected": 1, "know_access": "NOT_SEARCHED_OTHER"}
        d = self.incoming(base(), intake(1, "CONTRIBUTES_EVIDENCE", targets=[itarget(3)], search=search))
        d["briefing"]["completeness"] = "INCOMPLETE"; d["briefing"]["omitted"] = [{"what": "KNOW pages were not searched", "why": "n/a"}]
        self.bad(d, "needs a reason")
        d["briefing"]["omitted"] = [{"what": "KNOW pages were not searched", "why": "the docs service refused the read"}]
        self.assertEqual(interp.validate(d), [])

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
