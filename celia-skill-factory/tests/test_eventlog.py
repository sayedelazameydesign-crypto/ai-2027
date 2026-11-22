"""Event log integrity — the P1 tests.

Each test performs a REAL attack on a real file and asserts detection.
Detection is asserted by reason code, not by 'an error happened'.
"""
import json
import os
import shutil
import tempfile
import unittest

from reference import canonical
from reference.eventlog import Anchor, EventLog, LogIntegrityError, verify_all


class LogCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="celia-log-")
        self.path = os.path.join(self.dir, "registry.events.jsonl")
        self.anchor = Anchor(os.path.join(self.dir, "anchor.json"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _seed(self, n=5):
        log = EventLog(self.path)
        for i in range(n):
            log.append("RELEASE", {"release": f"r{i}"})
            self.anchor.write(log)
        return log

    def _read_lines(self):
        with open(self.path, encoding="utf-8") as fh:
            return fh.read().splitlines()

    def _write_lines(self, lines):
        with open(self.path, "w", encoding="utf-8", newline="") as fh:
            for ln in lines:
                fh.write(ln + "\n")

    # ---------- happy path ----------

    def test_append_and_verify(self):
        log = self._seed(5)
        self.assertEqual(log.sequence, 5)
        verify_all(log, self.anchor)

    def test_reopen_and_verify(self):
        self._seed(5)
        log = EventLog(self.path)
        verify_all(log, self.anchor)
        self.assertEqual(len(log.events()), 5)

    def test_genesis_prev_is_zeros(self):
        log = EventLog(self.path)
        ev = log.append("RELEASE", {"release": "r0"})
        self.assertEqual(ev.previous_event_sha256, canonical.GENESIS)

    # ---------- P1: chain attacks ----------

    def test_edit_payload_detected(self):
        self._seed(5)
        lines = self._read_lines()
        obj = json.loads(lines[2])
        obj["payload"]["release"] = "TAMPERED"
        lines[2] = canonical.dumps(obj)
        self._write_lines(lines)
        with self.assertRaises(LogIntegrityError) as cm:
            EventLog(self.path)
        self.assertEqual(cm.exception.reason, "HASH_MISMATCH")

    def test_reorder_detected(self):
        self._seed(5)
        lines = self._read_lines()
        lines[1], lines[3] = lines[3], lines[1]
        self._write_lines(lines)
        log = EventLog.__new__(EventLog)
        log.path = self.path
        log._events = []
        with self.assertRaises(LogIntegrityError) as cm:
            log._load()
        self.assertIn(cm.exception.reason, {"SEQUENCE_GAP", "CHAIN_BROKEN"})

    def test_swap_two_adjacent_detected(self):
        self._seed(5)
        lines = self._read_lines()
        lines[2], lines[3] = lines[3], lines[2]
        self._write_lines(lines)
        log = EventLog.__new__(EventLog)
        log.path, log._events = self.path, []
        with self.assertRaises(LogIntegrityError) as cm:
            log._load()
        self.assertIn(cm.exception.reason, {"SEQUENCE_GAP", "CHAIN_BROKEN"})

    def test_delete_middle_detected(self):
        self._seed(5)
        lines = self._read_lines()
        del lines[2]
        self._write_lines(lines)
        log = EventLog.__new__(EventLog)
        log.path, log._events = self.path, []
        with self.assertRaises(LogIntegrityError) as cm:
            log._load()
        self.assertEqual(cm.exception.reason, "SEQUENCE_GAP")

    def test_duplicate_line_detected(self):
        self._seed(5)
        lines = self._read_lines()
        lines.insert(2, lines[1])
        self._write_lines(lines)
        log = EventLog.__new__(EventLog)
        log.path, log._events = self.path, []
        with self.assertRaises(LogIntegrityError):
            log._load()

    # ---------- P1b: anchor attacks ----------

    def test_tail_truncation_detected_by_anchor(self):
        log = self._seed(5)
        lines = self._read_lines()
        self._write_lines(lines[:3])          # chain alone stays internally valid
        rebuilt = EventLog(self.path)
        rebuilt.verify_chain()                # passes: no gap, links intact
        with self.assertRaises(LogIntegrityError) as cm:
            rebuilt and self.anchor.verify_against(rebuilt)
        self.assertEqual(cm.exception.reason, "TRUNCATED")

    def test_rollback_to_older_state_detected(self):
        self._seed(5)
        backup = self._read_lines()
        log = EventLog(self.path)
        log.append("RELEASE", {"release": "r5"})
        self.anchor.write(log)
        self._write_lines(backup)             # restore an older snapshot
        rolled = EventLog(self.path)
        with self.assertRaises(LogIntegrityError) as cm:
            self.anchor.verify_against(rolled)
        self.assertEqual(cm.exception.reason, "TRUNCATED")

    def test_consistent_full_rewrite_detected_by_anchor(self):
        """The case the anchor exists for: an internally CONSISTENT rewrite.

        Every event is rebuilt from genesis with valid hashes and links, so
        chain verification passes completely. Only the independent anchor
        reveals that history changed.
        """
        self._seed(5)
        original_head = EventLog(self.path).head

        # rebuild the whole chain with different payloads, consistently
        prev = canonical.GENESIS
        lines = []
        for i in range(1, 6):
            body = {"sequence": i, "event_type": "RELEASE",
                    "payload": {"release": f"REWRITTEN{i}"},
                    "previous_event_sha256": prev}
            ev = {"sequence": i, "event_type": "RELEASE",
                  "payload": {"release": f"REWRITTEN{i}"},
                  "previous_event_sha256": prev,
                  "event_sha256": canonical.digest(body)}
            prev = ev["event_sha256"]
            lines.append(canonical.dumps(ev))
        self._write_lines(lines)

        rebuilt = EventLog(self.path)
        rebuilt.verify_chain()                     # layer 1 passes: consistent
        self.assertNotEqual(rebuilt.head, original_head)
        with self.assertRaises(LogIntegrityError) as cm:
            self.anchor.verify_against(rebuilt)    # layer 2 catches it
        self.assertEqual(cm.exception.reason, "ANCHOR_MISMATCH")

    def test_edit_breaks_chain_before_anchor(self):
        """A naive edit is caught by layer 1, not layer 2 — order matters."""
        self._seed(5)
        lines = self._read_lines()
        obj = json.loads(lines[1])
        obj["payload"]["release"] = "TAMPERED"
        lines[1] = canonical.dumps(obj)
        self._write_lines(lines)
        with self.assertRaises(LogIntegrityError) as cm:
            EventLog(self.path)
        self.assertEqual(cm.exception.reason, "HASH_MISMATCH")

    def test_no_anchor_means_chain_only(self):
        log = self._seed(3)
        anchor = Anchor(os.path.join(self.dir, "missing.json"))
        log.verify_chain()
        anchor.verify_against(log)            # no anchor: no claim, no crash

    # ---------- honest limit ----------

    def test_full_rewrite_with_anchor_is_undetected(self):
        """TRUSTED_ADMIN: rewriting log AND anchor is consistent.

        This test asserts the LIMIT, not a guarantee. It is here so the
        limitation cannot quietly disappear from the design.
        """
        log = self._seed(3)
        lines = self._read_lines()
        obj = json.loads(lines[0])
        obj["payload"]["release"] = "ADMIN_REWROTE"
        body = {"sequence": 1, "event_type": obj["event_type"],
                "payload": obj["payload"], "previous_event_sha256": canonical.GENESIS}
        obj["event_sha256"] = canonical.digest(body)
        with open(self.path, "w", encoding="utf-8", newline="") as fh:
            fh.write(canonical.dumps(obj) + "\n")
        rewritten = EventLog(self.path)
        self.anchor.write(rewritten)          # admin also rewrites the anchor
        verify_all(rewritten, self.anchor)    # passes — undetectable in-repo
        self.assertEqual(rewritten.events()[0].payload["release"], "ADMIN_REWROTE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
