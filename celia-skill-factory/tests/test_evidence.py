"""Evidence integrity and the release gate — the P2 tests.

The gate must refuse: missing tests, no human approval, any BLOCKED item,
unpinned evidence, and substituted evidence.
"""
import os
import shutil
import tempfile
import unittest

from reference import canonical
from reference.eventlog import EventLog
from reference.evidence import (
    EvidenceEntry,
    GateError,
    ReleaseRequest,
    assert_release_allowed,
    build_manifest,
    bundle_digest,
    check_release,
)


class EvidenceCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="celia-ev-")
        self.log = EventLog(os.path.join(self.dir, "registry.events.jsonl"))
        self.approval = self.log.append("APPROVED", {"by": "owner"})

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, name, content):
        path = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return name

    def _bundle(self, *files):
        return bundle_digest(build_manifest(self.dir, files))

    def _req(self, **kw):
        base = dict(
            approval_sha256=self.approval.event_sha256,
            evidence_digest="",
            approval_evidence_digest="",
            tests_passed=True,
            blocked_items=[],
            human_approver="owner",
        )
        base.update(kw)
        return ReleaseRequest(**base)


class TestManifest(EvidenceCase):
    def test_manifest_is_sorted(self):
        a = self._write("b.txt", "b")
        b = self._write("a.txt", "a")
        m = build_manifest(self.dir, [a, b])
        self.assertEqual([e.path for e in m], ["a.txt", "b.txt"])

    def test_digest_is_order_independent(self):
        a = self._write("x.txt", "x")
        b = self._write("y.txt", "y")
        self.assertEqual(self._bundle(a, b), self._bundle(b, a))

    def test_missing_file_rejected(self):
        with self.assertRaises(GateError) as cm:
            build_manifest(self.dir, ["nope.txt"])
        self.assertIn("EVIDENCE_MISSING", cm.exception.blockers)

    def test_path_escape_rejected(self):
        with self.assertRaises(GateError) as cm:
            build_manifest(self.dir, ["../../etc/passwd"])
        self.assertIn("PATH_ESCAPE", cm.exception.blockers)

    def test_duplicate_evidence_rejected(self):
        a = self._write("dup.txt", "d")
        with self.assertRaises(GateError) as cm:
            build_manifest(self.dir, [a, a])
        self.assertIn("DUPLICATE_EVIDENCE", cm.exception.blockers)

    def test_symlink_rejected(self):
        target = self._write("real.txt", "r")
        link = os.path.join(self.dir, "link.txt")
        os.symlink(os.path.join(self.dir, target), link)
        with self.assertRaises(GateError) as cm:
            build_manifest(self.dir, ["link.txt"])
        self.assertIn("SYMLINK_REJECTED", cm.exception.blockers)

    def test_single_byte_change_changes_bundle(self):
        a = self._write("f.txt", "original")
        before = self._bundle(a)
        self._write("f.txt", "originaL")
        self.assertNotEqual(before, self._bundle(a))


class TestReleaseGate(EvidenceCase):
    def test_open_gate(self):
        a = self._write("ev.txt", "evidence")
        d = self._bundle(a)
        req = self._req(evidence_digest=d, approval_evidence_digest=d)
        self.assertEqual(check_release(req, self.log), [])
        assert_release_allowed(req, self.log)   # must not raise

    def test_blocked_without_tests(self):
        a = self._write("ev.txt", "e"); d = self._bundle(a)
        req = self._req(evidence_digest=d, approval_evidence_digest=d, tests_passed=False)
        assert_release_allowed_raises = None
        with self.assertRaises(GateError) as cm:
            assert_release_allowed(req, self.log)
        self.assertIn("TESTS_PASSED", cm.exception.blockers)

    def test_blocked_without_human(self):
        a = self._write("ev.txt", "e"); d = self._bundle(a)
        req = self._req(evidence_digest=d, approval_evidence_digest=d, human_approver="  ")
        with self.assertRaises(GateError) as cm:
            assert_release_allowed(req, self.log)
        self.assertIn("HUMAN_APPROVED", cm.exception.blockers)

    def test_blocked_when_anything_is_blocked(self):
        a = self._write("ev.txt", "e"); d = self._bundle(a)
        req = self._req(evidence_digest=d, approval_evidence_digest=d, blocked_items=["12pro"])
        with self.assertRaises(GateError) as cm:
            assert_release_allowed(req, self.log)
        self.assertIn("NO_BLOCKED", cm.exception.blockers)

    def test_blocked_when_evidence_unpinned(self):
        req = self._req(evidence_digest="", approval_evidence_digest="")
        with self.assertRaises(GateError) as cm:
            assert_release_allowed(req, self.log)
        self.assertIn("EVIDENCE_PINNED", cm.exception.blockers)

    def test_blocked_when_evidence_substituted(self):
        a = self._write("ev.txt", "approved-version")
        approved = self._bundle(a)
        self._write("ev.txt", "swapped-version")     # substitute after approval
        current = self._bundle(a)
        req = self._req(evidence_digest=current, approval_evidence_digest=approved)
        with self.assertRaises(GateError) as cm:
            assert_release_allowed(req, self.log)
        self.assertIn("EVIDENCE_PINNED", cm.exception.blockers)

    def test_blocked_when_approval_not_in_log(self):
        a = self._write("ev.txt", "e"); d = self._bundle(a)
        req = self._req(evidence_digest=d, approval_evidence_digest=d,
                        approval_sha256=canonical.GENESIS)
        with self.assertRaises(GateError) as cm:
            assert_release_allowed(req, self.log)
        self.assertIn("APPROVAL_NOT_IN_LOG", cm.exception.blockers)

    def test_multiple_blockers_all_reported(self):
        req = self._req(tests_passed=False, human_approver="", blocked_items=["x"])
        blockers = check_release(req, self.log)
        for expected in ("TESTS_PASSED", "HUMAN_APPROVED", "NO_BLOCKED", "EVIDENCE_PINNED"):
            self.assertIn(expected, blockers)


if __name__ == "__main__":
    unittest.main(verbosity=2)
