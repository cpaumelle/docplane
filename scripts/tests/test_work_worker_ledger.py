"""Synthetic lifecycle scenarios; no production content or access."""
from copy import deepcopy
import sys
from pathlib import Path
from uuid import UUID
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from work_worker.ledger import reconcile, decide, identity


def uid(n):
    return str(UUID(int=n))


def example(n=1):
    return {"subject_id": uid(1), "kind": "UNRECORDED_RESIDUAL",
            "obligation_id": uid(n), "evidence": [
                {"record_id": uid(2), "revision_id": uid(3)}]}


class LedgerTests(unittest.TestCase):
    def test_distinct_residuals_and_idempotent_repeat(self):
        values = [example(4), example(5), example(6)]
        state = reconcile({}, values, evaluated_subjects=[uid(1)])
        self.assertEqual(len(state), 3)
        self.assertEqual(reconcile(state, values, evaluated_subjects=[uid(1)]), state)

    def test_partial_scan_and_absence_never_close(self):
        state = reconcile({}, [example()], evaluated_subjects=[uid(1)])
        partial = reconcile(state, [], evaluated_subjects=[])
        self.assertEqual(partial[identity(example())]["detection"], "NOT_REEVALUATED")
        absent = reconcile(partial, [], evaluated_subjects=[uid(1)])
        self.assertEqual(absent[identity(example())]["detection"], "NOT_DETECTED")
        self.assertEqual(absent[identity(example())]["status"], "OPEN")
        self.assertEqual(state[identity(example())]["detection"], "DETECTED")

    def test_review_sticks_until_evidence_revision_changes(self):
        for status in ("REJECTED", "RESOLVED"):
            value = example()
            state = reconcile({}, [value], evaluated_subjects=[uid(1)])
            reviewed = decide(state, identity(value), status=status,
                              actor_id=uid(7), evidence=[uid(8)])
            repeated = reconcile(reviewed, [value], evaluated_subjects=[uid(1)])
            self.assertEqual(repeated[identity(value)]["status"], status)
            changed = deepcopy(value)
            changed["evidence"][0]["revision_id"] = uid(9)
            reopened = reconcile(repeated, [changed], evaluated_subjects=[uid(1)])
            self.assertEqual(reopened[identity(value)]["status"], "OPEN")
            self.assertEqual(len(reopened[identity(value)]["decisions"]), 1)

    def test_refuses_uncovered_and_duplicate_candidates(self):
        with self.assertRaises(ValueError):
            reconcile({}, [example()], evaluated_subjects=[])
        with self.assertRaises(ValueError):
            reconcile({}, [example(), example()], evaluated_subjects=[uid(1)])

    def test_no_unvalidated_text_is_copied(self):
        for field in ("body", "origin", "metadata", "requested_action"):
            value = example()
            value[field] = "untrusted upstream text"
            with self.assertRaises(ValueError):
                reconcile({}, [value], evaluated_subjects=[uid(1)])

    def test_versioned_evidence_is_required(self):
        value = example()
        del value["evidence"][0]["revision_id"]
        with self.assertRaises(ValueError):
            reconcile({}, [value], evaluated_subjects=[uid(1)])


if __name__ == "__main__":
    unittest.main()
