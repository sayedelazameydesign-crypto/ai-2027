#!/usr/bin/env python3
"""Tests for the T6 task runner — the budget claim must be falsifiable.

The runner is started against a *real* server process on a free port, exactly
like an outside user would, and the tests assert the four outcomes that matter:

  * a real task verifies, its artifact hash is recomputed locally and matches;
  * the evidence bundle is written and carries the run record;
  * a real task that exceeds the budget exits 3 (the 60 s promise can fail);
  * a DENIED goal is reported as a task failure, never as success;
  * an unreachable agent exits 2 instead of pretending.

Run: python3 scripts/test_run_task.py -v
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import run_task as rt  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class RealTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "live-agent" / "server.py"),
             "--port", str(cls.port), "--host", "127.0.0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(cls.base + "/api/health", timeout=1) as r:
                    if json.loads(r.read()).get("ok"):
                        return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("live-agent did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    def run_cli(self, *args: str) -> tuple[int, dict, str | None]:
        with tempfile.TemporaryDirectory() as td:
            proc = subprocess.run(
                [sys.executable, str(HERE / "run_task.py"),
                 "--live-agent", self.base, "--evidence-dir", td, "--json", *args],
                capture_output=True, text=True, timeout=120)
            payload = json.loads(proc.stdout.strip().splitlines()[-1])  # one JSON line
            bundles = sorted(Path(td).glob("*.json"))
            bundle_text = bundles[-1].read_text() if bundles else None
            return proc.returncode, payload, bundle_text

    def test_01_a_real_task_verifies_inside_the_budget(self):
        code, res, bundle = self.run_cli("--goal", "create_file",
                                         "--param", "path=demo/t6.txt",
                                         "--param", "content=measured not promised")
        self.assertEqual(code, 0, res)
        self.assertEqual(res["status"], "VERIFIED")
        self.assertTrue(res["verifier_checks_passed"])
        self.assertTrue(res["claims"]["within_budget"])
        self.assertLess(res["claims"]["elapsed_ms"], 60_000)
        self.assertTrue(res["artifact"]["hash_verified"])
        self.assertIn("/api/run/", res["evidence_link"])
        self.assertIsNotNone(bundle)

    def test_02_the_bundle_contains_the_evidence_record_itself(self):
        code, res, bundle_text = self.run_cli("--goal", "create_file",
                                              "--param", "path=demo/t6b.txt",
                                              "--param", "content=bundle check")
        self.assertEqual(code, 0)
        self.assertIsNotNone(bundle_text)
        self.assertIn("task_", res["bundle"])
        payload = json.loads(bundle_text)
        self.assertEqual(payload["record"]["run_id"], res["run_id"])
        self.assertTrue(payload["record"]["evidence"])

    def test_03_exceeding_the_budget_is_reported_as_a_failure(self):
        code, res, _ = self.run_cli("--goal", "create_file",
                                    "--param", "path=demo/t6c.txt",
                                    "--param", "content=tiny budget",
                                    "--budget", "0.000001")
        self.assertEqual(code, 3, res)
        self.assertEqual(res["verdict"], "OVER-BUDGET")
        self.assertFalse(res["claims"]["within_budget"])

    def test_04_a_denied_goal_is_a_failed_task_not_a_success(self):
        code, res, _ = self.run_cli("--goal", "create_file",
                                    "--param", "path=../../etc/pwned",
                                    "--param", "content=x")
        self.assertEqual(code, 1, res)
        self.assertEqual(res["status"], "DENIED")
        self.assertFalse(res["verified"])

    def test_05_an_unreachable_agent_exits_2_and_says_so(self):
        port = free_port()
        proc = subprocess.run(
            [sys.executable, str(HERE / "run_task.py"), "--json", "--wait", "2",
             "--live-agent", f"http://127.0.0.1:{port}"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["status"], "UNREACHABLE")

    def test_06_the_budget_default_is_the_published_sixty_seconds(self):
        code, res, _ = self.run_cli("--goal", "create_file",
                                    "--param", "path=demo/t6d.txt",
                                    "--param", "content=default budget")
        self.assertEqual(code, 0)
        self.assertEqual(res["claims"]["budget_ms"], 60_000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
