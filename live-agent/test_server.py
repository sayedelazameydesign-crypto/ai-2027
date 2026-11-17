#!/usr/bin/env python3
"""Stdlib tests for the Live Agent Experience server (Phase 1)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server  # noqa: E402


class TestRunGoal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        server.RUNS_DIR = Path(self.tmp) / "runs"
        server.SANDBOXES_DIR = Path(self.tmp) / "sandboxes"
        server._fresh_state()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_file_is_verified_with_evidence(self):
        rec = server.run_goal("create_file", {"path": "a/b.txt", "content": "hi"})
        self.assertEqual(rec["status"], "VERIFIED")
        self.assertTrue(rec["evidence"])
        for e in rec["evidence"]:
            self.assertTrue(e["checks"])
            self.assertTrue(all(e["checks"].values()))
        self.assertIn("a/b.txt", rec["sandbox_files"])
        # the file really exists in that run's sandbox
        self.assertTrue(
            (server.SANDBOXES_DIR / rec["run_id"] / "a" / "b.txt").is_file()
        )

    def test_traversal_is_denied_by_kernel_policy(self):
        rec = server.run_goal("create_file",
                              {"path": "../../escape.txt", "content": "x"})
        self.assertEqual(rec["status"], "DENIED")
        self.assertTrue(rec["denial_reasons"])
        # nothing escaped
        self.assertFalse(Path(self.tmp).parent.joinpath("escape.txt").exists())

    def test_unknown_goal_fails_loudly(self):
        with self.assertRaises(ValueError):
            server.run_goal("launch_missiles", {"target": "nowhere"})

    def test_param_limits(self):
        with self.assertRaises(ValueError):
            server.run_goal("create_file",
                            {"path": "x.txt", "content": "y" * 5000})
        with self.assertRaises(ValueError):
            server.run_goal("create_file", "not-a-dict")

    def test_record_persisted_and_readable(self):
        rec = server.run_goal("ensure_dir", {"path": "d1"})
        loaded = server.read_run(rec["run_id"])
        self.assertEqual(loaded["status"], "VERIFIED")

    def test_sandbox_file_serving_is_traversal_safe(self):
        rec = server.run_goal("create_file", {"path": "pub.txt", "content": "42"})
        got = server.sandbox_file(rec["run_id"], "pub.txt")
        self.assertEqual(got[0], b"42")
        self.assertIsNone(server.sandbox_file(rec["run_id"], "../pub.txt"))
        self.assertIsNone(server.sandbox_file(rec["run_id"], "ledger.jsonl"))
        self.assertIsNone(server.sandbox_file("bad id", "pub.txt"))
        self.assertIsNone(server.sandbox_file("run_0_00000000", "pub.txt"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
