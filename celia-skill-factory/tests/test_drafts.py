"""Draft lifecycle — the P1b tests.

Asserts the two things the review asked for:
  - a draft has its OWN log at skills/drafts/{draft_id}/events.jsonl
  - it is separate from the release log, and tampering is detected there too
"""
import json
import os
import shutil
import tempfile
import unittest

from reference import canonical
from reference.drafts import (
    DraftError,
    DraftLog,
    TRANSITIONS,
    draft_events_path,
    valid_draft_id,
)
from reference.eventlog import LogIntegrityError


class TestDraftValidation(unittest.TestCase):
    def test_valid_ids(self):
        for d in ("skill-001", "a", "celia.skill_factory_v1"):
            self.assertTrue(valid_draft_id(d), d)

    def test_invalid_ids(self):
        for d in ("", "-lead", "UPPER", "has space", "x" * 70, "a/b", "../etc"):
            self.assertFalse(valid_draft_id(d), d)


class TestDraftLifecycle(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="celia-drafts-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_path_matches_adr(self):
        p = draft_events_path(self.root, "skill-001")
        self.assertTrue(p.endswith(os.path.join("skills", "drafts", "skill-001", "events.jsonl")))

    def test_initial_state_is_draft(self):
        self.assertEqual(DraftLog(self.root, "skill-001").state(), "DRAFT")

    def test_happy_path_to_published(self):
        d = DraftLog(self.root, "skill-001")
        d.transition("SUBMITTED", "author")
        d.transition("IN_REVIEW", "system")
        d.transition("APPROVED", "reviewer")
        d.transition("PUBLISHED", "publisher")
        self.assertEqual(d.state(), "PUBLISHED")
        self.assertEqual(d.count(), 4)

    def test_every_transition_emits_an_event(self):
        d = DraftLog(self.root, "skill-002")
        before = d.count()
        d.transition("SUBMITTED", "author")
        self.assertEqual(d.count(), before + 1)
        self.assertEqual(d.events()[-1].event_type, "DRAFT_TRANSITION")

    def test_illegal_transition_rejected(self):
        d = DraftLog(self.root, "skill-003")
        with self.assertRaises(DraftError) as cm:
            d.transition("PUBLISHED", "author")   # DRAFT -> PUBLISHED is not allowed
        self.assertEqual(cm.exception.reason, "ILLEGAL_TRANSITION")

    def test_terminal_state_is_final(self):
        d = DraftLog(self.root, "skill-004")
        d.transition("ABANDONED", "author")
        with self.assertRaises(DraftError) as cm:
            d.transition("SUBMITTED", "author")
        self.assertEqual(cm.exception.reason, "ILLEGAL_TRANSITION")

    def test_missing_actor_rejected(self):
        d = DraftLog(self.root, "skill-005")
        with self.assertRaises(DraftError) as cm:
            d.transition("SUBMITTED", "")
        self.assertEqual(cm.exception.reason, "MISSING_ACTOR")

    def test_attach_artefact_records_digest(self):
        d = DraftLog(self.root, "skill-006")
        digest = canonical.digest({"tests": "passed"})
        ev = d.attach("EVIDENCE", digest, "author")
        self.assertEqual(ev.payload["sha256"], digest)

    def test_attach_rejects_bad_digest(self):
        d = DraftLog(self.root, "skill-007")
        with self.assertRaises(DraftError) as cm:
            d.attach("EVIDENCE", "not-a-digest", "author")
        self.assertEqual(cm.exception.reason, "BAD_DIGEST")

    def test_attach_rejected_after_close(self):
        d = DraftLog(self.root, "skill-008")
        d.transition("ABANDONED", "author")
        with self.assertRaises(DraftError) as cm:
            d.attach("EVIDENCE", canonical.digest({"x": 1}), "author")
        self.assertEqual(cm.exception.reason, "DRAFT_CLOSED")

    def test_state_survives_reload(self):
        d = DraftLog(self.root, "skill-009")
        d.transition("SUBMITTED", "author")
        d.transition("IN_REVIEW", "system")
        reopened = DraftLog(self.root, "skill-009")
        self.assertEqual(reopened.state(), "IN_REVIEW")
        self.assertEqual(reopened.count(), 2)

    def test_draft_logs_are_separate_files(self):
        a = DraftLog(self.root, "skill-a")
        b = DraftLog(self.root, "skill-b")
        a.transition("SUBMITTED", "author")
        self.assertEqual(a.count(), 1)
        self.assertEqual(b.count(), 0)
        self.assertNotEqual(a.log.path, b.log.path)

    def test_draft_log_never_touches_release_log(self):
        d = DraftLog(self.root, "skill-c")
        d.transition("SUBMITTED", "author")
        release = os.path.join(self.root, "registry.events.jsonl")
        self.assertFalse(os.path.exists(release))

    def test_tampered_draft_log_detected_on_open(self):
        d = DraftLog(self.root, "skill-d")
        d.transition("SUBMITTED", "author")
        path = draft_events_path(self.root, "skill-d")
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        obj = json.loads(lines[0])
        obj["payload"]["to_state"] = "APPROVED"
        lines[0] = canonical.dumps(obj)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(lines) + "\n")
        with self.assertRaises(LogIntegrityError) as cm:
            DraftLog(self.root, "skill-d")
        self.assertEqual(cm.exception.reason, "HASH_MISMATCH")

    def test_every_reachable_state_is_declared(self):
        for src, targets in TRANSITIONS.items():
            for t in targets:
                self.assertIn(t, TRANSITIONS, f"{src} -> {t} targets an undeclared state")


if __name__ == "__main__":
    unittest.main(verbosity=2)
