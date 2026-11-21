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

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest.mock import patch
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import run_task as rt  # noqa: E402

_TEST_WRITE_TOKEN = "run-task-test-write-" + "w" * 40
_TEST_READ_ID = "run-task-reader"
_TEST_READ_SECRET = "run-task-test-read-" + "r" * 40
_TEST_READ_HASHES = json.dumps({
    _TEST_READ_ID: hashlib.sha256(_TEST_READ_SECRET.encode()).hexdigest()
})
_TEST_ENV = {
    **os.environ,
    "AI2027_WRITE_TOKEN": _TEST_WRITE_TOKEN,
    "AI2027_READ_TOKEN_HASHES": _TEST_READ_HASHES,
    "AI2027_READ_TOKEN_ID": _TEST_READ_ID,
    "AI2027_READ_TOKEN_SECRET": _TEST_READ_SECRET,
    "AI2027_READ_ONLY": "0",
}


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
            env=_TEST_ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                env=_TEST_ENV, capture_output=True, text=True, timeout=120)
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
        self.assertEqual(res["cleanup"]["status"], "not_applicable")
        self.assertEqual(res["cleanup"]["reason"], "direct_live_agent_mode")
        self.assertFalse(res["cleanup_complete"])
        self.assertTrue(res["cleanup_accepted"])
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

    def test_07_partial_cleanup_cannot_be_a_success(self):
        run_id = "run_1700000000_abcdef01"
        submitted = {
            "run_id": run_id,
            "status": "VERIFIED",
            "evidence": [{"checks": {"independent_check": True}}],
            "cleanup": {"status": "partial", "warnings": ["CLEANUP_PARTIAL"]},
        }
        reread = {"run_id": run_id, "status": "VERIFIED"}
        with tempfile.TemporaryDirectory() as td:
            with patch.object(rt, "wait_for_agent", return_value=True), patch.object(
                rt, "http_json", side_effect=[submitted, reread]
            ):
                result, code = rt.run_task(
                    self.base, "list_dir", {"path": "."}, 60, Path(td), None)
        self.assertEqual(code, 1)
        self.assertEqual(result["cleanup"]["status"], "partial")
        self.assertFalse(result["cleanup_complete"])
        self.assertFalse(result["cleanup_accepted"])
        self.assertEqual(result["verdict"], "TASK-FAILED")
        self.assertIn("cleanup   : partial · CLEANUP_PARTIAL", rt.render(result))

    def test_08_missing_cleanup_report_fails_closed(self):
        run_id = "run_1700000000_abcdef02"
        submitted = {
            "run_id": run_id,
            "status": "VERIFIED",
            "evidence": [{"checks": {"independent_check": True}}],
        }
        reread = {"run_id": run_id, "status": "VERIFIED"}
        with tempfile.TemporaryDirectory() as td:
            with patch.object(rt, "wait_for_agent", return_value=True), patch.object(
                rt, "http_json", side_effect=[submitted, reread]
            ):
                result, code = rt.run_task(
                    self.base, "list_dir", {"path": "."}, 60, Path(td), None)
        self.assertEqual(code, 1)
        self.assertEqual(result["cleanup"]["status"], "unreported")
        self.assertFalse(result["cleanup_complete"])
        self.assertFalse(result["cleanup_accepted"])
        self.assertEqual(result["verdict"], "TASK-FAILED")

    def test_09_not_applicable_requires_direct_mode_marker(self):
        run_id = "run_1700000000_abcdef03"
        submitted = {
            "run_id": run_id,
            "status": "VERIFIED",
            "evidence": [{"checks": {"independent_check": True}}],
            "cleanup": {"status": "not_applicable", "reason": "unknown_mode"},
        }
        reread = {"run_id": run_id, "status": "VERIFIED"}
        with tempfile.TemporaryDirectory() as td:
            with patch.object(rt, "wait_for_agent", return_value=True), patch.object(
                rt, "http_json", side_effect=[submitted, reread]
            ):
                result, code = rt.run_task(
                    self.base, "list_dir", {"path": "."}, 60, Path(td), None)
        self.assertEqual(code, 1)
        self.assertFalse(result["cleanup_complete"])
        self.assertFalse(result["cleanup_accepted"])
        self.assertEqual(result["verdict"], "TASK-FAILED")

    def test_10_wrapped_complete_cleanup_is_accepted_as_complete(self):
        run_id = "run_1700000000_abcdef04"
        submitted = {
            "run_id": run_id,
            "status": "VERIFIED",
            "evidence": [{"checks": {"independent_check": True}}],
            "cleanup": {"status": "complete", "warnings": []},
        }
        reread = {"run_id": run_id, "status": "VERIFIED"}
        with tempfile.TemporaryDirectory() as td:
            with patch.object(rt, "wait_for_agent", return_value=True), patch.object(
                rt, "http_json", side_effect=[submitted, reread]
            ):
                result, code = rt.run_task(
                    self.base, "list_dir", {"path": "."}, 60, Path(td), None)
        self.assertEqual(code, 0)
        self.assertEqual(result["cleanup"]["status"], "complete")
        self.assertTrue(result["cleanup_complete"])
        self.assertTrue(result["cleanup_accepted"])
        self.assertEqual(result["verdict"], "VERIFIED-AND-WITHIN-BUDGET")


if __name__ == "__main__":
    unittest.main(verbosity=2)
