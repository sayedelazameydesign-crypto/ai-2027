#!/usr/bin/env python3
"""Phase 2B tests: the alert must describe the log, not a plausible story.

Nine tests. Every test that writes an alert/recovery event writes it to a REAL
hash-chained JSONL file and then asks ``verify()`` what it thinks — an
in-memory mock cannot prove that ``prev`` still links to the previous record.

Run: python3 monitoring/test_phase2b.py -v
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import local_watchdog as wd  # noqa: E402
import phase2b as p2b  # noqa: E402


def make_records(kinds, start=1000.0):
    """A flat, seq/ts-complete timeline for pure-function tests."""
    out = []
    ts = start
    for seq, item in enumerate(kinds):
        kind, data = item if isinstance(item, tuple) else (item, {})
        out.append({"seq": seq, "ts": ts, "ts_iso": "t", "kind": kind,
                    "data": data, "prev": "genesis", "hash": "sha256:x"})
        ts += 1.0
    return out


def failed(message="unknown error: flux capacitor"):
    return ("poll_failed", {"error_class": "HTTPError", "message": message})


class LogTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="p2b-test-"))
        self.log_path = self.tmp / "watch.jsonl"
        self.log = wd.EventLog(self.log_path, fsync=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def records(self):
        return self.log.read_all()[0]


class DetectionTests(unittest.TestCase):
    def test_01_three_scattered_failures_are_not_consecutive(self):
        recs = make_records(["start", failed(), "poll", failed(), "poll",
                             failed(), "poll", "stop"])
        gap = p2b.analyze_gaps(recs)
        self.assertEqual(gap["poll_failures"], 3)          # total in the log
        self.assertEqual(gap["consecutive_failures"], 1)   # adjacent run
        self.assertEqual(len(gap["consecutive_failure_runs"]), 3)
        self.assertEqual(gap["trailing_consecutive_failures"], 0)

    def test_02_defaults_are_three_consecutive_and_300_seconds(self):
        self.assertEqual(p2b.DEFAULT_FAILURE_THRESHOLD, 3)
        self.assertEqual(p2b.DEFAULT_SILENCE_THRESHOLD, 300.0)
        rules = {r.name: r for r in p2b.default_rules()}
        self.assertEqual(rules["repeated_poll_failures"].metric,
                         "consecutive_failures")
        self.assertEqual(rules["repeated_poll_failures"].threshold, 3)
        self.assertEqual(rules["large_gap"].threshold, 300.0)
        self.assertEqual(rules["large_gap"].metric, "max_gap_seconds")

    def test_03_alert_needs_a_real_run_not_a_running_total(self):
        scattered = make_records([failed(), "poll", failed(), "poll",
                                  failed(), "poll"])
        run = make_records([failed(), failed(), failed(), "poll"])
        names = lambda rs: {a["rule"] for a in p2b.evaluate(records=rs)}
        self.assertNotIn("repeated_poll_failures", names(scattered))
        self.assertIn("repeated_poll_failures", names(run))

    def test_04_silence_threshold_is_300_not_60(self):
        below = make_records([("silence", {"gap_seconds": 299.9}), "poll"])
        at = make_records([("silence", {"gap_seconds": 300.0}), "poll"])
        names = lambda rs: {a["rule"] for a in p2b.evaluate(records=rs)}
        self.assertNotIn("large_gap", names(below))
        self.assertIn("large_gap", names(at))

    def test_05_recovery_exists_only_after_a_successful_poll(self):
        outage = make_records([failed(), failed(), failed()])
        restored = make_records([failed(), failed(), failed(), "poll"])
        self.assertEqual([e["kind"] for e in p2b.derive_events(outage)],
                         ["alert"])
        events = p2b.derive_events(restored)
        self.assertEqual([e["kind"] for e in events], ["alert", "recovery"])
        self.assertEqual(events[0]["data"]["reason"], "consecutive_failures")
        self.assertEqual(events[1]["data"]["reason"], "consecutive_failures")
        # Keys are derived from the log, so re-analysis cannot invent new ones.
        self.assertEqual([e["data"]["key"] for e in events],
                         [e["data"]["key"] for e in p2b.derive_events(restored)])


class ChainTests(LogTestCase):
    def test_06_append_event_preserves_the_hash_chain(self):
        self.log.append("start", {}, ts=1000.0)
        for i in range(3):
            self.log.append("poll_failed", {"message": "unknown error"},
                            ts=1001.0 + i)
        head_before = self.log.head()
        recs = self.records()
        events = p2b.derive_events(recs)
        appended = [p2b.append_event(self.log, e["kind"], e["data"],
                                     records=recs) for e in events]
        self.assertEqual(appended[0]["prev"], head_before["hash"])
        all_recs = self.records()
        self.assertEqual([r["seq"] for r in all_recs],
                         list(range(len(all_recs))))
        for prev, cur in zip(all_recs, all_recs[1:]):
            self.assertEqual(cur["prev"], prev["hash"])
        v = self.log.verify()
        self.assertTrue(v["intact"], v)
        self.assertTrue(v["clean"], v)
        self.assertEqual([r["kind"] for r in appended], ["alert"])

    def test_07_record_alert_events_is_idempotent(self):
        self.log.append("start", {}, ts=1000.0)
        for i in range(3):
            self.log.append("poll_failed", {"message": "unknown error"},
                            ts=1001.0 + i)
        self.log.append("poll", {"snapshot": {"status": "live"}}, ts=1010.0)
        first = p2b.record_alert_events(log=self.log, records=self.records())
        self.assertEqual(first["appended"], 2)  # one alert + one recovery
        self.assertEqual(first["duplicates"], 0)
        second = p2b.record_alert_events(log=self.log)
        self.assertEqual(second["appended"], 0)
        self.assertEqual(second["duplicates"], 2)
        kinds = wd.report(self.records())["kinds"]
        self.assertEqual(kinds.get("alert"), 1)
        self.assertEqual(kinds.get("recovery"), 1)
        self.assertTrue(self.log.verify()["clean"])

    def test_08_derived_events_do_not_reclassify_themselves(self):
        self.log.append("start", {}, ts=1000.0)
        self.log.append("poll_failed",
                        {"message": "unknown error: flux capacitor"},
                        ts=1001.0)
        original = self.records()
        before = p2b.analyze_errors(records=original)
        # "unclassified" is published instead of a guessed cause.
        self.assertEqual(before["unclassified"], 1)
        self.assertEqual(before["diagnosis"]["causes"], [])
        p2b.record_alert_events(log=self.log, records=original)
        after = p2b.analyze_errors(records=self.records())
        self.assertEqual(after["error_lines"], before["error_lines"])
        self.assertEqual(after["unclassified"], before["unclassified"])

    def test_09_derived_events_do_not_stretch_the_report_span(self):
        self.log.append("start", {}, ts=1000.0)
        for i in range(3):
            self.log.append("poll_failed", {"message": "unknown error"},
                            ts=1001.0 + i)
        self.log.append("poll", {"snapshot": {"status": "live"}}, ts=1010.0)
        base = wd.report(self.records())
        p2b.record_alert_events(log=self.log, records=self.records())
        after = wd.report(self.records())
        self.assertEqual(after["span_seconds"], base["span_seconds"])
        self.assertEqual(after["observed_fraction"], base["observed_fraction"])
        self.assertEqual(after["silence_seconds_total"],
                         base["silence_seconds_total"])
        # ts is pinned to the last observed record; the write time is kept
        # separately instead of being disguised as log time.
        for r in self.records():
            if r["kind"] in p2b.ALERT_KINDS:
                self.assertEqual(r["ts"], 1010.0)
                self.assertIn("detected_at_iso", r["data"])
        self.assertTrue(self.log.verify()["clean"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
