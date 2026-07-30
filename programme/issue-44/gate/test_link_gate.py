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
    """Historical quotations and inline code are preserved, never counted."""

    def test_blockquote_not_counted(self):
        active = {"a/b.md": {}}
        bodies = {"a/b.md": "> quoted [x](gone.md)\n"}
        self.assertEqual(audit(active, bodies), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
