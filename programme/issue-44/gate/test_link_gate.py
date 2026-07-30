#!/usr/bin/env python3
"""Regression tests for the existence-aware release gate.

Each test encodes a failure this programme actually hit. Pure functions only --
no network, no DocPlane.
"""
import os, sys, unittest

sys.path.insert(0, os.path.expanduser("~/i44"))
sys.path.insert(0, os.path.expanduser("~/docplane-dev-redirects"))
from link_baseline import audit, check, key  # noqa: E402


def corpus(pages):
    return {p: "" for p in pages}


class SyntacticallyResolvableButAbsent(unittest.TestCase):
    """resolve() returning a normalised path is NOT proof the target exists.
    This exact conflation reported CORPUS SCAN CLEAN over 23 regressions."""

    def test_absent_target_is_broken(self):
        active = {"a/b.md": {}}
        bodies = {"a/b.md": "see [x](../nowhere/gone.md)\n"}
        f = audit(active, bodies)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["resolved_target"], "nowhere/gone.md")

    def test_present_target_is_not_broken(self):
        active = {"a/b.md": {}, "a/c.md": {}}
        bodies = {"a/b.md": "see [x](c.md)\n"}
        self.assertEqual(audit(active, bodies), [])


class SourceRelativeDepthChange(unittest.TestCase):
    """A page moved deeper keeps its relative links; they now resolve elsewhere.
    Broke ../services/ccm-edge.md into control-plane/services/ccm-edge.md."""

    def test_depth_change_detected(self):
        active = {"control-plane/design/p.md": {}, "services/ccm-edge.md": {}}
        bodies = {"control-plane/design/p.md": "[e](../services/ccm-edge.md)\n"}
        f = audit(active, bodies)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["resolved_target"], "control-plane/services/ccm-edge.md")

    def test_root_absolute_is_immune(self):
        active = {"control-plane/design/p.md": {}, "services/ccm-edge.md": {}}
        bodies = {"control-plane/design/p.md": "[e](/services/ccm-edge/)\n"}
        self.assertEqual(audit(active, bodies), [])


class PartialSiblingDirectoryMove(unittest.TestCase):
    """Moving half a cluster breaks relative SIBLING links: i-geo-consumer-1.md
    resolved into invariants/ where the sibling had not moved yet."""

    def test_partial_move_breaks_sibling(self):
        active = {"cp/invariants/moved.md": {}, "cp/topology-invariants/sib.md": {}}
        bodies = {"cp/invariants/moved.md": "[s](sib.md)\n"}
        f = audit(active, bodies)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["resolved_target"], "cp/invariants/sib.md")

    def test_whole_cluster_moved_is_clean(self):
        active = {"cp/invariants/moved.md": {}, "cp/invariants/sib.md": {}}
        bodies = {"cp/invariants/moved.md": "[s](sib.md)\n"}
        self.assertEqual(audit(active, bodies), [])


class OwnedBaselineExceptions(unittest.TestCase):
    """Compatibility-route-backed evidence-surface references stay permitted,
    but only when explicitly owned."""

    def setUp(self):
        self.active = {"ops/incident.md": {}}
        self.bodies = {"ops/incident.md": "[q](../cp/doctrine.md)\n"}
        self.findings = audit(self.active, self.bodies)
        self.assertEqual(len(self.findings), 1)

    def test_owned_exception_passes(self):
        bl = {"exceptions": [{"key": key(self.findings[0]), "owner": "evidence-surface"}]}
        new, stale = check(self.findings, bl)
        self.assertEqual(new, [])
        self.assertEqual(stale, [])

    def test_unowned_break_blocks_release(self):
        new, _ = check(self.findings, {"exceptions": []})
        self.assertEqual(len(new), 1)

    def test_wrong_source_does_not_grant_exception(self):
        """An exception is keyed on source AND target; it must not blanket-permit."""
        bl = {"exceptions": [{"key": "other/page.md::cp/doctrine.md"}]}
        new, _ = check(self.findings, bl)
        self.assertEqual(len(new), 1)

    def test_repaired_exception_reported_as_stale(self):
        bl = {"exceptions": [{"key": "gone/page.md::gone/target.md"}]}
        new, stale = check(self.findings, bl)
        self.assertEqual(len(new), 1)
        self.assertEqual(stale, ["gone/page.md::gone/target.md"])


class ProtectedContexts(unittest.TestCase):
    """A link inside a blockquote is still a link a reader can follow, and it
    still 404s. Current-corpus validity therefore COUNTS it. The old gate
    skipped protected contexts by default, which is why live breaks survived
    the first repair -- Agent 45 found them afterwards."""

    def test_blockquote_counted_for_current_validity(self):
        active = {"a/b.md": {}}
        bodies = {"a/b.md": "> quoted [x](gone.md)\n"}
        f = audit(active, bodies, include_protected=True)
        self.assertEqual(len(f), 1)
        self.assertTrue(f[0]["protected"])

    def test_blockquote_skipped_only_when_explicitly_asked(self):
        active = {"a/b.md": {}}
        bodies = {"a/b.md": "> quoted [x](gone.md)\n"}
        self.assertEqual(audit(active, bodies, include_protected=False), [])


class MissedLinkFixtures(unittest.TestCase):
    """Fixtures built from the two links Agent 45 found live after repair 1,
    plus the third occurrence that audit missed alongside them."""

    def test_i_gen_no_vcs_1_reference_inside_inline_code_is_not_a_link(self):
        """The reported i-gen-no-vcs-1.md target sits INSIDE inline code, in a
        sentence that says the earlier link 'resolved to nothing'. It does not
        render as a hyperlink, so it cannot 404 for a reader and must not be
        counted. Counting it led me to retarget it, which made the sentence
        contradict itself -- a falsified historical quotation."""
        tick = chr(96)
        active = {"control-plane/invariants/i-gen-no-vcs-1.md": {},
                  "control-plane/invariants/index.md": {}}
        body = ("The earlier " + tick + "[I-3](invariants.md)" + tick +
                " link here resolved to nothing.\n")
        bodies = {"control-plane/invariants/i-gen-no-vcs-1.md": body}
        self.assertEqual(audit(active, bodies, include_protected=True), [])

    def test_plain_prose_version_of_the_same_target_is_counted(self):
        """The same raw target OUTSIDE code is a real link and must be caught."""
        active = {"control-plane/invariants/i-gen-no-vcs-1.md": {},
                  "control-plane/invariants/index.md": {}}
        bodies = {"control-plane/invariants/i-gen-no-vcs-1.md":
                  "see the [Invariants catalogue](invariants.md) for the set\n"}
        f = audit(active, bodies, include_protected=True)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["resolved_target"],
                         "control-plane/invariants/invariants.md")

    def test_code_sample_link_is_never_a_release_blocker(self):
        """docs-rename-design.md L34 documents what a cross-page ref looks like:
        `[foo](./old/path.md)`. It is a code sample, not a broken hyperlink, and
        must not block release nor be added to the owned baseline."""
        tick = chr(96)
        active = {"control-plane/design/docs-rename-design.md": {}}
        bodies = {"control-plane/design/docs-rename-design.md":
                  "- Affects cross-page refs (" + tick + "[foo](./old/path.md)" +
                  tick + ")\n"}
        f = audit(active, bodies, include_protected=True)
        self.assertEqual(f, [])
        new, _ = check(f, {"exceptions": []})
        self.assertEqual(new, [])

    def test_i_transport_hub_1_blockquote_navigational_link(self):
        """The occurrence structurally equivalent to one already repaired,
        surviving because it sits inside a blockquote."""
        active = {"control-plane/invariants/i-transport-hub-1.md": {},
                  "control-plane/design/transport-plane-authority.md": {}}
        bodies = {"control-plane/invariants/i-transport-hub-1.md":
                  "> see [Transport Plane Authority](../transport-plane-authority.md)\n"}
        f = audit(active, bodies, include_protected=True)
        self.assertEqual(len(f), 1)
        self.assertTrue(f[0]["protected"])
        self.assertEqual(f[0]["resolved_target"],
                         "control-plane/transport-plane-authority.md")

    def test_i_transport_hub_1_blockquote_sibling_link(self):
        """Third occurrence, unreported: a partial sibling migration inside a
        blockquote. Compatibility route 404, so strictly worse than the other."""
        active = {"control-plane/invariants/i-transport-hub-1.md": {},
                  "control-plane/topology-invariants/i-iface-naming-1.md": {}}
        bodies = {"control-plane/invariants/i-transport-hub-1.md":
                  "> per [I-IFACE-NAMING-1](i-iface-naming-1.md), not\n"}
        f = audit(active, bodies, include_protected=True)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["resolved_target"],
                         "control-plane/invariants/i-iface-naming-1.md")

    def test_all_three_block_release_when_unowned(self):
        active = {"p.md": {}, "real.md": {}}
        bodies = {"p.md": "> a [x](gone.md)\n\n[y](also-gone.md)\n"}
        f = audit(active, bodies, include_protected=True)
        new, _ = check(f, {"exceptions": []})
        self.assertEqual(len(new), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
