#!/usr/bin/env python3
"""Phase 2A tests: the log must break loudly when it is broken.

Every attack test MODIFIES A REAL FILE ON DISK and then asks the verifier what it
thinks. A test that only exercises in-memory objects cannot tell you whether the
thing on disk is trustworthy, which is the entire point.

Run: python3 monitoring/test_watchdog.py -v
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import local_watchdog as wd  # noqa: E402


class LogTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wd-test-"))
        self.log_path = self.tmp / "watch.jsonl"
        self.anchor = self.tmp / "watch.anchor"
        self.log = wd.EventLog(self.log_path, anchor_path=self.anchor, fsync=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fill(self, n=5):
        for i in range(n):
            self.log.append("poll", {"snapshot": {"status": "live", "i": i}})
        return self.log.read_all()[0]

    def raw_lines(self):
        return self.log_path.read_bytes().split(b"\n")


# --------------------------------------------------------------------------- #
# 01-09  chain integrity
# --------------------------------------------------------------------------- #
class ChainTests(LogTestCase):
    def test_01_a_fresh_log_verifies(self):
        self.fill(3)
        result = self.log.verify()
        self.assertTrue(result["intact"], result)
        self.assertTrue(result["clean"], result)
        self.assertEqual(result["records"], 3)

    def test_02_genesis_has_no_prev_hash(self):
        rec = self.log.append("start", {})
        self.assertEqual(rec["prev"], wd.GENESIS)
        self.assertEqual(rec["seq"], 0)

    def test_03_each_record_links_to_the_one_before(self):
        recs = self.fill(4)
        for i in range(1, len(recs)):
            self.assertEqual(recs[i]["prev"], recs[i - 1]["hash"])
            self.assertNotEqual(recs[i]["hash"], recs[i - 1]["hash"])

    def test_04_hash_changes_when_any_field_changes(self):
        rec = self.log.append("poll", {"snapshot": {"status": "live"}})
        base = rec["hash"]
        for field, value in (("kind", "stop"), ("ts", rec["ts"] + 1),
                             ("seq", rec["seq"] + 5)):
            mutated = dict(rec, **{field: value})
            mutated.pop("hash", None)
            self.assertNotEqual(wd.record_hash(mutated), base,
                                f"changing {field} must change the hash")

    def test_05_hash_is_stable_across_dict_order(self):
        a = {"b": 1, "a": [1, 2], "c": {"z": 1, "y": 2}}
        b = {"a": [1, 2], "c": {"y": 2, "z": 1}, "b": 1}
        self.assertEqual(wd.digest(a), wd.digest(b),
                         "key order must not change the hash")

    def test_06_unicode_is_hashed_not_mangled(self):
        rec = self.log.append("poll", {"snapshot": {"note": "خدمة متوقفة ✅"}})
        recs = self.log.read_all()[0]
        self.assertEqual(recs[0]["data"]["snapshot"]["note"], "خدمة متوقفة ✅")
        self.assertEqual(self.log.verify()["records"], 1)
        self.assertIn("خدمة متوقفة".encode("utf-8"), self.log_path.read_bytes())


# --------------------------------------------------------------------------- #
# 10-19  attacks on a real file
# --------------------------------------------------------------------------- #
class TamperTests(LogTestCase):
    def _rewrite(self, mutate_lines):
        lines = self.raw_lines()
        out = mutate_lines(lines)
        self.log_path.write_bytes(b"\n".join(out))

    def test_10_editing_a_value_is_detected(self):
        self.fill(5)
        self._rewrite(lambda ls: [l.replace(b'"i":2', b'"i":99') if b'"i":2' in l else l
                                  for l in ls])
        result = self.log.verify()
        self.assertFalse(result["intact"])
        self.assertTrue(any(p["type"] == "hash_mismatch"
                            for p in result["tamper_problems"]),
                        result["tamper_problems"])

    def test_11_deleting_a_middle_record_is_detected(self):
        self.fill(5)
        self._rewrite(lambda ls: ls[:2] + ls[3:])
        result = self.log.verify()
        self.assertFalse(result["intact"], "a deleted record must not verify")
        types = {p["type"] for p in result["tamper_problems"]}
        self.assertTrue({"seq_break", "prev_mismatch"} & types, types)

    def test_12_reordering_records_is_detected(self):
        self.fill(5)
        self._rewrite(lambda ls: ls[:1] + [ls[2], ls[1]] + ls[3:])
        result = self.log.verify()
        self.assertFalse(result["intact"])
        self.assertIn("prev_mismatch",
                      {p["type"] for p in result["tamper_problems"]})

    def test_13_appending_a_forged_record_is_detected(self):
        self.fill(3)
        recs = self.log.read_all()[0]
        forged = {
            "v": 1, "seq": 3, "ts": time.time(), "ts_iso": "2026-01-01T00:00:00Z",
            "kind": "poll", "prev": recs[-1]["hash"],
            "data": {"snapshot": {"status": "live", "forged": True}},
        }
        forged["hash"] = wd.record_hash(forged)  # attacker computes a VALID hash
        with open(self.log_path, "ab") as fh:
            fh.write(wd.canonical(forged) + b"\n")
        result = self.log.verify()
        # A hash chain alone CANNOT catch this: the attacker kept it consistent.
        # That is exactly why the anchor exists — and it is tested at test_30.
        self.assertTrue(result["intact"], "without an anchor, a consistent forgery "
                                          "verifies; the anchor is what catches it")

    def test_14_truncating_the_file_is_detected_with_an_anchor(self):
        self.fill(5)
        self.log.write_anchor()
        lines = self.raw_lines()
        self.log_path.write_bytes(b"\n".join(lines[:3]) + b"\n")
        result = self.log.verify()
        self.assertFalse(result["intact"])
        self.assertIn("anchor_ahead",
                      {p["type"] for p in result["anchor_problems"]})

    def test_15_duplicating_a_line_is_detected(self):
        self.fill(4)
        self._rewrite(lambda ls: ls[:2] + [ls[1]] + ls[2:])
        result = self.log.verify()
        self.assertFalse(result["intact"])

    def test_16_an_empty_log_verifies_as_empty(self):
        result = self.log.verify()
        self.assertTrue(result["intact"])
        self.assertEqual(result["records"], 0)
        self.assertEqual(result["head"], wd.GENESIS)


class BoundaryTests(LogTestCase):
    """The exact edge of the guarantee, so nobody assumes more than is true."""

    def _recompute_consistently(self, records, keep):
        prev = wd.GENESIS
        lines = []
        for i, rec in enumerate(records):
            if i not in keep:
                continue
            new = dict(rec, seq=len(lines), prev=prev)
            new.pop("hash", None)
            new["hash"] = wd.record_hash(new)
            prev = new["hash"]
            lines.append(wd.canonical(new))
        self.log_path.write_bytes(b"\n".join(lines) + b"\n")

    def test_17_a_recomputed_rewrite_defeats_the_chain_and_the_anchor_catches_it(self):
        self.fill(5)
        self.log.write_anchor()
        recs = self.log.read_all()[0]
        self._recompute_consistently(recs, keep={0, 1, 2, 3})

        chain_only = wd.EventLog(self.log_path).verify()
        self.assertTrue(chain_only["intact"],
                        "no local hash chain can beat a consistent rewrite — "
                        "if this ever fails, the threat model changed")

        anchored = self.log.verify()
        self.assertFalse(anchored["intact"], "the anchor is the real defence")
        # Either outcome is a detection: the anchored record is gone
        # (anchor_ahead) or it is still there with a different hash
        # (anchor_mismatch). Which one depends on how much the attacker deleted.
        types = {p["type"] for p in anchored["anchor_problems"]}
        self.assertTrue({"anchor_ahead", "anchor_mismatch"} & types, types)

    def test_18_a_sloppy_forgery_that_forgets_prev_is_caught(self):
        self.fill(5)
        recs = self.log.read_all()[0]
        prev = wd.GENESIS
        lines = []
        for i, rec in enumerate(recs):
            new = dict(rec, data={"snapshot": {"status": "live", "i": 50 + i}})
            new.pop("hash", None)
            new["hash"] = wd.record_hash(new)   # hash fixed, prev left stale
            lines.append(wd.canonical(new))
            prev = new["hash"]
        self.log_path.write_bytes(b"\n".join(lines) + b"\n")
        result = self.log.verify()
        self.assertFalse(result["intact"])
        self.assertIn("prev_mismatch",
                      {p["type"] for p in result["tamper_problems"]})


# --------------------------------------------------------------------------- #
# 20-29  crash safety: a power cut is not an attacker
# --------------------------------------------------------------------------- #
class CrashTests(LogTestCase):
    def test_20_a_torn_final_line_is_reported_as_a_crash_not_forgery(self):
        self.fill(3)
        with open(self.log_path, "ab") as fh:
            fh.write(b'{"v":1,"seq":3,"ts":1.0,"kind":"pol')  # no newline
        result = self.log.verify()
        self.assertTrue(result["truncated_tail"], result)
        self.assertTrue(result["intact"],
                        "a crash must not be reported as tampering")
        self.assertFalse(result["clean"], "but it must not be reported as clean either")

    def test_21_appending_after_a_crash_repairs_and_records_the_loss(self):
        self.fill(3)
        with open(self.log_path, "ab") as fh:
            fh.write(b'{"v":1,"seq":3,"ts":1.0,"kind":"pol')
        rec = self.log.append("poll", {"snapshot": {"status": "live"}})
        self.assertEqual(rec["kind"], "tail_repaired")
        self.assertGreater(rec["data"]["dropped_bytes"], 0)
        self.assertEqual(rec["data"]["before_seq"], 3)
        result = self.log.verify()
        self.assertTrue(result["clean"], result)
        self.assertTrue(result["intact"])

    def test_22_the_chain_survives_the_repair(self):
        self.fill(3)
        with open(self.log_path, "ab") as fh:
            fh.write(b"garbage!")
        self.log.append("poll", {"snapshot": {"status": "live"}})
        self.log.append("poll", {"snapshot": {"status": "live"}})
        result = self.log.verify()
        self.assertTrue(result["intact"], result["tamper_problems"])
        # 3 polls + (poll + tail_repaired) + 1 poll = 6 records
        self.assertEqual(result["records"], 6)

    def test_23_repair_does_not_touch_a_healthy_file(self):
        self.fill(3)
        size_before = self.log_path.stat().st_size
        self.log.append("poll", {"snapshot": {"status": "live"}})
        recs = self.log.read_all()[0]
        self.assertEqual([r["kind"] for r in recs],
                         ["poll", "poll", "poll", "poll"])
        self.assertGreater(self.log_path.stat().st_size, size_before)


# --------------------------------------------------------------------------- #
# 30-39  the anchor is the real defence
# --------------------------------------------------------------------------- #
class AnchorTests(LogTestCase):
    def test_30_a_full_rewrite_is_caught_only_by_the_anchor(self):
        self.fill(4)
        self.log.write_anchor()
        # Attacker rewrites the ENTIRE chain consistently, then recomputes hashes.
        recs = self.log.read_all()[0]
        prev = wd.GENESIS
        lines = []
        for i, rec in enumerate(recs):
            new = dict(rec, data={"snapshot": {"status": "live", "i": 100 + i}})
            new["prev"] = prev
            new.pop("hash", None)
            new["hash"] = wd.record_hash(new)
            prev = new["hash"]
            lines.append(wd.canonical(new))
        self.log_path.write_bytes(b"\n".join(lines) + b"\n")

        no_anchor = wd.EventLog(self.log_path).verify()
        self.assertTrue(no_anchor["intact"],
                        "the chain is internally consistent — expected")
        with_anchor = self.log.verify()
        self.assertFalse(with_anchor["intact"], "the anchor must catch the rewrite")
        self.assertIn("anchor_mismatch",
                      {p["type"] for p in with_anchor["anchor_problems"]})

    def test_31_anchor_is_append_only(self):
        self.fill(2)
        self.log.write_anchor()
        self.log.append("poll", {"snapshot": {"status": "live"}})
        self.log.write_anchor()
        lines = self.anchor.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].split()[2], wd.EventLog(self.log_path).read_all()[0][1]["hash"])

    def test_32_verify_without_an_anchor_still_works(self):
        self.fill(3)
        result = wd.EventLog(self.log_path).verify()
        self.assertTrue(result["intact"])
        self.assertEqual(result["anchor_problems"], [])


# --------------------------------------------------------------------------- #
# 40-49  silence and failure are different facts
# --------------------------------------------------------------------------- #
class FakeClock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class SilenceTests(LogTestCase):
    def test_40_a_gap_before_start_is_recorded_as_silence(self):
        self.log.append("stop", {"polls": 1}, ts=1000.0)
        clock = FakeClock(1000.0 + 300.0)  # 300s later, 60s interval
        wd.run(interval=60, log=self.log, max_polls=1, clock=clock,
               sleep=clock.sleep)
        recs = self.log.read_all()[0]
        silence = [r for r in recs if r["kind"] == "silence"]
        self.assertEqual(len(silence), 1)
        self.assertEqual(silence[0]["data"]["gap_seconds"], 300.0)
        self.assertEqual(silence[0]["data"]["missed_intervals"], 4)
        self.assertEqual(silence[0]["data"]["after_seq"], 0)

    def test_41_a_source_failure_is_poll_failed_not_silence(self):
        def dead():
            raise ConnectionResetError("TLS reset by peer")
        clock = FakeClock()
        wd.run(interval=60, log=self.log, source=dead, max_polls=2,
               clock=clock, sleep=clock.sleep)
        recs = self.log.read_all()[0]
        kinds = [r["kind"] for r in recs]
        self.assertEqual(kinds.count("poll_failed"), 2)
        self.assertNotIn("silence", kinds,
                         "the loop was alive; calling that silence would hide the "
                         "difference between a dead host and a dead network")
        failed = [r for r in recs if r["kind"] == "poll_failed"][0]
        self.assertEqual(failed["data"]["error_class"], "ConnectionResetError")
        self.assertTrue(failed["data"]["was_alive"])

    def test_42_the_loop_survives_a_dead_source(self):
        state = {"n": 0}

        def flaky():
            state["n"] += 1
            if state["n"] % 2:
                raise TimeoutError("api.render.com did not answer")
            return {"status": "live", "n": state["n"]}
        clock = FakeClock()
        result = wd.run(interval=10, log=self.log, source=flaky, max_polls=4,
                        clock=clock, sleep=clock.sleep)
        self.assertEqual(result["polls"], 4)
        self.assertEqual(result["failures"], 2)
        self.assertTrue(self.log.verify()["intact"])

    def test_43_a_long_poll_is_recorded_as_silence_not_hidden(self):
        clock = FakeClock()

        def slow():
            clock.t += 180  # one poll takes three intervals
            return {"status": "live"}
        wd.run(interval=60, log=self.log, source=slow, max_polls=3,
               clock=clock, sleep=clock.sleep)
        silences = [r for r in self.log.read_all()[0] if r["kind"] == "silence"]
        self.assertGreaterEqual(len(silences), 1)
        self.assertIn("overran", silences[0]["data"]["reason"])

    def test_44_a_clean_short_run_records_no_silence(self):
        clock = FakeClock()
        wd.run(interval=10, log=self.log, source=lambda: {"status": "live"},
               max_polls=5, clock=clock, sleep=clock.sleep)
        recs = self.log.read_all()[0]
        self.assertEqual([r["kind"] for r in recs],
                         ["start"] + ["poll"] * 5 + ["stop"])

    def test_45_report_never_claims_full_coverage_it_lacks(self):
        # 10 polls at 10s, but a 90s hole was cut out of the middle.
        clock = FakeClock()
        state = {"n": 0}

        def source():
            state["n"] += 1
            if state["n"] == 5:
                clock.t += 90
            return {"status": "live"}
        wd.run(interval=10, log=self.log, source=source, max_polls=10,
               clock=clock, sleep=clock.sleep)
        recs = self.log.read_all()[0]
        rep = wd.report(recs)
        self.assertEqual(rep["silence_events"], 1)
        self.assertLess(rep["observed_fraction"], 1.0,
                        "the report must not round its own hole away")
        self.assertEqual(rep["silence_seconds_largest"], 70.0,
                         "90s was cut out, but 20s of it was the interval "
                         "already owed between two polls")


class ConcurrencyTests(LogTestCase):
    """Several watchdogs, one log. The lock must serialise them, and noise must
    not be recorded as an incident."""

    def test_46_four_writers_share_one_log_without_corruption(self):
        import subprocess
        fixture = self.tmp / "snap.json"
        fixture.write_text(json.dumps({"status": "live"}))
        procs, polls = 4, 30
        cmd = [sys.executable, str(HERE / "local_watchdog.py"), "run",
               "--log", str(self.log_path), "--interval", "0.002",
               "--polls", str(polls), "--source", str(fixture)]
        procs_run = [subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.PIPE, text=True)
                     for _ in range(procs)]
        errs = [p.communicate(timeout=180)[1] for p in procs_run]
        self.assertEqual([p.returncode for p in procs_run], [0] * procs,
                         [e for e in errs if e.strip()][:2])

        records, problems = self.log.read_all()
        self.assertEqual(problems, [])
        self.assertEqual([r["seq"] for r in records],
                         list(range(len(records))), "seq must stay contiguous")
        self.assertEqual(len({r["hash"] for r in records}), len(records))
        self.assertTrue(self.log.verify()["clean"])
        self.assertEqual(sum(1 for r in records if r["kind"] == "poll"),
                         procs * polls)

    def test_47_scheduling_noise_is_not_recorded_as_an_outage(self):
        # Regression: a 4-writer run produced 92 "silence" records whose
        # gap_seconds was 0.002. Sub-millisecond jitter is not an outage, and a
        # log full of fake incidents teaches the reader to ignore real ones.
        import subprocess
        fixture = self.tmp / "snap.json"
        fixture.write_text(json.dumps({"status": "live"}))
        cmd = [sys.executable, str(HERE / "local_watchdog.py"), "run",
               "--log", str(self.log_path), "--interval", "0.002",
               "--polls", "30", "--source", str(fixture)]
        procs = [subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL) for _ in range(3)]
        [p.wait(timeout=180) for p in procs]
        records, _ = self.log.read_all()
        silences = [r for r in records if r["kind"] == "silence"]
        tiny = [s for s in silences if s["data"]["gap_seconds"] < 0.02]
        self.assertEqual(tiny, [],
                         f"{len(tiny)} bogus silence records under 20ms")

    def test_49_suppressed_jitter_is_counted_not_hidden(self):
        # The floor must be auditable: a reader has to be able to see that 99
        # lateness events were suppressed and how large the biggest one was,
        # otherwise the threshold is a silent edit to the data.
        clock = FakeClock()

        def slow():
            clock.t += 0.002          # 2ms late: below the 1s floor
            return {"status": "live"}
        res = wd.run(interval=10, log=self.log, source=slow, max_polls=3,
                     clock=clock, sleep=clock.sleep, min_gap=1.0)
        stop = res["stop"]
        self.assertEqual(stop["data"]["jitter_suppressed"], 0,
                         "2ms of lateness is owed, not a gap")
        self.assertEqual(stop["data"]["min_gap"], 1.0)
        kinds = [r["kind"] for r in self.log.read_all()[0]]
        self.assertNotIn("silence", kinds)

        # Now force lateness that is real but below the floor.
        clock2 = FakeClock()

        def late():
            clock2.t += 0.4
            return {"status": "live"}
        # interval*tolerance = 0.05s, so 0.4s of lateness IS drift — but it is
        # under the 5s floor, which is the branch under test.
        res2 = wd.run(interval=0.1, log=self.log, source=late, max_polls=3,
                      clock=clock2, sleep=clock2.sleep, min_gap=5.0)
        self.assertGreater(res2["stop"]["data"]["jitter_suppressed"], 0,
                           "suppressed lateness must still be counted")
        self.assertGreater(res2["stop"]["data"]["jitter_largest_seconds"], 0)
        self.assertNotIn("silence",
                         [r["kind"] for r in self.log.read_all()[0]])

    def test_48_a_future_timestamp_is_not_a_gap(self):
        # Another writer appended after we read the head: a negative gap.
        self.log.append("poll", {"snapshot": {"status": "live"}}, ts=5000.0)
        clock = FakeClock(1000.0)  # our clock is "behind" that record
        wd.run(interval=60, log=self.log, max_polls=1, clock=clock, sleep=clock.sleep)
        kinds = [r["kind"] for r in self.log.read_all()[0]]
        self.assertNotIn("silence", kinds)


# --------------------------------------------------------------------------- #
# 50-59  transitions
# --------------------------------------------------------------------------- #
class TransitionTests(LogTestCase):
    def test_50_a_state_change_is_recorded_once(self):
        seq = iter([{"status": "live", "deploy": "a"},
                    {"status": "live", "deploy": "a"},
                    {"status": "live", "deploy": "b"}])
        clock = FakeClock()
        wd.run(interval=10, log=self.log, source=lambda: next(seq),
               max_polls=3, clock=clock, sleep=clock.sleep)
        recs = self.log.read_all()[0]
        all_t = [t for r in recs if r["kind"] == "poll"
                 for t in r["data"]["transitions"]]
        # The very first poll has no prior snapshot, so it reports the initial
        # state (asserted separately in test_51). Count real CHANGES here.
        changes = [t for t in all_t if t["before"] is not None]
        self.assertEqual(len(changes), 1, all_t)
        self.assertEqual(changes[0]["key"], "deploy")
        self.assertEqual(changes[0]["before"], "a")
        self.assertEqual(changes[0]["after"], "b")
        poll2 = [r for r in recs if r["kind"] == "poll"][1]
        self.assertEqual(poll2["data"]["transitions"], [],
                         "an unchanged poll must record no transition")

    def test_51_the_first_poll_reports_the_initial_state_as_a_transition(self):
        clock = FakeClock()
        wd.run(interval=10, log=self.log, source=lambda: {"status": "live"},
               max_polls=1, clock=clock, sleep=clock.sleep)
        recs = self.log.read_all()[0]
        t = recs[1]["data"]["transitions"]
        self.assertEqual(t, [{"key": "status", "before": None, "after": "live"}])

    def test_52_a_snapshot_survives_a_restart_of_the_loop(self):
        clock = FakeClock()
        wd.run(interval=10, log=self.log, source=lambda: {"status": "live"},
               max_polls=1, clock=clock, sleep=clock.sleep)
        wd.run(interval=10, log=self.log, source=lambda: {"status": "live"},
               max_polls=1, clock=clock, sleep=clock.sleep)
        recs = self.log.read_all()[0]
        second_poll = [r for r in recs if r["kind"] == "poll"][1]
        self.assertEqual(second_poll["data"]["transitions"], [],
                         "a restart must not re-report an unchanged state as a change")

    def test_53_diff_is_flat_and_readable(self):
        d = wd.diff_snapshots({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4})
        self.assertEqual(d, [{"key": "b", "before": 2, "after": 3},
                             {"key": "c", "before": None, "after": 4}])


# --------------------------------------------------------------------------- #
# 60-69  CLI refuses to lie
# --------------------------------------------------------------------------- #
class CliTests(LogTestCase):
    def _run(self, *args):
        import subprocess
        return subprocess.run([sys.executable, str(HERE / "local_watchdog.py"), *args],
                              capture_output=True, text=True, timeout=120)

    def test_60_verify_exits_0_on_a_clean_log(self):
        self.fill(3)
        r = self._run("verify", "--log", str(self.log_path))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_61_verify_exits_1_on_tampering(self):
        self.fill(3)
        data = self.log_path.read_bytes().replace(b'"i":1', b'"i":7')
        self.log_path.write_bytes(data)
        r = self._run("verify", "--log", str(self.log_path))
        self.assertEqual(r.returncode, 1, "tampering must be a non-zero exit")
        self.assertIn("TAMPER", r.stderr)

    def test_62_verify_exits_3_on_a_crash(self):
        self.fill(3)
        with open(self.log_path, "ab") as fh:
            fh.write(b'{"v":1,"se')
        r = self._run("verify", "--log", str(self.log_path))
        self.assertEqual(r.returncode, 3,
                         "a crash must be distinct from both clean and forged")

    def test_63_run_from_a_json_fixture_writes_a_verifiable_log(self):
        fixture = self.tmp / "snap.json"
        fixture.write_text(json.dumps({"status": "live", "service": "pro-print"}))
        r = self._run("run", "--log", str(self.log_path), "--polls", "2",
                      "--interval", "0.01", "--source", str(fixture))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.log.verify()["intact"])

    def test_64_a_bad_source_spec_is_rejected_loudly(self):
        r = self._run("run", "--log", str(self.log_path), "--polls", "1",
                      "--source", "nonsense")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("module:function", r.stderr)


# --------------------------------------------------------------------------- #
# 70-79  the module's own constraints
# --------------------------------------------------------------------------- #
class ConstraintTests(unittest.TestCase):
    def test_70_watchdog_is_stdlib_only(self):
        import ast
        stdlib = set(sys.stdlib_module_names)
        tree = ast.parse((HERE / "local_watchdog.py").read_text(encoding="utf-8"))
        bad = set()
        for n in ast.walk(tree):
            names = []
            if isinstance(n, ast.Import):
                names = [a.name.split(".")[0] for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                names = [n.module.split(".")[0]]
            bad.update(x for x in names if x not in stdlib)
        self.assertEqual(bad, set(), f"third-party imports: {bad}")

    def test_71_the_log_file_is_created_mode_600(self):
        tmp = Path(tempfile.mkdtemp(prefix="wd-perm-"))
        try:
            log = wd.EventLog(tmp / "w.jsonl")
            log.append("start", {})
            mode = (tmp / "w.jsonl").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600,
                             "a monitoring log can contain service names and "
                             "error text; it must not be world-readable")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_72_fsync_can_be_disabled_without_breaking_the_chain(self):
        tmp = Path(tempfile.mkdtemp(prefix="wd-nofsync-"))
        try:
            log = wd.EventLog(tmp / "w.jsonl", fsync=False)
            for _ in range(10):
                log.append("poll", {"snapshot": {"status": "live"}})
            self.assertTrue(log.verify()["intact"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
