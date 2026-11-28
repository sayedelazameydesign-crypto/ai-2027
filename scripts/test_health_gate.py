#!/usr/bin/env python3
"""Tests for the health gate — a gate that cannot fail is decoration.

Every test here is a claim the gate makes in public, turned into arithmetic:

  * a healthy beat passes and exits 0;
  * a beat that *looks* fine but lost checks is caught (the whole point);
  * transient signals ask for a retry (exit 2) instead of paging a human;
  * the retry is not infinite — attempt 2 escalates to an issue (exit 1);
  * three degraded beats in a row escalate to stage 3, and the alert is never
    reported as "sent" when no webhook exists.

Run: python3 scripts/test_health_gate.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import health_gate as hg  # noqa: E402

NOW = 1_800_000_000.0


def make_status(passed: int = 17, total: int = 17, *, memories: int = 3,
                live_paths: int = 1, n_paths: int = 1, heat: float = 0.8,
                checks: int | None = None, beat_ts: float = NOW) -> dict:
    rows = [{"name": f"check_{i}", "ok": i < passed} for i in range(
        total if checks is None else checks)]
    return {
        "beat_ts": beat_ts,
        "beat_at_iso": "2027-01-15T00:00:00Z",
        "heat": {"heat": heat, "verdict": "burning"},
        "smoke": {"ok": passed == total, "passed": passed, "total": total,
                  "checks": rows, "error": None},
        "memory": {"memories": memories, "refusals": 0},
        "public_paths": [{"url": f"https://example.test/{i}",
                          "status": 200 if i < live_paths else 0,
                          "live": i < live_paths} for i in range(n_paths)],
        "degraded": passed != total,
    }


def make_history(n: int = 2, *, passed: int = 17, total: int = 17,
                 heat: float = 0.8, spacing_h: float = 6.0) -> list[dict]:
    return [{"ts": NOW - spacing_h * 3600 * (n - i), "ok": passed == total,
             "passed": passed, "total": total, "memories": 1, "heat": heat}
            for i in range(n)]


class ContractTests(unittest.TestCase):
    def test_01_healthy_beat_passes_with_a_fixed_check_list(self):
        self.assertEqual(hg.DEFAULT_TOTAL, 17)
        res = hg.evaluate(make_status(), make_history(), now=NOW)
        self.assertEqual(tuple(c["id"] for c in res["checks"]), hg.CHECK_IDS)
        self.assertEqual(res["verdict"], "healthy")
        self.assertEqual(res["exit_code"], 0)
        self.assertTrue(res["healthy"])
        self.assertEqual(res["failures"], [])

    def test_02_a_beat_that_lost_checks_is_caught_even_though_it_says_3_of_3(self):
        # passed == total, so every printed field looks perfect — but the
        # historical bar is 17 and this beat only ever measured 3.
        res = hg.evaluate(make_status(passed=3, total=3), make_history(), now=NOW)
        self.assertEqual(res["verdict"], "degraded")
        self.assertIn("smoke_complete", res["failures"])
        self.assertEqual(res["exit_code"], 1)

    def test_03_a_real_failed_check_degrades(self):
        res = hg.evaluate(make_status(passed=16, total=17), make_history(), now=NOW)
        self.assertEqual(res["verdict"], "degraded")
        self.assertIn("smoke_complete", res["failures"])
        self.assertIn("smoke_no_regression", res["failures"])

    def test_04_counted_check_without_evidence_is_a_failure(self):
        res = hg.evaluate(make_status(checks=14), make_history(), now=NOW)
        self.assertFalse(next(c for c in res["checks"] if c["id"] == "evidence_complete")["ok"])
        self.assertEqual(res["stage"], 2)


class TransientTests(unittest.TestCase):
    def test_05_unreachable_memory_asks_for_a_retry_not_an_issue(self):
        res = hg.evaluate(make_status(memories=-1), make_history(), now=NOW)
        self.assertEqual(res["verdict"], "retryable")
        self.assertEqual(res["exit_code"], 2)
        self.assertIn("memory_reachable", res["failures"])

    def test_06_no_live_public_path_asks_for_a_retry(self):
        res = hg.evaluate(make_status(n_paths=2, live_paths=0), make_history(), now=NOW)
        self.assertEqual(res["exit_code"], 2)
        self.assertIn("public_path", res["failures"])

    def test_07_the_retry_is_not_infinite_second_attempt_becomes_an_issue(self):
        status = make_status(memories=-1)
        first = hg.decide_stage(hg.evaluate(status, make_history(), now=NOW, attempt=1), [], 1)
        second = hg.decide_stage(hg.evaluate(status, make_history(), now=NOW, attempt=2), [], 2)
        self.assertEqual((first["stage"], first["exit_code"]), (1, 2))
        self.assertEqual((second["stage"], second["exit_code"]), (2, 1))

    def test_08_first_ever_beat_cannot_claim_healthy_freshness(self):
        res = hg.evaluate(make_status(), [], now=NOW)
        freshness = next(c for c in res["checks"] if c["id"] == "beat_freshness")
        self.assertIn("first beat", freshness["detail"])
        self.assertEqual(res["exit_code"], 2)


class FreshnessTests(unittest.TestCase):
    def test_09_a_late_beat_that_passes_everything_is_a_visible_warning(self):
        # Each beat boots its own servers in the runner: a 14h gap proves GitHub
        # ran the schedule late, not that anything was down. Visible, not paged.
        res = hg.evaluate(make_status(), make_history(spacing_h=14.0), now=NOW)
        freshness = next(c for c in res["checks"] if c["id"] == "beat_freshness")
        self.assertFalse(freshness["ok"])
        self.assertEqual(freshness["severity"], hg.WARNING)
        self.assertIn("scheduler late", freshness["detail"])
        self.assertIn("beat_freshness", res["failures"])
        self.assertEqual((res["verdict"], res["exit_code"]), ("healthy-with-warnings", 0))
        self.assertFalse(res["healthy"], "a late beat must never read as a clean healthy")

    def test_09b_a_full_day_of_silence_means_the_schedule_stopped_and_fails(self):
        res = hg.evaluate(make_status(), make_history(spacing_h=25.0), now=NOW)
        freshness = next(c for c in res["checks"] if c["id"] == "beat_freshness")
        self.assertEqual(freshness["severity"], hg.CRITICAL)
        self.assertIn("schedule itself has stopped", freshness["detail"])
        self.assertEqual((res["verdict"], res["exit_code"]), ("degraded", 1))

    def test_09c_a_late_beat_does_not_hide_a_real_failure(self):
        res = hg.evaluate(make_status(passed=16), make_history(spacing_h=14.0), now=NOW)
        self.assertEqual(res["exit_code"], 1)
        self.assertIn("smoke_complete", res["failures"])

    def test_09d_the_measured_week_no_longer_escalates(self):
        # Real gaps from the heartbeat branch, 2026-11-23..27: every beat was
        # 17/17 and every one was degraded at stage 3 under the old rule.
        gaps = [8.0, 13.8, 12.5, 12.5, 10.1, 10.7, 8.3, 13.3, 8.8, 10.6, 11.1]
        for gap in gaps:
            with self.subTest(gap_h=gap):
                res = hg.evaluate(make_status(), make_history(spacing_h=gap), now=NOW)
                self.assertEqual(res["exit_code"], 0)
                self.assertEqual(res["stage"], 0)

    def test_09e_the_dead_schedule_threshold_is_configurable(self):
        res = hg.evaluate(make_status(), make_history(spacing_h=14.0), now=NOW,
                          scheduler_dead_h=12.0)
        self.assertEqual(res["exit_code"], 1)

    def test_10_six_hour_cadence_inside_tolerance_is_fresh(self):
        res = hg.evaluate(make_status(), make_history(spacing_h=7.5), now=NOW)
        self.assertNotIn("beat_freshness", res["failures"])

    def test_11_history_reset_is_a_warning_not_a_page(self):
        future = [{"ts": NOW + 3600, "ok": True, "passed": 17, "total": 17, "heat": 0.8}]
        res = hg.evaluate(make_status(), future, now=NOW)
        warning = next(c for c in res["checks"] if c["id"] == "history_monotonic")
        self.assertFalse(warning["ok"])
        self.assertEqual(warning["severity"], hg.WARNING)
        self.assertEqual(res["verdict"], "healthy-with-warnings")
        self.assertEqual(res["exit_code"], 0)


class EscalationTests(unittest.TestCase):
    def test_12_three_consecutive_degraded_beats_escalate_to_stage_3(self):
        # two degraded runs already logged + this (third) one, after the retry
        log = [{"healthy": True}, {"healthy": False}, {"healthy": False}]
        res = hg.decide_stage(hg.evaluate(make_status(passed=14), make_history(), now=NOW),
                              log, 2)
        self.assertEqual(res["streak"], 3)
        self.assertEqual(res["stage"], 3)
        self.assertTrue(res["escalate"])

    def test_13_two_degraded_beats_are_stage_2_without_escalation(self):
        log = [{"healthy": True}, {"healthy": False}]
        res = hg.decide_stage(hg.evaluate(make_status(passed=14), make_history(), now=NOW),
                              log, 2)
        self.assertEqual((res["stage"], res["escalate"]), (2, False))
        self.assertEqual(res["streak"], 2)

    def test_13b_a_single_degraded_run_retries_before_it_is_escalated(self):
        log = [{"healthy": True}, {"healthy": False}, {"healthy": False}]
        res = hg.decide_stage(hg.evaluate(make_status(passed=14), make_history(), now=NOW),
                              log, 1)
        self.assertEqual((res["stage"], res["exit_code"], res["escalate"]), (1, 2, False))
        self.assertEqual(res["streak"], 3)   # visible, but escalation waits for the retry

    def test_14_a_recovery_resets_the_streak(self):
        log = [{"healthy": False}, {"healthy": False}, {"healthy": True}]
        self.assertEqual(hg.consecutive_degraded(log), 0)
        res = hg.decide_stage(hg.evaluate(make_status(), make_history(), now=NOW), log, 1)
        self.assertEqual(res["stage"], 0)

    def test_14b_warnings_never_accumulate_into_an_escalation(self):
        # three runs that were "healthy with warnings" are NOT three degradations
        log = [{"healthy": False, "degraded": False} for _ in range(3)]
        self.assertEqual(hg.consecutive_degraded(log), 0)
        res = hg.decide_stage(hg.evaluate(make_status(n_paths=3, live_paths=1),
                                          make_history(), now=NOW), log, 2)
        self.assertEqual((res["stage"], res["escalate"]), (0, False))

    def test_15_no_webhook_means_the_alert_is_reported_as_not_configured(self):
        self.assertEqual(hg.send_alert({"stage": 3, "failures": ["x"]}, None),
                         "not-configured")


class OfflineTests(unittest.TestCase):
    def test_21_offline_says_not_measured_instead_of_paging(self):
        status = make_status(n_paths=2, live_paths=0)
        res = hg.evaluate(status, make_history(), now=NOW, measure_paths=False)
        details = {c["id"]: c for c in res["checks"]}
        self.assertFalse(details["public_path"]["ok"])
        self.assertEqual(details["public_path"]["severity"], hg.WARNING)
        self.assertIn("not measured", details["public_path"]["detail"])
        # a warning is never a clean "healthy" ...
        self.assertEqual(res["verdict"], "healthy-with-warnings")
        # ... and it never pages anyone for a local run without network
        self.assertEqual(res["exit_code"], 0)


class InvariantTests(unittest.TestCase):
    def test_16_losing_one_of_three_paths_costs_at_most_0_07_heat(self):
        res = hg.evaluate(make_status(n_paths=3, live_paths=2), make_history(), now=NOW)
        inv = next(c for c in res["checks"] if c["id"] == "path_independence")
        self.assertTrue(inv["ok"])
        self.assertLessEqual(0.2 / 3, hg.MAX_PATH_IMPACT)
        # the T4 acceptance criterion is only meaningful if the formula obeys it
        self.assertAlmostEqual(0.2 / 3, 0.0667, places=4)

    def test_17_two_of_three_paths_dead_is_only_a_warning(self):
        res = hg.evaluate(make_status(n_paths=3, live_paths=1), make_history(), now=NOW)
        self.assertIn("public_paths_all", res["failures"])
        self.assertEqual(res["exit_code"], 0)

    def test_18_a_collapsing_heat_index_is_flagged(self):
        hist = make_history(heat=0.9)
        res = hg.evaluate(make_status(heat=0.3), hist, now=NOW)
        self.assertIn("heat_not_dropped", res["failures"])
        self.assertEqual(res["verdict"], "healthy-with-warnings")


class CliTests(unittest.TestCase):
    def test_19b_cli_scheduler_dead_hours_flag_reaches_the_gate(self):
        # The CLI judges against the real clock, so build a 14h-old history now.
        import time as _time
        now = _time.time()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "status.json").write_text(json.dumps(make_status(beat_ts=now)))
            (td / "history.jsonl").write_text("\n".join(json.dumps(
                {"ts": now - h * 3600, "ok": True, "passed": 17, "total": 17,
                 "memories": 1, "heat": 0.8}) for h in (28.0, 14.0)) + "\n")
            base = [sys.executable, str(HERE / "health_gate.py"),
                    "--status", str(td / "status.json"), "--history", str(td / "history.jsonl"),
                    "--gate-log", str(td / "gate.jsonl"), "--json"]
            late = subprocess.run(base, capture_output=True, text=True)
            self.assertEqual(late.returncode, 0, late.stdout[-400:])
            self.assertEqual(json.loads(late.stdout.strip().splitlines()[-1])["verdict"],
                             "healthy-with-warnings")
            # attempt 2: on attempt 1 every critical first asks for a retry (test_07)
            dead = subprocess.run(base + ["--scheduler-dead-hours", "12", "--attempt", "2"],
                                  capture_output=True, text=True)
            self.assertEqual(dead.returncode, 1, dead.stdout[-400:])

    def test_19_cli_writes_the_gate_log_and_returns_the_stage_exit_code(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "status.json").write_text(json.dumps(make_status(passed=14)))
            (td / "history.jsonl").write_text(
                "\n".join(json.dumps(b) for b in make_history()) + "\n")
            gate_log = td / "gate.jsonl"
            proc = subprocess.run(
                [sys.executable, str(HERE / "health_gate.py"),
                 "--status", str(td / "status.json"),
                 "--history", str(td / "history.jsonl"),
                 "--gate-log", str(gate_log), "--json", "--write",
                 "--attempt", "2"],   # attempt 1 retries, attempt 2 pages
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertEqual(payload["verdict"], "degraded")
            self.assertEqual(payload["stage"], 2)
            self.assertTrue(gate_log.is_file())
            written = json.loads(gate_log.read_text().strip())
            self.assertFalse(written["healthy"])
            self.assertIn("smoke_complete", written["failures"])

    def test_19b_committing_a_beat_is_idempotent_so_a_retry_cannot_inflate_history(self):
        with tempfile.TemporaryDirectory() as td:
            hist = Path(td) / "history.jsonl"
            status = make_status()
            self.assertTrue(hg.append_beat(status, hist))
            self.assertFalse(hg.append_beat(status, hist))     # same beat_ts
            self.assertFalse(hg.append_beat({"smoke": {}}, hist))  # no beat_ts
            rows = [json.loads(line) for line in hist.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["passed"], 17)

    def test_20_self_test_runs_the_gate_on_synthetic_evidence(self):
        proc = subprocess.run([sys.executable, str(HERE / "health_gate.py"), "--self-test"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PASS", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
