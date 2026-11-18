#!/usr/bin/env python3
"""Tests for the live-egress audit — a claim may fail, but it may not be guessed.

No network: probes and the preflight report are injected.
Run: python3 scripts/test_live_egress.py -v
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import live_egress_audit as lea  # noqa: E402
import network_preflight as npf  # noqa: E402

REGISTER = {"urls": [
    {"url": "https://live.example/", "what": "should be live", "expected": "live"},
    {"url": "https://dead.example/", "what": "known dead", "expected": "dead"},
]}


def fake_probe(status_by_url):
    calls = []

    def _probe(url, timeout=15.0, opener=None):
        calls.append(url)
        value = status_by_url.get(url, "unreachable")
        if value == "unreachable":
            return False, None, 1.0, "connection refused"
        return True, value, 1.0, None
    return _probe, calls


class ClassifyTests(unittest.TestCase):
    def test_01_a_live_claim_that_answers_2xx_holds(self):
        self.assertEqual(lea.classify("live", True, 200, False), lea.OK)
        self.assertEqual(lea.classify("live", True, 204, False), lea.OK)

    def test_02_a_live_claim_that_answers_404_is_a_regression(self):
        self.assertEqual(lea.classify("live", True, 404, False), lea.REGRESSION)
        self.assertEqual(lea.classify("live", True, 503, False), lea.REGRESSION)

    def test_03_a_live_claim_that_cannot_be_reached_is_a_regression_only_if_we_can_see(self):
        self.assertEqual(lea.classify("live", False, None, False), lea.REGRESSION)
        self.assertEqual(lea.classify("live", False, None, True), lea.BLOCKED)

    def test_04_a_dead_claim_that_stays_dead_is_ok(self):
        self.assertEqual(lea.classify("dead", True, 404, False), lea.OK)

    def test_05_a_dead_claim_that_comes_back_is_reported_as_recovered(self):
        self.assertEqual(lea.classify("dead", True, 200, False), lea.RECOVERED)

    def test_06_blindness_blocks_every_verdict(self):
        for expected in ("live", "dead"):
            self.assertEqual(lea.classify(expected, True, 200, True), lea.BLOCKED)


class AuditTests(unittest.TestCase):
    def test_10_mixed_measurements_are_counted_honestly(self):
        probe, calls = fake_probe({"https://live.example/": 200,
                                   "https://dead.example/": 200})
        result = lea.audit(REGISTER, npf.Report(npf.OPEN, ()), probe_fn=probe)
        self.assertEqual(result["counts"], {"ok": 1, "regression": 0, "recovered": 1,
                                            "blocked": 0})
        self.assertEqual(len(calls), 2)

    def test_11_a_regression_is_listed_not_just_counted(self):
        probe, _ = fake_probe({"https://live.example/": 404,
                               "https://dead.example/": 404})
        result = lea.audit(REGISTER, npf.Report(npf.OPEN, ()), probe_fn=probe)
        self.assertEqual(result["counts"]["regression"], 1)
        self.assertEqual(result["regressions"][0]["url"], "https://live.example/")
        self.assertEqual(result["regressions"][0]["code"], 404)

    def test_12_a_blind_runner_probes_nothing_and_claims_nothing(self):
        probe, calls = fake_probe({})
        result = lea.audit(REGISTER, npf.Report(npf.ALLOWLISTED_ONLY, ()), probe_fn=probe)
        self.assertEqual(calls, [], "a blind runner must not pretend to measure")
        self.assertEqual(result["counts"]["blocked"], 2)
        self.assertEqual(result["counts"]["regression"], 0)
        self.assertIn("no general egress", result["results"][0]["error"])

    def test_13_the_result_is_json_serialisable(self):
        probe, _ = fake_probe({"https://live.example/": 200,
                               "https://dead.example/": 404})
        result = lea.audit(REGISTER, npf.Report(npf.OPEN, ()), probe_fn=probe)
        payload = json.loads(json.dumps({k: v for k, v in result.items()
                                         if k != "_rows"}))
        self.assertEqual(payload["egress"]["egress"], npf.OPEN)
        self.assertIn("measured_at", payload)

    def test_14_markdown_says_blocked_rows_were_not_judged(self):
        result = lea.audit(REGISTER, npf.Report(npf.ALLOWLISTED_ONLY, ()),
                           probe_fn=fake_probe({})[0])
        md = lea.to_markdown(result)
        self.assertIn("blocked", md)
        self.assertIn("not judged", md)
        self.assertIn("docs/public-urls.json", md)

    def test_15_markdown_flags_a_regression_loudly(self):
        result = lea.audit(REGISTER, npf.Report(npf.OPEN, ()),
                           probe_fn=fake_probe({"https://live.example/": 404})[0])
        md = lea.to_markdown(result)
        self.assertIn("now false", md)


class RegisterTests(unittest.TestCase):
    def test_20_the_real_register_is_well_formed(self):
        reg = lea.load_register()
        self.assertGreaterEqual(len(reg["urls"]), 7)
        for entry in reg["urls"]:
            self.assertIn(entry["expected"], ("live", "dead"), entry["url"])
            self.assertTrue(entry["url"].startswith("https://"))
            self.assertIn("last_checked", entry)

    def test_21_a_register_without_urls_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.json"
            path.write_text('{"schema": 1}', encoding="utf-8")
            with self.assertRaises(ValueError):
                lea.load_register(path)


class CliTests(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = lea.main(argv)
        return code, buf.getvalue()

    def test_30_exit_is_zero_when_claims_hold(self):
        original = lea.probe
        lea.probe = fake_probe({"https://live.example/": 200,
                                "https://dead.example/": 404})[0]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                reg = Path(tmp) / "r.json"
                reg.write_text(json.dumps(REGISTER), encoding="utf-8")
                code, out = self._run(["--register", str(reg), "--assume-egress", "OPEN",
                                       "--strict-exit"])
            self.assertEqual(code, 0)
            self.assertIn("ok 2", out)
        finally:
            lea.probe = original

    def test_31_exit_is_one_when_a_live_claim_regressed(self):
        original = lea.probe
        lea.probe = fake_probe({"https://live.example/": 500,
                                "https://dead.example/": 404})[0]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                reg = Path(tmp) / "r.json"
                reg.write_text(json.dumps(REGISTER), encoding="utf-8")
                code, out = self._run(["--register", str(reg), "--assume-egress", "OPEN",
                                       "--strict-exit"])
            self.assertEqual(code, 1)
            self.assertIn("regression 1", out)
        finally:
            lea.probe = original

    def test_32_a_blind_run_does_not_fail_the_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Path(tmp) / "r.json"
            reg.write_text(json.dumps(REGISTER), encoding="utf-8")
            code, out = self._run(["--register", str(reg), "--assume-egress", "CLOSED",
                                   "--strict-exit"])
        self.assertEqual(code, 0, "blindness is not a regression")
        self.assertIn("blocked 2", out)

    def test_33_artifacts_are_written_where_asked(self):
        original = lea.probe
        lea.probe = fake_probe({"https://live.example/": 200,
                                "https://dead.example/": 404})[0]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                reg = Path(tmp) / "r.json"
                reg.write_text(json.dumps(REGISTER), encoding="utf-8")
                out_json = Path(tmp) / "out" / "egress.json"
                out_md = Path(tmp) / "out" / "EGRESS.md"
                self._run(["--register", str(reg), "--assume-egress", "OPEN",
                           "--write-json", str(out_json),
                           "--write-markdown", str(out_md)])
                payload = json.loads(out_json.read_text(encoding="utf-8"))
                self.assertEqual(payload["counts"]["ok"], 2)
                self.assertIn("# 🌐 Live egress audit", out_md.read_text(encoding="utf-8"))
        finally:
            lea.probe = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
