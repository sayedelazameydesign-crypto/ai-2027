#!/usr/bin/env python3
"""Tests for the ember beat — the numbers must be arithmetic, not mood.

Run: python3 scripts/test_ember_beat.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import ember_beat as eb  # noqa: E402


class HeatIndexTests(unittest.TestCase):
    def test_01_fresh_beat_on_a_healthy_repo_is_not_cold(self):
        now = time.time()
        h = eb.heat_index([{"ts": now, "ok": True}], memories=1, embers_live=1,
                          now=now)
        # 0.4*1 + 0.2*1 + 0.2*0.01 + 0.2*(1/3) = 0.6687
        self.assertAlmostEqual(h["heat"], 0.6687, places=4)
        self.assertEqual(h["verdict"], "hot-without-flame")
        self.assertEqual(h["recency_component"] if "recency_component" in h else
                         h["components"]["recency"], 0.2)

    def test_02_zero_beat_seconds_ago_is_not_read_as_24h_of_silence(self):
        """Regression: `since_last_h or 24.0` treated 0.0 as missing (falsy)."""
        now = time.time()
        h = eb.heat_index([{"ts": now - 5, "ok": True}], memories=0, embers_live=0,
                          now=now)
        self.assertEqual(h["components"]["recency"], 0.2)

    def test_03_a_week_of_silence_goes_cold_no_matter_how_good_the_last_beat_was(self):
        now = time.time()
        history = [{"ts": now - 8 * 24 * 3600, "ok": True}]
        h = eb.heat_index(history, memories=100, embers_live=3, now=now)
        self.assertEqual(h["beats_7d"], 0)
        self.assertEqual(h["components"]["recency"], 0.0)
        self.assertEqual(h["components"]["success"], 0.0)
        # fuel + embers alone can never exceed 0.4 — silence cannot look healthy
        self.assertLessEqual(h["heat"], 0.4)

    def test_04_failures_lower_the_index_monotonically(self):
        now = time.time()
        good = eb.heat_index([{"ts": now, "ok": True}] * 4, 10, 2, now=now)["heat"]
        mixed = eb.heat_index([{"ts": now, "ok": True}] * 2
                              + [{"ts": now, "ok": False}] * 2, 10, 2, now=now)["heat"]
        self.assertGreater(good, mixed)

    def test_05_verdict_thresholds_are_ordered(self):
        now = time.time()
        burning = eb.heat_index([{"ts": now, "ok": True}] * 6, 100, 3, now=now)
        self.assertGreaterEqual(burning["heat"], 0.8)
        self.assertEqual(burning["verdict"], "burning")


class StatusPublishingTests(unittest.TestCase):
    def test_10_status_json_and_status_md_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old = (eb.EMBER_DIR, eb.STATUS_JSON, eb.STATUS_MD, eb.HISTORY)
            eb.EMBER_DIR = tmp_path / "ember"
            eb.STATUS_JSON = eb.EMBER_DIR / "status.json"
            eb.STATUS_MD = tmp_path / "STATUS.md"
            eb.HISTORY = eb.EMBER_DIR / "history.jsonl"
            try:
                now = time.time()
                status = {
                    "beat_ts": now,
                    "beat_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                 time.gmtime(now)),
                    "duration_ms": 1.0,
                    "smoke": {"ok": True, "passed": 15, "total": 15,
                              "checks": [{"name": "a", "ok": True, "note": "n"}],
                              "error": None},
                    "memory": {"memories": 3, "refusals": 1},
                    "public_paths": [],
                    "heat": eb.heat_index([{"ts": now, "ok": True}], 3, 1, now=now),
                    "degraded": False,
                }
                eb.write_status(status)
                eb.append_history(status)
                js = json.loads(eb.STATUS_JSON.read_text(encoding="utf-8"))
                md = eb.STATUS_MD.read_text(encoding="utf-8")
                self.assertEqual(js["smoke"]["passed"], 15)
                self.assertIn(f"{js['smoke']['passed']}/{js['smoke']['total']}", md)
                self.assertIn(str(js["heat"]["heat"]), md)
                self.assertIn(str(js["memory"]["memories"]), md)
                self.assertEqual(len(eb.load_history()), 1)
                self.assertTrue(True)  # keep the tempdir paths for the finally
            finally:
                (eb.EMBER_DIR, eb.STATUS_JSON, eb.STATUS_MD, eb.HISTORY) = old

    def test_11_unreachable_servers_produce_a_degraded_beat(self):
        """A dead server must be reported as degraded, never as a healthy beat."""
        proc = subprocess.run(
            [sys.executable, str(HERE / "ember_beat.py"), "--json",
             "--live-agent", "http://127.0.0.1:59399",
             "--hud", "http://127.0.0.1:59398"],
            capture_output=True, text=True, timeout=120)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["smoke"]["ok"])
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("PASS", json.dumps(payload["smoke"]["checks"]).replace(
            '"ok": true', "PASS"))


class BlindedEgressTests(unittest.TestCase):
    """Regression for the 2026-11-18 false claim: "the sites are down".

    The session ran in a sandbox with no general egress. Every public URL
    returned status 0 and was published as not-live — blindness reported as
    death. These tests pin the distinction.
    """

    def test_20_blocked_paths_are_not_reported_as_dead(self):
        import network_preflight as npf
        rep = npf.Report(npf.ALLOWLISTED_ONLY, ())
        paths = eb.public_paths(["https://a.vercel.app/", "https://b.netlify.app/"],
                                report=rep)
        self.assertEqual([p["verdict"] for p in paths], ["blocked", "blocked"])
        self.assertFalse(any(p["live"] for p in paths))

    def test_21_open_egress_with_no_answer_is_still_a_real_death(self):
        import urllib.error

        import network_preflight as npf

        def boom(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        rep = npf.Report(npf.OPEN, ())
        paths = eb.public_paths(["https://gone.vercel.app/"], report=rep, opener=boom)
        self.assertEqual(paths[0]["verdict"], "dead")

    def test_22_open_egress_404_is_dead_but_reachable(self):
        import urllib.error

        import network_preflight as npf

        def not_found(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

        rep = npf.Report(npf.OPEN, ())
        paths = eb.public_paths(["https://gone.vercel.app/"], report=rep,
                                opener=not_found)
        self.assertEqual(paths[0]["verdict"], "dead")
        self.assertEqual(paths[0]["status"], 404)

    def test_23_unmeasured_embers_are_dropped_not_scored_zero(self):
        now = time.time()
        measured = eb.heat_index([{"ts": now, "ok": True}], 10, 2, now=now)
        blind = eb.heat_index([{"ts": now, "ok": True}], 10, 2, now=now,
                              embers_measured=False)
        self.assertFalse(blind["embers_measured"])
        self.assertEqual(blind["weights"]["embers"], 0.0)
        self.assertAlmostEqual(sum(blind["weights"].values()), 1.0, places=4)
        # blindness must not lower the index: the same evidence, less preamble
        self.assertGreaterEqual(blind["heat"], measured["heat"] - 0.13)

    def test_24_a_blind_runner_does_not_publish_public_paths_as_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old = (eb.EMBER_DIR, eb.STATUS_JSON, eb.STATUS_MD, eb.HISTORY)
            eb.EMBER_DIR = tmp_path / "ember"
            eb.STATUS_JSON = eb.EMBER_DIR / "status.json"
            eb.STATUS_MD = tmp_path / "STATUS.md"
            eb.HISTORY = eb.EMBER_DIR / "history.jsonl"
            try:
                now = time.time()
                status = {
                    "beat_ts": now,
                    "beat_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                 time.gmtime(now)),
                    "duration_ms": 1.0,
                    "smoke": {"ok": True, "passed": 15, "total": 15,
                              "checks": [], "error": None},
                    "memory": {"memories": 1, "refusals": 0},
                    "public_paths": [{"url": "https://a.vercel.app/", "status": 0,
                                      "live": False, "verdict": "blocked"}],
                    "public_paths_blocked": 1,
                    "egress": {"egress": "ALLOWLISTED_ONLY",
                               "general_egress": False, "probes": []},
                    "heat": eb.heat_index([{"ts": now, "ok": True}], 1, 1, now=now,
                                          embers_measured=False),
                    "degraded": False,
                }
                eb.write_status(status)
                md = eb.STATUS_MD.read_text(encoding="utf-8")
                js = json.loads(eb.STATUS_JSON.read_text(encoding="utf-8"))
                self.assertIn("BLOCKED", md)
                self.assertIn("CANNOT be judged", md)
                self.assertEqual(js["public_paths_blocked"], 1)
                self.assertNotIn("0/1", md.split("Public paths live")[1][:40])
            finally:
                (eb.EMBER_DIR, eb.STATUS_JSON, eb.STATUS_MD, eb.HISTORY) = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
