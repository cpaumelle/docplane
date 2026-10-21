"""Per-input evaluations against the read log. Synthetic logs only; regression evidence from the held-out scoring, not held-out validation."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from work_worker import evaluations as ev

A, B, C, X = "aaaaaaaa-0000-4000-8000-000000000001", "bbbbbbbb-0000-4000-8000-000000000002", "cccccccc-0000-4000-8000-000000000003", "dddddddd-0000-4000-8000-000000000004"
INI, INI2 = "11111111-0000-4000-8000-000000000001", "22222222-0000-4000-8000-000000000002"


def log(*rows):
    return list(rows)


def cap(cid):
    return {"tool": "docplane_work_capture_get", "args": {"capture_id": cid}, "ok": True, "ids_returned": [cid]}


def hist(iid, offset=0):
    return {"tool": "docplane_work_get", "args": {"initiative_id": iid, "activity_offset": offset}, "ok": True, "ids_returned": [iid]}


def listing(*ids):
    return {"tool": "docplane_work_initiatives", "args": {}, "ok": True, "ids_returned": list(ids)}


def evaluation(n, cid, scope_caps, scope_inis=(), **kw):
    e = {"input": n, "capture_id": cid, "proposed_disposition": "CONTRIBUTES_EVIDENCE", "relation_to_existing": {"class": "RELATED"}, "provisional": False, "provisional_reason": None,
         "search_scope": {"captures_read": list(scope_caps), "initiatives_read": list(scope_inis), "know": "not searched: no docs credential"}}
    e.update(kw)
    return e


class SearchScope(unittest.TestCase):
    LOG = log(cap(A), cap(B), cap(C), listing(INI, INI2), hist(INI, 0), hist(INI, 30), {"tool": "register", "args": {"grep": "K8"}, "ok": True})

    def test_derived_scope_always_contains_the_input_itself_and_only_what_was_opened(self):
        s = ev.derive_search_scope(self.LOG, A, [B, X, INI, INI2])
        self.assertEqual(s["captures_read"], [A, B])                        # X was never opened; the input itself is first
        self.assertEqual(s["initiatives_read"], [INI])                      # INI2 was listed, not opened
        self.assertEqual(s["activity_offsets_opened"][INI], [0, 30])
        self.assertEqual(s["run_level"], {"initiatives_listed": 2, "histories_opened": 1, "pinned_register_queries": 1})
        self.assertTrue(s["know"].startswith("not searched"))

    def test_scopes_differ_per_input_when_their_cited_records_differ(self):
        a, b = ev.derive_search_scope(self.LOG, A, [B]), ev.derive_search_scope(self.LOG, B, [C])
        self.assertNotEqual(a["captures_read"], b["captures_read"])

    def test_regression_a_generic_pair_copied_across_inputs_and_missing_the_input_is_refused(self):
        generic = [B, C]                                                     # the same pair for every input, none of them the input itself
        errors = ev.validate_evaluations([evaluation(1, A, generic), evaluation(2, B, generic)], self.LOG)
        self.assertTrue(any("must include the input itself" in e for e in errors), errors)
        self.assertTrue(any("identical capture scope" in e for e in errors), errors)

    def test_claims_are_checked_against_the_log(self):
        errors = ev.validate_evaluations([evaluation(1, A, [A, X], [INI2])], self.LOG)
        self.assertTrue(any("capture %s was read but the log shows it was not" % X in e for e in errors))
        self.assertTrue(any("initiative %s was read but the log shows no history opened" % INI2 in e for e in errors))

    def test_a_derived_scope_passes(self):
        evals = [evaluation(1, A, ev.derive_search_scope(self.LOG, A, [B])["captures_read"], [INI]),
                 evaluation(2, B, ev.derive_search_scope(self.LOG, B, [C])["captures_read"], [INI])]
        self.assertEqual(ev.validate_evaluations(evals, self.LOG), [])

    def test_know_access_is_explicit(self):
        e = evaluation(1, A, [A])
        e["search_scope"]["know"] = ""
        self.assertTrue(any("KNOW access must be stated" in x for x in ev.validate_evaluations([e], self.LOG)))
        e["search_scope"]["know"] = "searched: 3 pages read"
        self.assertEqual(ev.validate_evaluations([e], self.LOG), [])

    def test_regression_new_work_or_duplicate_disposal_without_other_captures_is_provisional(self):
        only_self = evaluation(1, A, [A], proposed_disposition="ADDS_OBLIGATION")
        self.assertTrue(any("must be provisional" in e for e in ev.validate_evaluations([only_self], self.LOG)))
        provisional = evaluation(1, A, [A], proposed_disposition="ADDS_OBLIGATION", provisional=True, provisional_reason="no other capture was inspected before proposing")
        self.assertEqual(ev.validate_evaluations([provisional], self.LOG), [])
        inspected = evaluation(1, A, [A, B], proposed_disposition="DUPLICATES")
        self.assertEqual(ev.validate_evaluations([inspected], self.LOG), [])
        self.assertTrue(any("states why" in e for e in ev.validate_evaluations([evaluation(1, A, [A, B], provisional=True)], self.LOG)))

    def test_review_unread_or_withheld_inputs_get_a_low_confidence_uncertain_disposition(self):
        withheld = evaluation(1, A, [A], read_status="WITHHELD", proposed_disposition="CONTRIBUTES_EVIDENCE", confidence="high")
        errors = ev.validate_evaluations([withheld], self.LOG)
        self.assertTrue(any("low-confidence UNCERTAIN" in e for e in errors), errors)
        good = evaluation(1, A, [A], read_status="WITHHELD", proposed_disposition="UNCERTAIN", confidence="low", purpose="UNDETERMINED")
        self.assertEqual(ev.validate_evaluations([good], self.LOG), [])
        self.assertTrue(any("UNDETERMINED purpose" in e for e in ev.validate_evaluations([dict(good, purpose="BACKGROUND")], self.LOG)))

    def test_review_know_not_searched_needs_a_reason(self):
        e = evaluation(1, A, [A]); e["search_scope"]["know"] = "not searched"
        self.assertTrue(any("no reason is given" in x for x in ev.validate_evaluations([e], self.LOG)))
        e["search_scope"]["know"] = "not searched: no docs credential on this path"
        self.assertEqual(ev.validate_evaluations([e], self.LOG), [])
        e["search_scope"]["know"] = "unknown"
        self.assertTrue(any("must be stated explicitly" in x for x in ev.validate_evaluations([e], self.LOG)))

    def test_failed_or_refused_reads_do_not_count_as_opened(self):
        failed = log(dict(cap(A), ok=False), cap(B))
        self.assertEqual(ev.summarise(failed)["captures_opened"], {B})


if __name__ == "__main__":
    unittest.main()
